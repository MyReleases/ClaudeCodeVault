# ClaudeCodeVault

**Never lose a Claude Code chat again.** Back up your AI coding conversations, move projects
without breaking `claude --resume`, carry them to another computer, and keep chats short so
they cost fewer tokens.

[![tests](https://github.com/MyReleases/ClaudeCodeVault/actions/workflows/tests.yml/badge.svg)](https://github.com/MyReleases/ClaudeCodeVault/actions/workflows/tests.yml)
&nbsp;One Python file · no dependencies · Windows, macOS, Linux · MIT

[Читать по-русски](README.ru.md)

> An unofficial community tool for [Claude Code](https://docs.claude.com/en/docs/claude-code),
> Anthropic's AI coding assistant. Not affiliated with Anthropic.

---

## Contents

1. [Why you might need it](#why-you-might-need-it)
2. [Install (5 minutes)](#install-5-minutes)
3. [First run](#first-run)
4. [What to do when…](#what-to-do-when)
5. [The habit that saves the most tokens](#the-habit-that-saves-the-most-tokens)
6. [All commands](#all-commands)
7. [How it works](#how-it-works)
8. [Safety and privacy](#safety-and-privacy)
9. [Limitations](#limitations)
10. [FAQ](#faq)

---

## Why you might need it

Claude Code saves every conversation on your computer, in a hidden folder:

```
~/.claude/projects/
├── C--Users-anna-Desktop-shop/        <- chats you had in C:\Users\anna\Desktop\shop
│   ├── 3f2a…e1.jsonl                  <- one file = one chat
│   └── memory/                        <- what Claude remembers about this project
└── -home-anna-code-blog/              <- chats you had in /home/anna/code/blog
```

The folder name **is the path of your project**. That simple design causes four everyday problems:

| You… | What happens | ClaudeCodeVault |
|---|---|---|
| move or rename a project folder | `claude --resume` in the new place shows **no chats**. They still exist, but they are "orphans". | `move` carries the chats along |
| want a backup | the history is a pile of cryptic folder names, including forks that point at other paths | `backup` with readable names |
| get a new computer or a work account | there is no way to export a project **with** its chats | `export` / `import` |
| keep one chat going for days | each message re-reads the whole history: slower, pricier, and it starts "forgetting" (compacting) | `init` + `/handoff` workflow |

And one you may not know about: chats often contain **API keys and tokens** you pasted or a tool
printed. `scan` finds them before you share anything.

---

## Install (5 minutes)

### Step 1. Check that Python is installed

Open a terminal (**Windows:** press `Win`, type `PowerShell`, Enter. **macOS:** Terminal.
**Linux:** your terminal) and run:

```bash
python --version
```

You need **3.8 or newer**. If you see an error:

- **Windows:** install Python from [python.org/downloads](https://www.python.org/downloads/).
  On the first screen of the installer **tick "Add python.exe to PATH"**. Then close and reopen
  PowerShell. (If `python` still opens the Microsoft Store, try `py --version` and use `py`
  instead of `python` below.)
- **macOS:** `brew install python` or the installer from python.org; then use `python3`.
- **Linux:** usually already there as `python3`.

### Step 2. Install ClaudeCodeVault

**Option A (recommended): pipx.** This installs the `ccvault` command in its own sandbox:

```bash
python -m pip install --user pipx
python -m pipx ensurepath
```

Close and reopen the terminal, then:

```bash
pipx install git+https://github.com/MyReleases/ClaudeCodeVault
```

**Option B: plain pip.**

```bash
python -m pip install --user git+https://github.com/MyReleases/ClaudeCodeVault
```

**Option C: no install at all.** Download [`claude_code_vault.py`](claude_code_vault.py) (button
"Download raw file") and run it directly: `python claude_code_vault.py status`.
Everywhere below, replace `ccvault` with `python claude_code_vault.py`.

### Step 3. Check it works

```bash
ccvault --version
ccvault --help
```

`claude-code-vault` is the same command with a longer name.

---

## First run

**1. See what you have.**

```bash
ccvault status
```

```
PROJECT                          CHATS       SIZE  LAST ACTIVITY
C:\Users\anna\code\shop              4    38.8 MB  2026-10-16 15:41
C:\Users\anna\Desktop\old-blog       3    31.7 MB  2026-10-02 13:53   <- folder missing (orphan)

2 projects, 7 chats, 70.5 MB, 1 orphaned
```

An **orphan** is a chat whose project folder no longer exists at that path. Usually you moved it.
See [I already moved a folder](#i-already-moved-a-folder-and-my-chats-are-gone).

**2. Make your first backup.** Pick any folder, ideally one that syncs to the cloud
(OneDrive, Dropbox, iCloud, Google Drive):

```bash
ccvault backup "C:\Users\anna\OneDrive\claude-backup"
```

```
  shop__1a2b3c: 12 new, 0 updated
  old-blog__9f8e7d: 5 new, 0 updated
Backed up 2 projects to C:\Users\anna\OneDrive\claude-backup: 17 new files, 0 updated. Nothing was deleted.
```

Run it again any time: only new and changed chats are copied.

**3. Set up a project for short, cheap chats** (optional, see
[the habit](#the-habit-that-saves-the-most-tokens)):

```bash
ccvault init "C:\Users\anna\code\shop"
```

---

## What to do when…

### I want to move a project to another folder

Use `ccvault move` **instead of** moving the folder by hand. Preview first:

```bash
ccvault move "C:\Users\anna\Desktop\shop" "D:\code\shop" --dry-run
ccvault move "C:\Users\anna\Desktop\shop" "D:\code\shop"
```

The folder moves, the chats are copied to the new location with the paths inside them updated.
Then:

```bash
cd D:\code\shop
claude --resume
```

Your old chats are in the list. Close Claude Code in that folder before moving it.

### I already moved a folder and my chats are gone

They are not gone, just orphaned. Tell the tool where the folder was and where it is now:

```bash
ccvault move "C:\Users\anna\Desktop\shop" "D:\code\shop" --history-only
```

`--history-only` means "don't touch the folder, only relink the chats".

### I deleted the history (or got a new computer) but I have a backup

```bash
ccvault move "C:\old\path\shop" "D:\code\shop" --history-only --from-backup "C:\Users\anna\OneDrive\claude-backup"
```

The first path is where the project was **when the chats were made** (`status` and the backup's
`manifest.json` show it).

### I want to continue on another computer or a work account

On the old computer:

```bash
ccvault export "D:\code\shop" -o shop.zip
```

If the chats contain secrets, the export stops and tells you. Add `--redact` to mask them in the zip,
or `--allow-secrets` if the zip stays with you. Big rebuildable folders (`node_modules`, `.venv`,
`dist`, `build`…) are left out; `.git` is kept (`--no-git` to drop it).

Copy `shop.zip` over (USB stick, cloud drive), install ClaudeCodeVault there and run:

```bash
ccvault import shop.zip "C:\Users\me\code\shop"
cd C:\Users\me\code\shop
claude --resume
```

Reinstall dependencies as usual (`npm install`, `pip install -r requirements.txt`…).

### I want to share a project with a teammate

```bash
ccvault export "D:\code\shop" -o shop-for-max.zip --redact
```

Or share only the code and the handoff notes, without chats:

```bash
ccvault export "D:\code\shop" -o shop-code.zip --no-chats
```

### My chat is getting long and slow

Type `/handoff` in the chat (after `ccvault init`), then open a **new** chat and write
*"read HANDOFF.md and let's continue"*. Details in the next section.

### I want to know whether I leaked a key

```bash
ccvault scan --all
```

```
C:\Users\anna\code\bot  [a41c09e2]  Telegram bot token x2
Found 2 possible secrets. Anyone you give these chats to can read them.
```

If a real key shows up, **revoke it and create a new one** at the provider (GitHub, OpenAI,
BotFather…). Deleting the chat does not undo a leak that already happened.

### I want automatic backups

**Windows (Task Scheduler), every day at 21:00:**

```powershell
schtasks /Create /SC DAILY /ST 21:00 /TN "ClaudeCodeVault backup" /TR "ccvault backup C:\Users\anna\OneDrive\claude-backup"
```

**macOS / Linux (cron), every day at 21:00:** run `crontab -e` and add

```
0 21 * * * $HOME/.local/bin/ccvault backup $HOME/claude-backup
```

### I want to see how active my projects were

```bash
ccvault stats --days 7
```

```
Last 7 days
PROJECT                  PROMPTS  CHATS  COMMITS  FILES
D:\code\shop                  23      1       20     58
```

`--json` gives the same for your own dashboards.

---

## The habit that saves the most tokens

Every message you send makes Claude re-read the whole conversation. A chat that has run for a
week is megabytes of history, so each new message is slow and expensive, and eventually Claude
starts compacting (summarising and forgetting details). The fix is a two-file habit:

| File | What goes in it | When it changes |
|---|---|---|
| `CLAUDE.md` | What the project is, stack, commands to run, rules. Claude Code reads it automatically at the start of every chat. **Keep it short:** it is paid for on every message. | Rarely |
| `HANDOFF.md` | Where the work stands: goal, done, next step, decisions and why, known problems. One screen. | At the end of every chat |

The routine:

1. **One task, one chat.** Start a chat for "add login", not for "the project".
2. When the task is done, or the chat gets long, type **`/handoff`**. Claude rewrites
   `HANDOFF.md` with the current state.
3. Open a **new chat** in the same folder and write: *"read HANDOFF.md and let's continue"*.
   The new chat starts with one screen of context instead of the whole history.
4. Now and then: `ccvault backup`, so the old chats are kept anyway.

`ccvault init` creates both files from templates plus the `/handoff` command
(`.claude/commands/handoff.md`). It never overwrites files you already have.

---

## All commands

Every command accepts `--claude-dir PATH` if your Claude Code config is not in `~/.claude`
(the `CLAUDE_CONFIG_DIR` environment variable is respected too).

### `status`
Lists every project that has chat history: number of chats, size on disk, last activity.
Marks orphans (folder missing). `--json` for machine-readable output.

### `backup DEST`
Copies all history into `DEST/<project name>__<short hash>/…` and writes `DEST/manifest.json`
(which folder belongs to which original path). Only new or changed files are copied.
Nothing is ever deleted from `DEST`, so it is also an archive of chats you removed later.

### `move OLD NEW`
Moves the project folder from `OLD` to `NEW` and copies its chats to the history folder for `NEW`,
updating the recorded paths. Chats started in subfolders of the project move too.

| Option | Meaning |
|---|---|
| `--history-only` | The folder is already at `NEW`; only relink the chats |
| `--from-backup DIR` | Take the chats from a `backup` folder instead of `~/.claude` |
| `--dry-run` | Show what would happen, change nothing |
| `--force` | Overwrite chats that already exist at the new place |

### `export [PROJECT]`
Creates one zip: `project/` (your files), `chats/` (the history), `claude-code-vault.json`
(where it came from). `PROJECT` defaults to the current folder; the zip goes next to the project.

| Option | Meaning |
|---|---|
| `-o FILE` | Where to write the zip |
| `--redact` | Mask anything that looks like a secret in the exported chats |
| `--allow-secrets` | Export even if secrets were found |
| `--no-chats` | Code only |
| `--no-git` | Leave the `.git` folder out |
| `--exclude NAME` | Leave out another folder name (repeatable) |
| `--include-heavy` | Keep `node_modules`, `.venv`, `dist`, `build` and similar |

### `import ZIP DEST`
Unpacks an export into `DEST` (must be empty or not exist) and relinks the chats to `DEST`.
`--force` overwrites chats that already exist.

### `scan [PROJECT]`
Searches chat history for: Anthropic, OpenAI, GitHub, AWS, Google, Slack, Stripe and Telegram
keys/tokens, and private key blocks. `--all` scans every project, `-v` shows line numbers.
Secrets are never printed in full. Exit code 1 if anything is found.

### `stats`
Per project for the last `--days N` (default 7): prompts you typed, chats with activity, git
commits, changed files. `--json` available.

### `init [PROJECT]`
Creates `CLAUDE.md`, `HANDOFF.md` and `.claude/commands/handoff.md`. Keeps existing files unless
`--force`.

---

## How it works

- Claude Code names a project's history folder by taking the project's full path and replacing
  every character that is not a letter or digit with `-`:
  `C:\Users\anna\Desktop\shop` → `C--Users-anna-Desktop-shop`.
- Each chat is a `.jsonl` file: one JSON record per line, and most records carry a `cwd` field,
  the folder the chat ran in.
- To find which project a history folder belongs to, ClaudeCodeVault reads those `cwd` fields
  (so it also handles forked chats that start with someone else's path).
- `move` and `import` copy each chat to the folder name for the new path and rewrite `cwd` from
  the old path to the new one. Everything else in the chat is copied byte for byte. Subfolders
  (`memory/`, tool results) are copied as they are.
- Nothing talks to the network. The only external program it runs is `git log` for `stats`.

## Safety and privacy

- **Nothing is deleted.** `move` leaves the old history in place (it shows as an orphan in
  `status`; remove it yourself when you're sure). `backup` only adds.
- `move` refuses to overwrite an existing folder, and existing chats unless `--force`.
- `import` validates every path inside the zip **before** writing anything (no `../` tricks).
- `scan` and `export` show only a masked preview of a secret, never the whole value.
- **Your chats are private data.** They contain your code, file contents Claude read, command
  output and anything you pasted. Treat an export like a copy of your project folder.

## Limitations

- **Claude desktop app sidebar.** The desktop app keeps its own list of sessions. After a `move`,
  continue from the terminal (`claude --resume`) or start a new chat in the new folder; old
  sidebar entries may still point to the old path.
- **Very long paths.** For project paths over about 200 characters newer Claude Code versions may
  shorten the history folder name, and a moved chat may then not appear in `--resume`. Finding
  history still works. Please open an issue if you hit this.
- **File checkpoints.** Paths inside file-history snapshots are not rewritten, so `/rewind` of file
  edits made *before* a move may not find the files.
- Only Claude Code (the terminal/IDE/desktop coding agent). Conversations on claude.ai are stored
  by Anthropic, not on your computer; export those in claude.ai Settings → Privacy → Export data.
- Built against the chat format of Claude Code 2.1.x.

## FAQ

**Does it send my chats anywhere?** No. It works only with files on your computer and makes no
network requests.

**Will it break my Claude Code setup?** It never edits your existing history files. It only adds
copies (with updated paths) and new files.

**What if I run `backup` to the same folder every day?** That is the intended use: it copies only
what changed.

**Can I undo a `move`?** Yes: `ccvault move NEW OLD` moves it back, and the original history was
never removed.

**Does it work with other AI coding tools?** Not yet: only Claude Code's history format.

## Uninstall

```bash
pipx uninstall claude-code-vault        # or: python -m pip uninstall claude-code-vault
```

Your backups and exports stay where you put them.

## Contributing

Issues and pull requests are welcome, especially reports from macOS/Linux and from very large
histories. Run the tests with:

```bash
python -m unittest discover -s tests -v
```

## License

MIT. "Claude" and "Claude Code" are trademarks of Anthropic, PBC. This project is not affiliated
with or endorsed by Anthropic.
