"""Progress records for work started from the UI.

One row per button press. The caller inserts it, the spawned process updates
`step` as it goes, and whoever finishes sets the terminal status. The browser
polls rather than holding a request open.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

# A job still 'running' after this long lost its process — a container restart,
# a killed shell, an unhandled crash before the handler could record it. Without
# this, one dead job would block every future one via the in-flight guard.
STALE_MINUTES = 45


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def start(conn: sqlite3.Connection, kind: str, target: str | None = None,
          step: str | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO jobs (kind, target, status, step, started_at) "
        "VALUES (?, ?, 'running', ?, ?)",
        (kind, target, step, _now()),
    )
    conn.commit()
    return int(cur.lastrowid)


def set_step(conn: sqlite3.Connection, job_id: int | None, text: str) -> None:
    """Record the current phase. No-op when there is no job, so the pipeline
    scripts stay runnable straight from a shell."""
    if job_id is None:
        return
    conn.execute("UPDATE jobs SET step = ? WHERE id = ?", (text, job_id))
    conn.commit()


def finish(conn: sqlite3.Connection, job_id: int | None, status: str,
           detail: str | None = None) -> None:
    if job_id is None:
        return
    conn.execute(
        "UPDATE jobs SET status = ?, detail = ?, finished_at = ?, step = NULL "
        "WHERE id = ?",
        (status, detail, _now(), job_id),
    )
    conn.commit()


def active(conn: sqlite3.Connection, kind: str | None = None,
           target: str | None = None) -> sqlite3.Row | None:
    """The newest running job, optionally narrowed to a kind or target."""
    sql = "SELECT * FROM jobs WHERE status = 'running'"
    args: list = []
    if kind:
        sql += " AND kind = ?"
        args.append(kind)
    if target:
        sql += " AND target = ?"
        args.append(target)
    sql += " ORDER BY id DESC LIMIT 1"
    return conn.execute(sql, args).fetchone()


def expire_stale(conn: sqlite3.Connection, minutes: int = STALE_MINUTES) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(
        timespec="seconds"
    )
    cur = conn.execute(
        "UPDATE jobs SET status = 'error', finished_at = ?, step = NULL, "
        "detail = COALESCE(detail, 'process vanished before reporting a result') "
        "WHERE status = 'running' AND started_at < ?",
        (_now(), cutoff),
    )
    conn.commit()
    return cur.rowcount
