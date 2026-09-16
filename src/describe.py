"""Plain-English one-liners for receipt rows, for people who don't read shell.

The raw target is always kept; this is the sentence shown above it.
"""

import re
import shlex
from pathlib import Path

SEPARATORS = {"&&", "||", ";", ";;", "|", "&", "(", ")"}
SKIP = {"cd", "echo", "printf", "true", "sleep", "cat", "ls", "grep", "head", "tail", "wc",
        "sort", "uniq", "cut", "tr", "pwd", "test", "[", "xargs", "tee", "time", "sudo", "nohup"}
MAX_PARTS = 3


def describe(action_type: str, target, tool: str = "", cwd=None, extra: dict | None = None) -> str:
    target = target or ""
    extra = extra or {}
    if action_type == "file_write":
        return _describe_file(tool, target, cwd, extra)
    if action_type == "execute":
        return _describe_command(target)
    if action_type == "send_email":
        return f"Sent an email to {target}" if target else "Sent an email"
    if action_type == "create_event":
        return f"Created a calendar event: {target}" if target else "Created a calendar event"
    if action_type == "purchase":
        amount = extra.get("amount")
        money = f"{amount:,.2f} {extra.get('currency') or ''}".strip() if amount is not None else "money"
        return f"Paid {money} at {target}" if target else f"Paid {money}"
    if action_type == "post":
        if tool == "SendUserFile":
            return f"Handed a file to the user: {_name(target)}"
        if tool == "Artifact":
            return f"Published a page: {_name(target)}"
        return f"Sent or posted: {_short(target)}" if target else "Sent or posted a message"
    return _describe_other(tool, target, extra)


def _describe_file(tool, path, cwd, extra):
    name = _relative(path, cwd)
    if tool in ("codex:file_delete", "cursor:delete") or extra.get("deleted"):
        return f"Deleted {name}"
    if tool == "Write":
        return f"Wrote the file {name}"
    if tool in ("Edit", "NotebookEdit"):
        return f"Edited {name}"
    if tool.startswith("declared:"):
        return f"Changed the file {name}"
    return f"Changed the file {name}"


def _describe_other(tool, target, extra):
    short = tool.split("__", 2)[-1] if tool.startswith("mcp__") else tool
    if tool.startswith("agent-receipt:"):                                  # the app's own buttons
        return _short(target)
    if tool.startswith("cursor:"):
        return f"Cursor tool {tool[len('cursor:'):]}: {_short(target)}" if target else f"Cursor tool {tool[len('cursor:'):]}"
    if tool == "Agent":
        return f"Started a sub-agent: {_short(target)}" if target else "Started a sub-agent"
    if tool == "calendar:changed":
        return f"Changed a calendar event: {_short(target.replace('Changed: ', '', 1))}"
    if tool == "calendar:deleted":
        return f"Deleted a calendar event: {_short(target.replace('Deleted: ', '', 1))}"
    if short == "navigate":
        return f"Opened a web page: {_short(target)}"
    if short == "javascript_tool":
        return "Ran a script inside a web page"
    if short in ("computer", "browser_batch") or "Browser" in tool or "chrome" in tool.lower():
        acts = [a.strip() for a in target.split(",") if a.strip()] if target else []
        if acts:
            return "Used the browser: " + ", ".join(_browser_verb(a) for a in acts[:4]) + ("…" if len(acts) > 4 else "")
        return "Used the browser"
    if short in ("preview_start", "preview_stop"):
        return "Started or stopped a local test server"
    if short == "write_clipboard":
        return "Put text on the clipboard"
    if short in ("open_application",):
        return f"Opened an app: {_short(target)}" if target else "Opened an app"
    if short in ("key", "left_click", "double_click", "right_click", "type", "scroll", "left_click_drag"):
        return f"Controlled the screen: {_browser_verb(short)}"
    if short in ("create_scheduled_task", "update_scheduled_task"):
        return "Created or changed a scheduled task"
    if short == "allow_cowork_file_delete":
        return "Allowed files to be deleted"
    if tool.startswith("declared:") and target:
        return _short(target)
    if extra.get("amount") is not None and target:
        return f"Moved money: {_short(target)}"
    if target:
        return f"{short}: {_short(target)}"
    return short or "Did something not described"


