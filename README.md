# Agent Receipt — how to start

## What's in this folder
- `CLAUDE.md` — the project brief. Claude Code reads this automatically at the start of every session.
- `PROGRESS.md` — where the project is. Claude Code updates it each session.
- `docs/DESIGN.md` — the reasoning behind the design, so future sessions don't re-argue it.
- `docs/SOURCES.md` — filled in during session 1: where the agent logs live on your machine.
- `LICENSE` — MIT. Put your name in it.

## Starting session 1
1. Put this folder inside `Claude Business Ideas` on your Desktop.
2. Open Terminal (Mac) or PowerShell (Windows).
3. Go into the folder:
   - Mac: `cd ~/Desktop/"Claude Business Ideas"/agent-receipt`
   - Windows: `cd "$HOME\Desktop\Claude Business Ideas\agent-receipt"`
4. Run `claude`.
5. Paste the first prompt below.

## First prompt (paste this)
```
Read CLAUDE.md and docs/DESIGN.md. Then ask me the open questions one at a time.
After that, walk me through v1 step 1 only. I don't code, so explain each
command before you run it and wait for me before moving on.
```

## Every later session
Go into the folder, run `claude`, and paste:
```
Read CLAUDE.md and PROGRESS.md. Tell me where we are and what today's step is, then wait for me.
```

## What to expect from session 1
About an hour. By the end: the repo exists, the license and .gitignore are in place, PROGRESS.md has its first entry, and you know which OS-specific path the build is taking. The input monitor (step 2) may or may not start; that's fine.
