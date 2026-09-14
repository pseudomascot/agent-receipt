"""Log integrity: prove the transcripts have not changed since the receipt read them.

At ingest time every consumed byte range is hashed (transcript_chunks). This
pass re-reads those ranges and compares. It cannot say a log was honest when
first read; it can say nothing in it was altered afterwards.
"""

import hashlib
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from store import DB_PATH, connect

CHUNK_READ = 1 << 20


def verify(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT path, start_offset, end_offset, sha256, first_seen FROM transcript_chunks "
        "ORDER BY path, start_offset"
    ).fetchall()
    by_path = {}
    for path, start, end, digest, first_seen in rows:
        by_path.setdefault(path, []).append((start, end, digest, first_seen))

    modified, missing, ok = [], [], 0
    for path, chunks in by_path.items():
        p = Path(path)
        if not p.exists():
            missing.append(path)
            continue
        size = p.stat().st_size
        bad = None
        with open(p, "rb") as f:
            for start, end, digest, first_seen in chunks:
                if end > size:
                    bad = f"shorter than when read (now {size} bytes, had read to {end})"
                    break
                h = hashlib.sha256()
                f.seek(start)
                remaining = end - start
                while remaining:
                    block = f.read(min(CHUNK_READ, remaining))
                    if not block:
                        break
                    h.update(block)
                    remaining -= len(block)
                if h.hexdigest() != digest:
                    bad = (f"bytes {start}-{end} differ from what was read on "
                           f"{datetime.fromtimestamp(first_seen).strftime('%Y-%m-%d %H:%M')}")
                    break
        if bad:
            modified.append({"path": path, "problem": bad})
        else:
            ok += 1

    result = {
        "checked_at": time.time(),
        "transcripts": len(by_path),
        "chunks": len(rows),
        "unchanged": ok,
        "modified": modified,
        "missing": missing,
        "bytes": sum(end - start for _, start, end, *_ in rows),
    }
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('integrity', ?)", (json.dumps(result),))
    conn.commit()
    return result


def last_result(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT value FROM meta WHERE key = 'integrity'").fetchone()
    return json.loads(row[0]) if row else None


def status_line(conn: sqlite3.Connection) -> str | None:
    """Text for pages and summaries, or None if no check has run yet."""
    result = last_result(conn)
    return describe(result) if result else None


def describe(result: dict | None) -> str:
    if not result:
        return "Log integrity: not checked yet."
    when = datetime.fromtimestamp(result["checked_at"]).strftime("%Y-%m-%d %H:%M")
    if not result["modified"] and not result["missing"]:
        return (f"Log integrity: checked {when} — all {result['transcripts']} transcripts "
                f"({result['bytes'] / 1048576:.0f} MB) unchanged since the receipt first read them.")
    parts = [f"Log integrity: checked {when} — {len(result['modified'])} transcript(s) MODIFIED"
             f" and {len(result['missing'])} missing since the receipt first read them."]
    for m in result["modified"][:5]:
        parts.append(f"  modified: {Path(m['path']).parent.name}/{Path(m['path']).name}: {m['problem']}")
    for path in result["missing"][:5]:
        parts.append(f"  missing: {Path(path).parent.name}/{Path(path).name}")
    return "\n".join(parts)


def run(db_path: Path = DB_PATH) -> None:
    conn = connect(db_path)
    started = time.time()
    result = verify(conn)
    print(describe(result))
    print(f"({result['chunks']} chunk(s) re-hashed in {time.time() - started:.1f}s)")
    conn.close()


if __name__ == "__main__":
    run()