def _browser_verb(action: str) -> str:
    return {
        "navigate": "opened a page", "left_click": "clicked", "double_click": "double-clicked",
        "right_click": "right-clicked", "type": "typed", "key": "pressed keys", "scroll": "scrolled",
        "left_click_drag": "dragged", "javascript_tool": "ran a page script", "form_input": "filled a form",
        "computer": "controlled the page", "browser_batch": "did several things",
    }.get(action, action.replace("_", " "))


LEADING_CD = re.compile(r'^\s*cd\s+(?:"[^"]*"|\'[^\']*\'|\S+)\s*(?:&&|;)\s*')


def _without_leading_cd(command: str) -> str:
    """`cd "/some/project" && real command` -> `real command` (the project is shown elsewhere)."""
    return LEADING_CD.sub("", command, count=1) or command


HEREDOC = re.compile(r"\s*<<-?\s*['\"]?\w+['\"]?")


def _describe_command(command: str) -> str:
    command = _without_leading_cd(command)
    suffix = ""
    m = HEREDOC.search(command)
    if m:                                    # `python - <<'EOF' …`: describe the command, not the script body
        command, suffix = command[:m.start()], " (with an inline script)"
    try:
        lex = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        # Unbalanced quotes (sed scripts and the like): fall back to the raw text.
        return f"Ran a command: {_short(command)}{suffix}"
    parts, segment = [], []
    for tok in tokens + [";"]:
        if tok in SEPARATORS:
            if segment:
                phrase = _describe_segment(segment)
                if phrase and phrase not in parts:
                    parts.append(phrase)
            segment = []
        else:
            segment.append(tok)
    if not parts:
        return f"Ran a command: {_short(command)}{suffix}"
    text = "; ".join(parts[:MAX_PARTS])
    if len(parts) > MAX_PARTS:
        text += f"; and {len(parts) - MAX_PARTS} more"
    return text[0].upper() + text[1:] + suffix


