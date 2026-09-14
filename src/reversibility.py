"""Say whether an action can be undone, and why. Rules, not guesses.

Returns (reversible, reason) where reversible is 1, 0, or None (not assessed).
Only claims "yes" or "no" when a rule clearly applies; everything else is None.
"""

import re
import shlex
from pathlib import Path

# Shell programs whose effect can be undone with another local command.
REVERSIBLE = {
    "mkdir": "directory can be removed",
    "touch": "file can be removed",
    "cp": "copy can be deleted",
    "mv": "can be moved back",
    "chmod": "permissions can be reset",
    "ln": "link can be removed",
    "open": "only opens an app or file",
    "pip": "package can be uninstalled",
    "pip3": "package can be uninstalled",
    "npm": "package can be uninstalled",
    "npx": "runs a local tool",
    "brew": "package can be uninstalled",
    "defaults": "setting can be written back",
}
# Shell programs whose effect cannot be undone locally.
IRREVERSIBLE = {
    "rm": "deleted files are gone",
    "rmdir": "deleted directory is gone",
    "shred": "data is overwritten",
    "dd": "raw write to a device or file",
    "ssh": "ran a command on another machine",
    "scp": "copied to another machine",
    "rsync": "changed files on another machine or overwrote a tree",
    "gh": "changed something on GitHub, visible to others",
    "mail": "a message was sent",
    "sendmail": "a message was sent",
}
GIT_REVERSIBLE = {"add", "commit", "stash", "checkout", "switch", "restore", "init",
                  "tag", "merge", "rebase", "cherry-pick", "revert", "mv", "fetch", "pull"}
GIT_IRREVERSIBLE = {"push": "published to a remote, visible to others",
                    "clean": "untracked files were deleted"}
CURL_SENDS = {"-X", "--request", "-d", "--data", "--data-raw", "--data-binary",
              "--data-urlencode", "-F", "--form", "-T", "--upload-file"}
SEPARATORS = {"&&", "||", ";", ";;", "|", "&", "(", ")"}
# Segments that change nothing and should not affect the verdict.
NEUTRAL = {"cd", "echo", "printf", "true", "false", "sleep", "cat", "ls", "grep", "head",
           "tail", "wc", "sort", "uniq", "cut", "tr", "pwd", "test", "[", "xargs", "tee"}


def assess(action_type: str, tool: str, tool_input: dict, target, cwd):
    if action_type == "file_write":
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if path and _in_git_repo(Path(path)):
            return 1, "file is inside a git repository"
        return None, None
    if action_type == "execute":
        return _assess_command(tool_input.get("command") or "")
    if tool == "SendUserFile":
        return 0, "a file was handed to the user; cannot be unsent"
    if tool == "Artifact":
        action = tool_input.get("action")
        if action in (None, "publish", "pin", "unpin", "upload_asset", "write_db"):
            return 1, "can be unpublished, unpinned, or removed"
        if action in ("delete", "delete_asset", "reply", "resolve"):
            return 0, "deletion or a reply others can see"
        return None, None
    if tool.startswith("mcp__"):
        name = tool.split("__", 2)[-1]
        if name in ("preview_start", "preview_stop"):
            return 1, "a local server was started or stopped"
        return None, None
    return None, None


def _assess_command(command: str):
    """Worst segment wins: any irreversible part makes the whole command irreversible."""
    try:
        lex = shlex.shlex(command.replace("\n", " ; "), posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        return None, None
    verdicts = []
    segment = []
    for tok in tokens + [";"]:
        if tok in SEPARATORS:
            if segment and segment[0].rsplit("/", 1)[-1] not in NEUTRAL:
                verdicts.append(_assess_segment(segment))
            segment = []
        else:
            segment.append(tok)
    for verdict, reason in verdicts:
        if verdict == 0:
            return 0, reason
    if verdicts and all(v == 1 for v, _ in verdicts):
        return 1, "; ".join(dict.fromkeys(r for _, r in verdicts))
    return None, None


def _assess_segment(tokens: list):
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens = tokens[1:]
    if not tokens:
        return None, None
    prog = tokens[0].rsplit("/", 1)[-1]
    args = tokens[1:]
    if prog == "sudo" and args:
        return _assess_segment(args)
    if prog == "git":
        sub = next((a for a in args if not a.startswith("-")), None)
        if sub == "reset" and any(a in ("--hard", "--merge") for a in args):
            return 0, "git reset --hard discards uncommitted work"
        if sub == "branch" and any(a in ("-D", "-d", "--delete") for a in args):
            return 0, "branch deleted"
        if sub == "stash" and any(a in ("drop", "clear") for a in args):
            return 0, "stash dropped"
        if sub in GIT_IRREVERSIBLE:
            return 0, GIT_IRREVERSIBLE[sub]
        if sub in GIT_REVERSIBLE:
            return 1, "git keeps history; can be undone with git"
        if sub in ("status", "log", "diff", "show", "rev-parse", "ls-files", "blame"):
            return 1, "read-only git command"
        return None, None
    if prog == "curl":
        if any(a in CURL_SENDS for a in args):
            return 0, "data was sent to a server"
        return 1, "fetched only"
    if prog == "sqlite3":
        sql = " ".join(args)
        if re.search(r"\b(delete|drop|truncate)\b", sql, re.I):
            return 0, "rows or tables deleted"
        if re.search(r"\b(insert|update|create|alter)\b", sql, re.I):
            return 1, "rows can be changed back"
        return 1, "read-only query"
    if prog in ("pip", "pip3", "npm", "brew") and args and args[0] in ("uninstall", "remove", "rm"):
        return 1, "package can be reinstalled"
    if prog == "npm" and args and args[0] == "publish":
        return 0, "published to a registry"
    if prog in IRREVERSIBLE:
        return 0, IRREVERSIBLE[prog]
    if prog in REVERSIBLE:
        return 1, REVERSIBLE[prog]
    return None, None


def _in_git_repo(path: Path) -> bool:
    for parent in [path] + list(path.parents):
        if (parent / ".git").exists():
            return True
    return False
