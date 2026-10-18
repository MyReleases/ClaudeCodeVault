import contextlib
import datetime as dt
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import claude_code_vault as hv  # noqa: E402

# Built at runtime so no token-shaped string is stored in the repository.
FAKE_TOKEN = "gh" + "p_" + "A1b2C3d4E5" * 4


def line(cwd, kind="user", text="hello", when=None, **extra):
    obj = {"type": kind, "cwd": cwd, "sessionId": "s",
           "timestamp": (when or dt.datetime.now(dt.timezone.utc)).isoformat().replace("+00:00", "Z"),
           "message": {"role": kind, "content": text}}
    obj.update(extra)
    return json.dumps(obj) + "\n"


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ccvault-test-")
        self.claude = os.path.join(self.tmp, "claude")
        self.projects = os.path.join(self.claude, "projects")
        os.makedirs(self.projects)
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.work)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = hv.main(["--claude-dir", self.claude, *argv])
        return code, out.getvalue()

    def make_project(self, name, chats=1, text="hello", sub=None):
        path = os.path.join(self.work, name)
        os.makedirs(os.path.join(path, "src"), exist_ok=True)
        with open(os.path.join(path, "src", "main.py"), "w") as fh:
            fh.write("print('hi')\n")
        os.makedirs(os.path.join(path, "node_modules", "pkg"), exist_ok=True)
        with open(os.path.join(path, "node_modules", "pkg", "index.js"), "w") as fh:
            fh.write("x")
        cwd = os.path.join(path, sub) if sub else path
        hist = os.path.join(self.projects, hv.encode_path(cwd))
        os.makedirs(hist, exist_ok=True)
        for i in range(chats):
            with open(os.path.join(hist, f"session-{name}-{i}.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(line(cwd, text=text))
                fh.write(line(cwd, kind="assistant", text="ok"))
        os.makedirs(os.path.join(hist, f"session-{name}-0", "tool-results"), exist_ok=True)
        with open(os.path.join(hist, f"session-{name}-0", "tool-results", "r.txt"), "w") as fh:
            fh.write("result")
        os.makedirs(os.path.join(hist, "memory"), exist_ok=True)
        with open(os.path.join(hist, "memory", "MEMORY.md"), "w") as fh:
            fh.write("- note\n")
        return path, hist

    def cwds(self, hist):
        out = set()
        for f in os.listdir(hist):
            if f.endswith(".jsonl"):
                with open(os.path.join(hist, f), encoding="utf-8") as fh:
                    out |= {json.loads(x)["cwd"] for x in fh if x.strip()}
        return out


class TestBasics(Sandbox):
    def test_encode_path(self):
        self.assertEqual(hv.encode_path(r"D:\Desktop\My App"), "D--Desktop-My-App")
        self.assertEqual(hv.encode_path("/home/a/b.c"), "-home-a-b-c")

    def test_remap_cwd(self):
        old, new = os.path.join(self.work, "a"), os.path.join(self.work, "b")
        self.assertEqual(hv.remap_cwd(old, old, new), new)
        self.assertEqual(hv.remap_cwd(os.path.join(old, "x"), old, new), os.path.join(new, "x"))
        self.assertIsNone(hv.remap_cwd(os.path.join(self.work, "ab"), old, new))

    def test_fork_detection_prefers_matching_cwd(self):
        path, hist = self.make_project("real")
        with open(os.path.join(hist, "fork.jsonl"), "w", encoding="utf-8") as fh:
            for _ in range(50):
                fh.write(line(os.path.join(self.work, "other")))
        self.assertEqual(hv.project_path_of(hist), path)

    def test_status_reports_orphans(self):
        path, _ = self.make_project("gone")
        shutil.rmtree(path)
        code, out = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("orphan", out)
        code, out = self.run_cli("status", "--json")
        rows = json.loads(out)
        self.assertFalse(rows[0]["exists"])


class TestBackup(Sandbox):
    def test_backup_is_incremental_and_readable(self):
        self.make_project("alpha", chats=2)
        dest = os.path.join(self.tmp, "backup")
        self.run_cli("backup", dest)
        folders = [f for f in os.listdir(dest) if f != "manifest.json"]
        self.assertEqual(len(folders), 1)
        self.assertTrue(folders[0].startswith("alpha__"))
        code, out = self.run_cli("backup", dest)
        self.assertIn("0 new files, 0 updated", out)

    def test_twins_do_not_collide(self):
        os.makedirs(os.path.join(self.work, "x"))
        self.work = os.path.join(self.work, "x")
        self.make_project("app")
        self.work = os.path.dirname(self.work)
        self.make_project("app")
        dest = os.path.join(self.tmp, "backup")
        self.run_cli("backup", dest)
        self.assertEqual(len([f for f in os.listdir(dest) if f.startswith("app__")]), 2)


class TestMove(Sandbox):
    def test_move_carries_history(self):
        old, hist = self.make_project("proj", chats=2)
        new = os.path.join(self.tmp, "elsewhere", "proj")
        code, out = self.run_cli("move", old, new)
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isfile(os.path.join(new, "src", "main.py")))
        new_hist = os.path.join(self.projects, hv.encode_path(new))
        self.assertEqual(self.cwds(new_hist), {new})
        self.assertTrue(os.path.isfile(os.path.join(new_hist, "memory", "MEMORY.md")))
        self.assertTrue(os.path.isfile(os.path.join(new_hist, "session-proj-0", "tool-results", "r.txt")))
        self.assertTrue(os.path.isdir(hist), "old history must be kept")

    def test_move_subfolder_sessions(self):
        old, _ = self.make_project("mono", sub="packages")
        new = os.path.join(self.tmp, "mono2")
        self.run_cli("move", old, new)
        target = os.path.join(self.projects, hv.encode_path(os.path.join(new, "packages")))
        self.assertEqual(self.cwds(target), {os.path.join(new, "packages")})

    def test_dry_run_changes_nothing(self):
        old, _ = self.make_project("dry")
        before = sorted(os.listdir(self.projects))
        self.run_cli("move", old, os.path.join(self.tmp, "dry2"), "--dry-run")
        self.assertTrue(os.path.isdir(old))
        self.assertEqual(before, sorted(os.listdir(self.projects)))

    def test_history_only_after_manual_move(self):
        old, _ = self.make_project("manual")
        new = os.path.join(self.tmp, "manual-moved")
        shutil.move(old, new)
        code, out = self.run_cli("move", old, new, "--history-only")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.cwds(os.path.join(self.projects, hv.encode_path(new))), {new})

    def test_restore_from_backup(self):
        old, hist = self.make_project("lost")
        dest = os.path.join(self.tmp, "backup")
        self.run_cli("backup", dest)
        shutil.rmtree(hist)
        new = os.path.join(self.tmp, "found")
        shutil.move(old, new)
        code, out = self.run_cli("move", old, new, "--history-only", "--from-backup", dest)
        self.assertEqual(code, 0, out)
        self.assertEqual(self.cwds(os.path.join(self.projects, hv.encode_path(new))), {new})

    def test_refuses_existing_target(self):
        old, _ = self.make_project("a")
        os.makedirs(os.path.join(self.tmp, "taken"))
        code, _ = self.run_cli("move", old, os.path.join(self.tmp, "taken"))
        self.assertEqual(code, 2)
        self.assertTrue(os.path.isdir(old))


