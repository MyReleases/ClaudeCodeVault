# handoff-vault

**Back up, move and hand off Claude Code projects without losing your chats.**

[Русская версия](README.ru.md)

Claude Code stores every conversation under `~/.claude/projects/<encoded folder path>/`. That works
until you:

- **move or rename a project folder:** the chats are still there, but `claude --resume` in the new
  folder can't see them;
- **want a backup:** the history sits in folders named like `C--Users-me-Desktop-my-app`, mixed with
  forks that point at other paths;
- **switch computers or accounts:** there is no export that brings the chats along;
- **keep one chat going for days:** every message re-reads the whole history, so long chats get slow
  and expensive.

`handoff-vault` is one Python file with no dependencies that fixes all four.

```
$ handoff-vault status
PROJECT                          CHATS       SIZE  LAST ACTIVITY
/home/me/code/shop                   4    38.8 MB  2026-10-16 15:41
/home/me/Desktop/old-shop            3    31.7 MB  2026-10-02 13:53   <- folder missing (orphan)

$ handoff-vault move ~/Desktop/old-shop ~/code/shop --history-only
Done. 3 chats relinked.
```

## Install

Python 3.8+ is all you need.

```bash
pipx install git+https://github.com/MyReleases/handoff-vault
```

Or just download [`handoff_vault.py`](handoff_vault.py) and run `python handoff_vault.py <command>`.

## Commands

| Command | What it does |
|---|---|
| `status` | Every project with chat history: chats, size, last activity. Flags **orphans**: chats whose folder was moved or deleted. |
| `backup DEST` | Copies all history into `DEST/<project>__<hash>/` with a `manifest.json`. Incremental, never deletes. |
| `move OLD NEW` | Moves the folder **and** its chats, rewriting the stored paths so `claude --resume` works in the new place. `--history-only` if you already moved the folder, `--from-backup DIR` to restore from a backup, `--dry-run` to preview. |
| `export [PROJECT]` | One zip with the project, its chats and a manifest. Skips `node_modules`, `.venv`, `dist` and the like. **Stops if the chats contain secrets** (see `scan`) unless you pass `--redact` or `--allow-secrets`. |
| `import ZIP DEST` | Unpacks an export anywhere (another machine, another account) and relinks the chats to `DEST`. |
| `scan [PROJECT] / --all` | Looks for API keys, GitHub/Slack/Stripe/Telegram tokens and private keys in chat history. Chats often contain secrets you pasted or that tools printed. |
| `stats [--days 7]` | Activity per project: your prompts, active chats, git commits, changed files. `--json` for dashboards. |
| `init [PROJECT]` | Adds `CLAUDE.md`, `HANDOFF.md` and a `/handoff` slash command (never overwrites). |

Every command takes `--claude-dir` if your config lives somewhere other than `~/.claude`
(`$CLAUDE_CONFIG_DIR` is honoured too).

## The habit that saves the most tokens

Tools help, but the biggest win is how you use chats:

1. **`CLAUDE.md`** holds what never changes: stack, commands, rules. Keep it short; it is read on
   every message.
2. **`HANDOFF.md`** holds the state of the work: goal, done, next step, decisions, known problems.
   One screen.
3. **One task, one chat.** When a chat gets long or starts compacting, type **`/handoff`**. Claude
   rewrites `HANDOFF.md`. Then open a new chat and say *"read HANDOFF.md and let's continue"*.
   The new chat starts with one screen of context instead of megabytes of history.
4. **`handoff-vault backup`** now and then, so the old chats are never lost.

`handoff-vault init` sets up 1–3 in a second.

## Safety

- Nothing is deleted. `move` copies the history to the new place and leaves the original, which then
  shows as an orphan in `status`. Remove it yourself when you're happy.
- `move` refuses to overwrite an existing folder or existing chats (unless `--force` for chats).
- `import` checks every path in the zip before writing anything (no `../` tricks).
- `scan` and `export` never print a full secret, only a masked preview.

## Limitations

- **Claude desktop app sidebar.** The app keeps its own list of sessions. After `move`, continue from
  a terminal with `claude --resume` or start a new chat in the new folder. Old sidebar entries may
  still point at the old path.
- **Very long paths.** Claude Code names history folders by replacing every non-alphanumeric
  character with `-`. Finding history always works (by the path recorded inside the chats), but for
  folder paths over about 200 characters newer Claude Code versions may shorten the name, and a moved
  chat might not show in `--resume`. Please open an issue if you hit this.
- **File checkpoints.** Paths inside file-history snapshots are not rewritten, so `/rewind` of file
  edits made *before* a move may not find the files.
- Tested on Windows, macOS and Linux (CI) with Python 3.8–3.13. Chat format of Claude Code 2.1.x.

## Development

```bash
python -m unittest discover -s tests -v
```

Not affiliated with Anthropic. "Claude" and "Claude Code" are trademarks of Anthropic.
MIT License.
