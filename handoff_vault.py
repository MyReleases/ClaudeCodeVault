#!/usr/bin/env python3
"""handoff-vault: back up, move and hand off Claude Code projects without losing chat history.

Claude Code keeps every conversation in ~/.claude/projects/<encoded-folder-path>/<session>.jsonl.
The history is tied to the folder path, so moving a project hides its chats, and there is no
built-in backup or export. This single-file tool (Python 3.8+, no dependencies) fixes that:

  status   list projects that have history, their size, last activity and orphans
  backup   copy all history into a folder with readable project names (never deletes)
  move     move a project folder and carry its chat history along (`claude --resume` keeps working)
  export   pack a project + its chats into one zip (scans for secrets first)
  import   unpack such a zip somewhere else and relink the chats to the new path
  scan     look for API keys, tokens and private keys in chat history
  stats    activity per project over the last N days
  init     add CLAUDE.md, HANDOFF.md and a /handoff command to a project

Nothing here deletes your data: history is copied, originals stay where they were.
"""

import argparse
import collections
import datetime as dt
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

__version__ = "0.1.0"

# Folders that are rebuilt from source and only bloat an export.
DEFAULT_EXCLUDES = [
    "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".next", ".nuxt", ".turbo", ".cache", ".parcel-cache", ".gradle", "dist", "build", "out", "target",
]