class TestExportImport(Sandbox):
    def test_roundtrip(self):
        old, _ = self.make_project("trip", chats=2)
        out = os.path.join(self.tmp, "trip.zip")
        code, msg = self.run_cli("export", old, "-o", out)
        self.assertEqual(code, 0, msg)
        names = zipfile.ZipFile(out).namelist()
        self.assertIn("project/src/main.py", [n.replace("\\", "/") for n in names])
        self.assertFalse(any("node_modules" in n for n in names))
        new = os.path.join(self.tmp, "imported")
        code, msg = self.run_cli("import", out, new)
        self.assertEqual(code, 0, msg)
        self.assertTrue(os.path.isfile(os.path.join(new, "src", "main.py")))
        self.assertEqual(self.cwds(os.path.join(self.projects, hv.encode_path(new))), {new})

    def test_export_stops_on_secrets_and_redacts(self):
        old, _ = self.make_project("leaky", text=f"my token is {FAKE_TOKEN}")
        out = os.path.join(self.tmp, "leaky.zip")
        code, msg = self.run_cli("export", old, "-o", out)
        self.assertEqual(code, 1)
        self.assertIn("GitHub token", msg)
        self.assertFalse(os.path.exists(out))
        code, msg = self.run_cli("export", old, "-o", out, "--redact")
        self.assertEqual(code, 0, msg)
        z = zipfile.ZipFile(out)
        blob = "".join(z.read(n).decode("utf-8", "replace") for n in z.namelist() if n.endswith(".jsonl"))
        self.assertNotIn(FAKE_TOKEN, blob)
        self.assertIn("[REDACTED GitHub token]", blob)

    def test_import_rejects_unsafe_paths(self):
        bad = os.path.join(self.tmp, "bad.zip")
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("claude-code-vault.json", json.dumps({"original_path": "/x", "histories": []}))
            z.writestr("project/ok.txt", "fine")
            z.writestr("project/../../evil.txt", "x")
        dest = os.path.join(self.tmp, "dest")
        code, out = self.run_cli("import", bad, dest)
        self.assertEqual(code, 2)
        self.assertIn("unsafe path", out)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")))
        self.assertFalse(os.path.exists(os.path.join(dest, "ok.txt")), "nothing is written before validation")


class TestScanStatsInit(Sandbox):
    def test_scan(self):
        self.make_project("clean")
        code, out = self.run_cli("scan", "--all")
        self.assertEqual(code, 0)
        self.make_project("dirty", text=f"key {FAKE_TOKEN}")
        code, out = self.run_cli("scan", "--all")
        self.assertEqual(code, 1)
        self.assertNotIn(FAKE_TOKEN, out)

    def test_stats_counts_prompts_not_tool_results(self):
        path, hist = self.make_project("busy")
        with open(os.path.join(hist, "extra.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(line(path, text="do it"))
            fh.write(line(path, text=[{"type": "tool_result", "content": "x"}]))
            fh.write(line(path, text="<command-name>/clear</command-name>"))
            fh.write(line(path, text="old", when=dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)))
        code, out = self.run_cli("stats", "--json")
        row = json.loads(out)["projects"][0]
        self.assertEqual(row["prompts"], 2)  # "hello" from the fixture + "do it"

    def test_init_does_not_overwrite(self):
        path, _ = self.make_project("fresh")
        with open(os.path.join(path, "CLAUDE.md"), "w") as fh:
            fh.write("mine")
        self.run_cli("init", path)
        with open(os.path.join(path, "CLAUDE.md")) as fh:
            self.assertEqual(fh.read(), "mine")
        self.assertTrue(os.path.isfile(os.path.join(path, "HANDOFF.md")))
        self.assertTrue(os.path.isfile(os.path.join(path, ".claude", "commands", "handoff.md")))


if __name__ == "__main__":
    unittest.main()
