"""Decide whether a shell command is read-only.

Conservative on purpose: a command is read-only only when every piece of it is
a known read-only program used in a read-only way. Anything unrecognised —
redirects, heredocs, loops, subshells we can't see into, unknown programs —
counts as a side effect. Wrong answers here are over-reporting, never gaps.
"""

import re
import shlex

READ_ONLY = {
    "cd", "ls", "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg",
    "wc", "echo", "printf", "pwd", "which", "type", "whoami", "id", "date", "env",
    "printenv", "file", "stat", "du", "df", "ps", "pgrep", "lsof", "uname", "sw_vers",
    "sleep", "true", "false", "test", "[", "sort", "uniq", "cut", "tr", "awk", "diff",
    "cmp", "jq", "xxd", "od", "strings", "basename", "dirname", "realpath", "readlink",
    "tree", "column", "nl", "tac", "rev", "md5", "shasum", "sha256sum", "hexdump",
    "defaults", "xargs", "sed", "find", "git", "sqlite3", "curl", "python3", "python",
}
# Programs above that need a closer look at their arguments.
GIT_READ_ONLY = {"status", "log", "diff", "show", "rev-parse", "ls-files", "blame",
                 "describe", "shortlog", "cat-file", "ls-tree", "reflog"}
SHELL_KEYWORDS = {"for", "while", "until", "if", "then", "else", "elif", "fi", "do",
                  "done", "case", "esac", "function", "select", "{", "}", "!"}
SEPARATORS = {"&&", "||", ";", ";;", "|", "&", "(", ")"}
HARMLESS_REDIRECTS = re.compile(r"(\d?>&\d|\d?>\s*/dev/null|&>\s*/dev/null|<\s*/dev/null)")
SQL_WRITE = re.compile(r"\b(insert|update|delete|create|drop|alter|replace|vacuum|attach)\b"
                       r"|pragma\s+(journal_mode|synchronous)", re.I)


def is_read_only(command: str) -> bool:
    if not command or not command.strip():
        return True
    text = HARMLESS_REDIRECTS.sub(" ", command)
    # Any remaining redirect, heredoc, backtick, or $( ) means we can't see
    # everything that runs. A ">" inside quotes trips this too — over-reporting
    # is the acceptable direction.
    if re.search(r"[<>]|`|\$\(", text):
        return False
    lex = shlex.shlex(text.replace("\n", " ; "), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError:
        return False
    segment = []
    for tok in tokens + [";"]:
        if tok in SEPARATORS:
            if segment and not _tokens_read_only(segment):
                return False
            segment = []
        else:
            segment.append(tok)
    return True


def _tokens_read_only(tokens: list) -> bool:
    tokens = list(tokens)
    while tokens and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tokens[0]):
        tokens.pop(0)
    if not tokens:
        return True
    prog = tokens[0].rsplit("/", 1)[-1]
    if prog in SHELL_KEYWORDS:
        return False
    if prog == "xargs":
        return _tokens_read_only(tokens[1:])
    if prog not in READ_ONLY:
        return False
    args = tokens[1:]
    if prog == "git":
        sub = next((a for a in args if not a.startswith("-")), None)
        if sub in GIT_READ_ONLY:
            return True
        if sub in ("branch", "remote", "tag", "stash", "config"):
            return _git_listing_only(sub, args)
        return False
    if prog == "find":
        return not any(a in ("-delete", "-exec", "-execdir", "-ok", "-okdir") for a in args)
    if prog == "sed":
        return not any(a.startswith("-i") for a in args)
    if prog == "sqlite3":
        return not SQL_WRITE.search(" ".join(args))
    if prog == "curl":
        return _curl_read_only(args)
    if prog in ("python", "python3"):
        return args[:1] in (["--version"], ["-V"])
    if prog == "defaults":
        return args[:1] == ["read"]
    return True


def _curl_read_only(args: list) -> bool:
    writes = {"-X", "--request", "-d", "--data", "--data-raw", "--data-binary",
              "--data-urlencode", "-F", "--form", "-T", "--upload-file", "-O", "--remote-name"}
    for i, a in enumerate(args):
        if a in writes:
            return False
        if a in ("-o", "--output"):
            if i + 1 >= len(args) or args[i + 1] != "/dev/null":
                return False
        elif a.startswith("-o") and a != "-o" and a[2:] != "/dev/null":
            return False
    return True


def _git_listing_only(sub: str, args: list) -> bool:
    rest = args[args.index(sub) + 1:]
    if sub == "stash":
        return rest[:1] == ["list"]
    if sub == "config":
        return any(a in ("--get", "--list", "-l", "--get-all") for a in rest)
    listing = {"-v", "-vv", "-a", "--all", "-l", "--list", "-r", "--show-current", "--verbose"}
    return all(a in listing for a in rest)