def _describe_segment(tokens):
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return None
    prog = tokens[0].rsplit("/", 1)[-1]
    args = tokens[1:]
    if prog in SKIP:
        return _describe_segment(args) if prog in ("sudo", "nohup", "time") else None
    if prog == "git":
        sub = next((a for a in args if not a.startswith("-")), "")
        return {
            "commit": "committed changes to git", "add": "staged files for a git commit",
            "push": "pushed code to the remote repository", "pull": "pulled code from the remote repository",
            "fetch": "fetched from the remote repository", "clone": "cloned a repository",
            "checkout": "switched git branch or restored files", "switch": "switched git branch",
            "merge": "merged git branches", "rebase": "rebased git history", "reset": "reset git state",
            "stash": "stashed changes in git", "init": "created a git repository", "mv": "moved files in git",
            "rm": "removed files from git", "tag": "tagged a git commit", "branch": "changed git branches",
            "clean": "deleted untracked files", "revert": "reverted a git commit", "restore": "restored files from git",
        }.get(sub, f"ran git {sub}".strip())
    if prog in ("rm", "rmdir"):
        return "deleted " + _list(args, "files")
    if prog == "mkdir":
        return "created the folder " + _list(args, "")
    if prog in ("cp",):
        return "copied " + _list(args[:-1], "files") + (f" to {args[-1]}" if len(args) > 1 else "")
    if prog == "mv":
        return "moved " + _list(args[:-1], "files") + (f" to {args[-1]}" if len(args) > 1 else "")
    if prog == "touch":
        return "created " + _list(args, "a file")
    if prog in ("chmod", "chown"):
        return "changed permissions on " + _list([a for a in args if not a.startswith("-") and not re.match(r"^[0-7+\-=rwxugo]+$", a)], "files")
    if prog in ("pip", "pip3") or (prog.startswith("python") and args[:2] == ["-m", "pip"]):
        a = args[2:] if prog.startswith("python") else args
        verb = a[0] if a else "ran"
        pkgs = [x for x in a[1:] if not x.startswith("-")]
        return f"{'installed' if verb == 'install' else verb} Python package{'s' if len(pkgs) != 1 else ''} " + (", ".join(pkgs) if pkgs else "from a requirements file")
    if prog in ("npm", "npx", "yarn", "pnpm", "brew"):
        sub = args[0] if args else ""
        if sub in ("install", "i", "add"):
            pkgs = [x for x in args[1:] if not x.startswith("-")]
            return f"installed {prog} package{'s' if len(pkgs) != 1 else ''} " + (", ".join(pkgs) if pkgs else "from the project")
        if sub in ("run", "start", "test", "build", "dev"):
            return f"ran {prog} {sub}" + (f" {args[1]}" if sub == "run" and len(args) > 1 else "")
        if sub == "publish":
            return "published a package"
        return f"ran {prog} {sub}".strip()
    if prog.startswith("python") or prog in ("node", "ruby", "swift", "bash", "zsh", "sh"):
        script = next((a for a in args if not a.startswith("-")), None)
        if args[:1] == ["-c"] or (script is None):
            return f"ran a {prog} snippet"
        return f"ran the script {_name(script)}"
    if prog == "curl":
        url = next((a for a in args if a.startswith("http")), None)
        host = re.sub(r"^https?://([^/]+).*$", r"\1", url) if url else "a server"
        if any(a in ("-X", "--request", "-d", "--data", "-F", "-T", "--upload-file") for a in args):
            return f"sent data to {host}"
        if any(a in ("-o", "-O", "--output") or a.startswith("-o") for a in args):
            return f"downloaded a file from {host}"
        return f"fetched from {host}"
    if prog in ("pkill", "kill", "killall"):
        return "stopped a running program"
    if prog == "open":
        return "opened " + _list([a for a in args if not a.startswith("-")], "something")
    if prog == "sqlite3":
        sql = " ".join(args)
        if re.search(r"\b(delete|drop)\b", sql, re.I):
            return "deleted data from a database"
        if re.search(r"\b(insert|update|create|alter)\b", sql, re.I):
            return "changed a database"
        return "queried a database"
    if prog in ("ssh", "scp", "rsync"):
        return f"used {prog} to reach another machine"
    if prog == "gh":
        return "did something on GitHub (gh " + " ".join(args[:2]) + ")"
    if prog in ("sed", "awk", "find", "defaults"):
        return f"ran {prog}"
    if prog in ("pytest", "make", "cargo", "go", "docker", "kubectl", "terraform"):
        return f"ran {prog} {args[0] if args and not args[0].startswith('-') else ''}".strip()
    return f"ran {prog}"


def _list(items, fallback: str, n: int = 2) -> str:
    items = [i for i in items if not i.startswith("-")]
    if not items:
        return fallback
    shown = ", ".join(_name(i) for i in items[:n])
    return shown + (f" and {len(items) - n} more" if len(items) > n else "")


def _name(path) -> str:
    if not path:
        return ""
    path = str(path)
    if path.startswith("http"):
        return path
    return Path(path).name or path


def _relative(path, cwd) -> str:
    if not path:
        return "a file"
    if cwd and str(path).startswith(str(cwd).rstrip("/") + "/"):
        return str(path)[len(str(cwd).rstrip("/")) + 1:]
    return Path(path).name if len(str(path)) > 60 else str(path)


def _short(text, n: int = 80) -> str:
    text = str(text).replace("\n", " ")
    return text if len(text) <= n else text[:n] + "…"