# Patterns that look like credentials. Tuned to avoid noise: each needs a recognisable prefix.
SECRET_PATTERNS = [
    ("Anthropic API key", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("OpenAI API key", r"sk-(?:proj-)?[A-Za-z0-9_\-]{32,}"),
    ("GitHub token", r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"),
    ("AWS access key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    ("Google API key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ("Slack token", r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    ("Stripe key", r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}"),
    ("Telegram bot token", r"\b\d{8,10}:AA[A-Za-z0-9_\-]{33}\b"),
    ("Private key block", r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
]
SECRET_RES = [(name, re.compile(rx)) for name, rx in SECRET_PATTERNS]

IS_WINDOWS = os.name == "nt"


# --------------------------------------------------------------------------- paths & encoding

def claude_dir(args=None):
    """The Claude Code config folder: --claude-dir, then $CLAUDE_CONFIG_DIR, then ~/.claude."""
    d = getattr(args, "claude_dir", None) or os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude")
    return os.path.abspath(d)


def projects_dir(args=None):
    return os.path.join(claude_dir(args), "projects")


def encode_path(path):
    """Folder name Claude Code uses for a project: every non-alphanumeric character becomes '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def norm(path):
    """Comparable form of a path (case-insensitive and slash-agnostic on Windows)."""
    return os.path.normcase(os.path.normpath(path))


def abspath(path):
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))


def remap_cwd(cwd, old, new):
    """If cwd is old or inside it, return the same location under new; otherwise None."""
    n_cwd, n_old = norm(cwd), norm(old)
    if n_cwd == n_old:
        return new
    if n_cwd.startswith(n_old.rstrip("\\/") + os.sep):
        rest = os.path.normpath(cwd)[len(os.path.normpath(old).rstrip("\\/")) + 1:]
        return os.path.join(new, rest)
    return None


def lp(path):
    """Long-path form on Windows, so deep history folders past 260 characters still copy."""
    if IS_WINDOWS:
        path = os.path.abspath(path)
        if not path.startswith("\\\\?\\"):
            return "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path
    return path


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0


# --------------------------------------------------------------------------- reading history

def iter_lines(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            yield line


def cwds_of(history_dir, limit_files=None):
    """Counter of `cwd` values recorded in the transcripts of one history folder."""
    counts = collections.Counter()
    files = sorted(f for f in os.listdir(history_dir) if f.endswith(".jsonl"))
    for name in files[:limit_files]:
        for line in iter_lines(os.path.join(history_dir, name)):
            if '"cwd"' not in line:
                continue
            try:
                cwd = json.loads(line).get("cwd")
            except ValueError:
                continue
            if cwd:
                counts[cwd] += 1
    return counts


def project_path_of(history_dir):
    """The project folder a history folder belongs to.

    A forked chat can start with lines from another folder, so prefer the cwd whose encoding
    matches the history folder's own name; fall back to the most frequent cwd.
    """
    counts = cwds_of(history_dir)
    name = os.path.basename(history_dir)
    for cwd in counts:
        if encode_path(cwd) == name:
            return cwd
    return counts.most_common(1)[0][0] if counts else None


class History:
    """One folder under ~/.claude/projects."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self._project = False

    @property
    def project(self):
        if self._project is False:
            self._project = project_path_of(self.path)
        return self._project

    @property
    def sessions(self):
        return sorted(f for f in os.listdir(self.path) if f.endswith(".jsonl"))

    def size(self):
        total = 0
        for root, _, files in os.walk(self.path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total

    def last_activity(self):
        times = [os.path.getmtime(os.path.join(self.path, s)) for s in self.sessions]
        return max(times) if times else os.path.getmtime(self.path)


def all_histories(args):
    base = projects_dir(args)
    if not os.path.isdir(base):
        return []
    return [History(os.path.join(base, d)) for d in sorted(os.listdir(base))
            if os.path.isdir(os.path.join(base, d))]


def histories_for(args, project):
    """History folders whose chats were started in `project` or one of its subfolders."""
    found = []
    for h in all_histories(args):
        p = h.project
        if p and remap_cwd(p, project, project) is not None:
            found.append(h)
    return found


def readable_name(project_path):
    """Folder name for backups: the project's own name plus a short hash so twins never collide."""
    leaf = os.path.basename(os.path.normpath(project_path)) or "root"
    leaf = re.sub(r"[^\w.\- ]", "_", leaf).strip() or "project"
    digest = hashlib.sha1(norm(project_path).encode("utf-8")).hexdigest()[:6]
    return f"{leaf}__{digest}"


# --------------------------------------------------------------------------- copying history

def copy_tree_incremental(src, dst, errors=None):
    """Copy files that are new or changed. Returns (new, updated). Never deletes anything.

    A file that cannot be copied (locked, vanished) is recorded in `errors` and skipped.
    """
    new = updated = 0
    for root, _, files in os.walk(lp(src)):
        rel = os.path.relpath(root, lp(src))
        out_root = os.path.join(lp(dst), rel) if rel != "." else lp(dst)
        os.makedirs(out_root, exist_ok=True)
        for f in files:
            s, d = os.path.join(root, f), os.path.join(out_root, f)
            try:
                if not os.path.exists(d):
                    shutil.copy2(s, d)
                    new += 1
                else:
                    ss, ds = os.stat(s), os.stat(d)
                    if ss.st_size != ds.st_size or ss.st_mtime > ds.st_mtime + 1:
                        shutil.copy2(s, d)
                        updated += 1
            except OSError as e:
                if errors is None:
                    raise
                errors.append(f"{s}: {e.strerror or e}")
    return new, updated


def rewrite_transcript(src, dst, old, new):
    """Copy one .jsonl, pointing every `cwd` inside `old` at the same place under `new`."""
    changed = 0
    tmp = dst + ".hv-tmp"
    with open(src, encoding="utf-8", errors="replace") as fin, \
            open(tmp, "w", encoding="utf-8", newline="") as fout:
        for line in fin:
            if '"cwd"' in line:
                try:
                    obj = json.loads(line)
                except ValueError:
                    obj = None
                if isinstance(obj, dict) and isinstance(obj.get("cwd"), str):
                    moved = remap_cwd(obj["cwd"], old, new)
                    if moved is not None and moved != obj["cwd"]:
                        obj["cwd"] = moved
                        line = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
                        changed += 1
            fout.write(line)
    os.replace(tmp, dst)
    shutil.copystat(src, dst)
    return changed


def relink_history(history_dir, target_dir, old, new, force=False):
    """Copy a history folder to target_dir with cwd rewritten. Returns (sessions, skipped)."""
    os.makedirs(target_dir, exist_ok=True)
    copied = skipped = 0
    for entry in os.listdir(history_dir):
        s, d = os.path.join(history_dir, entry), os.path.join(target_dir, entry)
        if os.path.exists(d) and not force:
            skipped += entry.endswith(".jsonl")
            continue
        if entry.endswith(".jsonl"):
            rewrite_transcript(s, d, old, new)
            copied += 1
        elif os.path.isdir(s):
            shutil.copytree(lp(s), lp(d), dirs_exist_ok=True)
        else:
            shutil.copy2(lp(s), lp(d))
    return copied, skipped


# --------------------------------------------------------------------------- secrets

def scan_text_file(path):
    hits = []
    for lineno, line in enumerate(iter_lines(path), 1):
        for name, rx in SECRET_RES:
            for m in rx.finditer(line):
                v = m.group(0)
                hits.append((name, lineno, v[:6] + "…" + v[-2:] if len(v) > 12 else "…"))
    return hits


def redact_file(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    for name, rx in SECRET_RES:
        text = rx.sub(f"[REDACTED {name}]", text)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


# --------------------------------------------------------------------------- commands

def cmd_status(args):
    by_project = {}
    for h in all_histories(args):
        if not h.sessions:
            continue
        p = h.project or "(unknown)"
        last = dt.datetime.fromtimestamp(h.last_activity()).strftime("%Y-%m-%d %H:%M")
        r = by_project.setdefault(norm(p), {"project": p, "exists": bool(h.project and os.path.isdir(p)),
                                            "sessions": 0, "size": 0, "last": last, "history": []})
        r["sessions"] += len(h.sessions)
        r["size"] += h.size()
        r["last"] = max(r["last"], last)
        r["history"].append(h.name)
    rows = sorted(by_project.values(), key=lambda r: r["last"], reverse=True)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    if not rows:
        print(f"No chat history found in {projects_dir(args)}")
        return 0
    w = max(len(r["project"]) for r in rows)
    print(f"{'PROJECT':<{w}}  {'CHATS':>5}  {'SIZE':>9}  LAST ACTIVITY")
    for r in rows:
        flag = "" if r["exists"] else "   <- folder missing (orphan)"
        print(f"{r['project']:<{w}}  {r['sessions']:>5}  {human_size(r['size']):>9}  {r['last']}{flag}")
    orphans = sum(not r["exists"] for r in rows)
    print(f"\n{len(rows)} projects, {sum(r['sessions'] for r in rows)} chats, "
          f"{human_size(sum(r['size'] for r in rows))}" + (f", {orphans} orphaned" if orphans else ""))
    if orphans:
        print("Orphans are chats whose folder was moved or deleted. If you moved it, relink with:\n"
              "  handoff-vault move <old path> <new path> --history-only")
    return 0


def cmd_backup(args):
    dest = abspath(args.dest)
    os.makedirs(dest, exist_ok=True)
    index = []
    errors = []
    total_new = total_upd = 0
    for h in all_histories(args):
        if not h.sessions:
            continue
        p = h.project or h.name
        folder = readable_name(p)
        new, upd = copy_tree_incremental(h.path, os.path.join(dest, folder, h.name), errors)
        total_new += new
        total_upd += upd
        index.append({"folder": folder, "project": p, "history": h.name, "sessions": len(h.sessions)})
        if new or upd:
            print(f"  {folder}: {new} new, {upd} updated")
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"tool": f"handoff-vault {__version__}", "created": dt.datetime.now().isoformat(timespec="seconds"),
                   "source": projects_dir(args), "projects": index}, fh, ensure_ascii=False, indent=1)
    print(f"Backed up {len(index)} projects to {dest}: {total_new} new files, {total_upd} updated. "
          "Nothing was deleted.")
    if errors:
        print(f"{len(errors)} files could not be copied (they are probably in use; run backup again later):")
        for e in errors[:10]:
            print(f"  {e}")
        return 1
    print("Restore a project's chats with: handoff-vault move <original path> <path> --history-only "
          f"--from-backup {dest}")
    return 0


def _history_sources(args, old):
    if getattr(args, "from_backup", None):
        root = abspath(args.from_backup)
        hs = []
        for folder in os.listdir(root):
            sub = os.path.join(root, folder)
            if not os.path.isdir(sub):
                continue
            for name in os.listdir(sub):
                h = History(os.path.join(sub, name))
                if os.path.isdir(h.path) and h.project and remap_cwd(h.project, old, old) is not None:
                    hs.append(h)
        return hs
    return histories_for(args, old)


def cmd_move(args):
    old, new = abspath(args.old), abspath(args.new)
    if norm(old) == norm(new):
        print("Old and new paths are the same.")
        return 2
    if not args.history_only:
        if not os.path.isdir(old):
            print(f"Folder not found: {old}\nIf you already moved it by hand, add --history-only.")
            return 2
        if os.path.exists(new):
            print(f"Target already exists: {new}\nIf the project is already there, add --history-only.")
            return 2
        if remap_cwd(new, old, old) is not None:
            print("Cannot move a folder into itself.")
            return 2
    sources = _history_sources(args, old)
    plan = []
    for h in sources:
        new_cwd = remap_cwd(h.project, old, new)
        plan.append((h, new_cwd, os.path.join(projects_dir(args), encode_path(new_cwd))))

    print(f"{'Would move' if args.dry_run else 'Moving'} project:\n  from {old}\n  to   {new}")
    if args.history_only:
        print("  (folder already in place: history only)")
    if not plan:
        print("No chat history found for this folder. Nothing to relink.")
    for h, new_cwd, target in plan:
        print(f"  chats: {len(h.sessions):>3}  {h.project}  ->  {new_cwd}")
    if args.dry_run:
        return 0

    if not args.history_only:
        os.makedirs(os.path.dirname(new) or ".", exist_ok=True)
        shutil.move(old, new)
    copied = skipped = 0
    for h, new_cwd, target in plan:
        c, s = relink_history(h.path, target, old, new, force=args.force)
        copied += c
        skipped += s
    print(f"Done. {copied} chats relinked" + (f", {skipped} already there (use --force to overwrite)" if skipped else "")
          + ".")
    if plan and not getattr(args, "from_backup", None):
        print("The old history was kept and now shows as an orphan in `handoff-vault status`;"
              " delete it yourself once you are happy.")
    print(f"Continue in the new place:  cd \"{new}\"  &&  claude --resume")
    return 0


def _walk_project(root, excludes, include_git):
    for cur, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs
                   if not any(fnmatch.fnmatch(d, pat) for pat in excludes) and (include_git or d != ".git")]
        for f in files:
            full = os.path.join(cur, f)
            yield full, os.path.relpath(full, root)


def cmd_scan(args):
    if args.all:
        targets = [h for h in all_histories(args) if h.sessions]
    else:
        project = abspath(args.project or os.getcwd())
        targets = histories_for(args, project)
    total = 0
    for h in targets:
        for s in h.sessions:
            hits = scan_text_file(os.path.join(h.path, s))
            if not hits:
                continue
            total += len(hits)
            kinds = collections.Counter(n for n, _, _ in hits)
            print(f"{h.project or h.name}  [{s[:8]}]  " + ", ".join(f"{k} x{v}" for k, v in kinds.items()))
            if args.verbose:
                for name, lineno, preview in hits[:20]:
                    print(f"    line {lineno}: {name} {preview}")
    if total:
        print(f"\nFound {total} possible secrets. Anyone you give these chats to can read them. "
              "Rotate real keys, and export with --redact.")
        return 1
    print("No secrets found.")
    return 0


def cmd_export(args):
    project = abspath(args.project or os.getcwd())
    if not os.path.isdir(project):
        print(f"Folder not found: {project}")
        return 2
    histories = [] if args.no_chats else histories_for(args, project)
    out = abspath(args.output or os.path.join(
        os.path.dirname(project), f"{os.path.basename(project)}-{dt.date.today().isoformat()}.hvault.zip"))
    if norm(os.path.dirname(out)).startswith(norm(project)):
        print("Write the export outside the project folder (use -o).")
        return 2

    found = []
    for h in histories:
        for s in h.sessions:
            found += [(h, s, x) for x in scan_text_file(os.path.join(h.path, s))]
    if found and not (args.redact or args.allow_secrets):
        kinds = collections.Counter(x[0] for _, _, x in found)
        print("Export stopped: the chats contain what looks like secrets: "
              + ", ".join(f"{k} x{v}" for k, v in kinds.items()))
        print("Re-run with --redact to mask them in the export, or --allow-secrets if you trust the recipient.")
        return 1

    excludes = DEFAULT_EXCLUDES + (args.exclude or [])
    if args.include_heavy:
        excludes = args.exclude or []
    count = 0
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in _walk_project(project, excludes, not args.no_git):
            z.write(full, os.path.join("project", rel))
            count += 1
        manifest = {"tool": f"handoff-vault {__version__}", "created": dt.datetime.now().isoformat(timespec="seconds"),
                    "project_name": os.path.basename(project), "original_path": project, "histories": [],
                    "redacted": bool(found and args.redact)}
        for h in histories:
            stage = os.path.join(tmp, h.name)
            shutil.copytree(h.path, stage)
            if args.redact:
                for s in h.sessions:
                    redact_file(os.path.join(stage, s))
            for full, rel in _walk_project(stage, [], True):
                z.write(full, os.path.join("chats", h.name, rel))
            manifest["histories"].append({"folder": h.name, "cwd": h.project, "sessions": len(h.sessions)})
        z.writestr("handoff-vault.json", json.dumps(manifest, ensure_ascii=False, indent=1))
    chats = sum(x["sessions"] for x in manifest["histories"])
    print(f"Exported {count} files and {chats} chats to {out} ({human_size(os.path.getsize(out))}).")
    if found and args.redact:
        print(f"{len(found)} possible secrets were masked in the exported chats.")
    print(f"Excluded folders: {', '.join(excludes) if excludes else 'none'}"
          + ("; .git excluded" if args.no_git else ""))
    return 0


def _safe_members(z, prefix):
    for info in z.infolist():
        name = info.filename.replace("\\", "/")
        if not name.startswith(prefix) or name.endswith("/"):
            continue
        rel = name[len(prefix):]
        parts = rel.split("/")
        if not rel or ".." in parts or rel.startswith("/") or re.match(r"^[A-Za-z]:", rel):
            raise ValueError(f"unsafe path in archive: {name}")
        yield info, rel


def cmd_import(args):
    src = abspath(args.archive)
    dest = abspath(args.dest)
    if os.path.exists(dest) and os.listdir(dest):
        print(f"Target folder is not empty: {dest}")
        return 2
    with zipfile.ZipFile(src) as z:
        try:
            manifest = json.loads(z.read("handoff-vault.json"))
        except KeyError:
            print("This zip was not made by handoff-vault export (no handoff-vault.json).")
            return 2
        old = manifest["original_path"]
        try:  # validate every path before writing anything
            project_members = list(_safe_members(z, "project/"))
            chat_members = list(_safe_members(z, "chats/"))
        except ValueError as e:
            print(f"Refusing to import: {e}")
            return 2
        files = 0
        for info, rel in project_members:
            target = os.path.join(dest, *rel.split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as fin, open(target, "wb") as fout:
                shutil.copyfileobj(fin, fout)
            files += 1
        os.makedirs(dest, exist_ok=True)
        relinked = 0
        with tempfile.TemporaryDirectory() as tmp:
            for info, rel in chat_members:
                target = os.path.join(tmp, *rel.split("/"))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(info) as fin, open(target, "wb") as fout:
                    shutil.copyfileobj(fin, fout)
            for hist in manifest.get("histories", []):
                stage = os.path.join(tmp, hist["folder"])
                if not os.path.isdir(stage):
                    continue
                cwd = hist.get("cwd") or old
                new_cwd = remap_cwd(cwd, old, dest) or dest
                c, _ = relink_history(stage, os.path.join(projects_dir(args), encode_path(new_cwd)), old, dest,
                                      force=args.force)
                relinked += c
    print(f"Imported {files} files into {dest} and relinked {relinked} chats.")
    if manifest.get("redacted"):
        print("Note: secrets were masked in these chats when they were exported.")
    print(f"Continue:  cd \"{dest}\"  &&  claude --resume   (start with: read HANDOFF.md)")
    return 0


def _git_commits(path, since):
    if not os.path.isdir(os.path.join(path, ".git")):
        return []
    try:
        out = subprocess.run(["git", "-C", path, "log", f"--since={since.isoformat()}", "--format=%cI"],
                             capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [dt.datetime.fromisoformat(t.replace("Z", "+00:00")) for t in out.split()]


def _is_prompt(obj):
    """True for a message the person typed (not a tool result or an injected note)."""
    if obj.get("type") != "user" or obj.get("isMeta") or obj.get("isSidechain"):
        return False
    content = (obj.get("message") or {}).get("content")
    if isinstance(content, str):
        return not content.lstrip().startswith("<")
    return isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "text" for p in content) \
        and not any(isinstance(p, dict) and p.get("type") == "tool_result" for p in content)


def cmd_stats(args):
    now = dt.datetime.now(dt.timezone.utc)
    since = now - dt.timedelta(days=args.days)
    rows = {}
    for h in all_histories(args):
        p = h.project
        if not p or not h.sessions:
            continue
        r = rows.setdefault(norm(p), {"project": p, "prompts": 0, "chats": 0, "commits": 0, "files": 0})
        for s in h.sessions:
            f = os.path.join(h.path, s)
            if os.path.getmtime(f) < since.timestamp():
                continue
            active = False
            for line in iter_lines(f):
                if '"type":"user"' not in line.replace(" ", ""):
                    continue
                try:
                    obj = json.loads(line)
                    ts = dt.datetime.fromisoformat(obj["timestamp"].replace("Z", "+00:00"))
                except (ValueError, KeyError, TypeError):
                    continue
                if ts >= since and _is_prompt(obj):
                    r["prompts"] += 1
                    active = True
            r["chats"] += active
    for r in rows.values():
        p = r["project"]
        if os.path.isdir(p):
            r["commits"] = len(_git_commits(p, since))
            for full, _ in _walk_project(p, DEFAULT_EXCLUDES, False):
                try:
                    if os.path.getmtime(full) >= since.timestamp():
                        r["files"] += 1
                except OSError:
                    pass
    result = sorted(rows.values(), key=lambda r: (r["prompts"] + r["commits"] + r["files"]), reverse=True)
    if args.json:
        print(json.dumps({"days": args.days, "generated": now.isoformat(timespec="seconds"), "projects": result},
                         ensure_ascii=False, indent=1))
        return 0
    active = [r for r in result if r["prompts"] or r["commits"] or r["files"]]
    if not active:
        print(f"No activity in the last {args.days} days.")
        return 0
    w = max(len(r["project"]) for r in active)
    print(f"Last {args.days} days\n{'PROJECT':<{w}}  {'PROMPTS':>7}  {'CHATS':>5}  {'COMMITS':>7}  {'FILES':>5}")
    for r in active:
        print(f"{r['project']:<{w}}  {r['prompts']:>7}  {r['chats']:>5}  {r['commits']:>7}  {r['files']:>5}")
    return 0


CLAUDE_MD = """# {name}

<!-- Claude reads this file at the start of every chat. Keep it short (under ~60 lines):
     every line here is paid for in every message. Put the changing state in HANDOFF.md. -->

One sentence: what this project is and who it is for.

## Stack
- ...

## Commands
- Run: `...`
- Test: `...`
- Build: `...`

## Layout
- `src/...` — ...

## Rules
- Read HANDOFF.md before starting work; update it (`/handoff`) before ending a long chat.
- Do not read generated folders (node_modules, dist, build) unless asked.
"""

HANDOFF_MD = """# HANDOFF — {name}

_Updated: {date}_

<!-- The state of the work, for the next chat. Keep it to one screen.
     Start a new chat with: "read HANDOFF.md and let's continue". -->

## Goal
One or two sentences: what we are building and why.

## Done
- ...

## Next step
- ...

## Decisions (and why)
- ...

## Known problems
- ...
"""

HANDOFF_COMMAND = """---
description: Update HANDOFF.md so a fresh chat can pick up the work
---
Update `HANDOFF.md` in the project root (create it if missing) so that a brand-new chat with no
memory of this conversation can continue the work. Keep it to one screen (about 60 lines):

- **Goal** — one or two sentences.
- **Done** — what is finished, with file paths and commit hashes where useful.
- **Next step** — the concrete next action(s), in order.
- **Decisions (and why)** — choices made in this chat that should not be re-discussed.
- **Known problems** — bugs, risks, things not verified.
- Set the "Updated" date to today.

Replace outdated content instead of appending a diary. Do not copy code or long logs.
Then reply with two lines: what changed in HANDOFF.md, and the sentence to start the next chat:
"read HANDOFF.md and let's continue". $ARGUMENTS
"""


def cmd_init(args):
    project = abspath(args.project or os.getcwd())
    if not os.path.isdir(project):
        print(f"Folder not found: {project}")
        return 2
    name = os.path.basename(project)
    files = {
        os.path.join(project, "CLAUDE.md"): CLAUDE_MD.format(name=name),
        os.path.join(project, "HANDOFF.md"): HANDOFF_MD.format(name=name, date=dt.date.today().isoformat()),
        os.path.join(project, ".claude", "commands", "handoff.md"): HANDOFF_COMMAND,
    }
    for path, text in files.items():
        rel = os.path.relpath(path, project)
        if os.path.exists(path) and not args.force:
            print(f"  kept     {rel} (already exists)")
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"  created  {rel}")
    print("Fill in CLAUDE.md once. Before a long chat gets slow or compacts, type /handoff,"
          " then open a new chat and say: read HANDOFF.md and let's continue.")
    return 0


# --------------------------------------------------------------------------- CLI

def build_parser():
    p = argparse.ArgumentParser(
        prog="handoff-vault",
        description="Back up, move and hand off Claude Code projects without losing chat history.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--claude-dir", help="Claude Code config folder (default: $CLAUDE_CONFIG_DIR or ~/.claude)")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("status", help="list projects with chat history; find orphans")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("backup", help="copy all chat history into DEST with readable names (never deletes)")
    s.add_argument("dest")
    s.set_defaults(func=cmd_backup)

    s = sub.add_parser("move", help="move a project folder and carry its chat history along")
    s.add_argument("old")
    s.add_argument("new")
    s.add_argument("--history-only", action="store_true", help="the folder is already at NEW; just relink chats")
    s.add_argument("--from-backup", metavar="DIR", help="take the history from a `backup` folder")
    s.add_argument("--dry-run", action="store_true", help="show what would happen")
    s.add_argument("--force", action="store_true", help="overwrite chats that already exist at the new place")
    s.set_defaults(func=cmd_move)

    s = sub.add_parser("export", help="pack a project and its chats into one zip")
    s.add_argument("project", nargs="?", help="project folder (default: current folder)")
    s.add_argument("-o", "--output", help="zip file to write")
    s.add_argument("--no-chats", action="store_true", help="export the code only")
    s.add_argument("--no-git", action="store_true", help="leave the .git folder out")
    s.add_argument("--exclude", action="append", metavar="PATTERN", help="extra folder name to leave out")
    s.add_argument("--include-heavy", action="store_true", help="keep node_modules, dist, .venv and similar")
    s.add_argument("--redact", action="store_true", help="mask possible secrets in the exported chats")
    s.add_argument("--allow-secrets", action="store_true", help="export even if secrets were found")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("import", help="unpack an export into DEST and relink its chats")
    s.add_argument("archive")
    s.add_argument("dest")
    s.add_argument("--force", action="store_true", help="overwrite chats that already exist")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("scan", help="look for API keys, tokens and private keys in chat history")
    s.add_argument("project", nargs="?", help="project folder (default: current folder)")
    s.add_argument("--all", action="store_true", help="scan every project")
    s.add_argument("-v", "--verbose", action="store_true", help="show line numbers")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("stats", help="activity per project over the last N days")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("init", help="add CLAUDE.md, HANDOFF.md and a /handoff command to a project")
    s.add_argument("project", nargs="?", help="project folder (default: current folder)")
    s.add_argument("--force", action="store_true", help="overwrite existing files")
    s.set_defaults(func=cmd_init)
    return p


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
