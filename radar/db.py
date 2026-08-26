from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS feeds (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  url TEXT NOT NULL UNIQUE,
  category TEXT NOT NULL DEFAULT '',
  etag TEXT,
  last_modified TEXT,
  last_checked_at TEXT,
  last_success_at TEXT,
  last_error TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
  guid TEXT,
  url TEXT NOT NULL,
  canonical_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  published_at TEXT,
  fetched_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  ai_score REAL,
  ai_topic TEXT,
  ai_reason TEXT,
  selected INTEGER NOT NULL DEFAULT 0,
  pinned INTEGER NOT NULL DEFAULT 0,
  fulltext TEXT,
  fulltext_fetched_at TEXT,
  fulltext_error TEXT,
  content_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_articles_fetched ON articles(fetched_at);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at);
CREATE INDEX IF NOT EXISTS idx_articles_selected ON articles(selected);
CREATE INDEX IF NOT EXISTS idx_articles_score ON articles(ai_score);
CREATE INDEX IF NOT EXISTS idx_articles_feed ON articles(feed_id);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  stats_json TEXT NOT NULL DEFAULT '{}',
  error TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def canonical_key(guid: str | None, url: str) -> str:
    raw = (guid or "").strip() or url.strip()
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


def ensure_feed(conn: sqlite3.Connection, *, name: str, url: str, category: str) -> int:
    conn.execute(
        """INSERT INTO feeds(name,url,category) VALUES(?,?,?)
           ON CONFLICT(url) DO UPDATE SET name=excluded.name, category=excluded.category""",
        (name, url, category),
    )
    row = conn.execute("SELECT id FROM feeds WHERE url=?", (url,)).fetchone()
    conn.commit()
    return int(row["id"])


def get_feed_http_state(conn: sqlite3.Connection, feed_id: int) -> tuple[str | None, str | None]:
    row = conn.execute("SELECT etag,last_modified FROM feeds WHERE id=?", (feed_id,)).fetchone()
    return (row["etag"], row["last_modified"]) if row else (None, None)


def mark_feed_result(conn: sqlite3.Connection, feed_id: int, *, ok: bool, etag: str | None = None, last_modified: str | None = None, error: str | None = None) -> None:
    checked = now_iso()
    if ok:
        conn.execute(
            """UPDATE feeds SET last_checked_at=?,last_success_at=?,last_error=NULL,
               consecutive_failures=0,etag=COALESCE(?,etag),last_modified=COALESCE(?,last_modified)
               WHERE id=?""",
            (checked, checked, etag, last_modified, feed_id),
        )
    else:
        conn.execute(
            """UPDATE feeds SET last_checked_at=?,last_error=?,
               consecutive_failures=consecutive_failures+1 WHERE id=?""",
            (checked, (error or "unknown error")[:2000], feed_id),
        )
    conn.commit()


def upsert_article(conn: sqlite3.Connection, *, feed_id: int, guid: str | None, url: str, title: str, summary: str, published_at: str | None) -> tuple[int, bool]:
    key = canonical_key(guid, url)
    now = now_iso()
    row = conn.execute("SELECT id,title,summary,url FROM articles WHERE canonical_key=?", (key,)).fetchone()
    if row:
        changed = row["title"] != title or row["summary"] != summary or row["url"] != url
        conn.execute(
            """UPDATE articles SET title=?,summary=?,url=?,guid=COALESCE(?,guid),
               published_at=COALESCE(?,published_at),updated_at=?,
               ai_score=CASE WHEN ? THEN NULL ELSE ai_score END,
               ai_topic=CASE WHEN ? THEN NULL ELSE ai_topic END,
               ai_reason=CASE WHEN ? THEN NULL ELSE ai_reason END,
               selected=CASE WHEN ? THEN 0 ELSE selected END
               WHERE id=?""",
            (title, summary, url, guid, published_at, now, changed, changed, changed, changed, row["id"]),
        )
        conn.commit()
        return int(row["id"]), False

    cur = conn.execute(
        """INSERT INTO articles(feed_id,guid,url,canonical_key,title,summary,published_at,fetched_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (feed_id, guid, url, key, title, summary, published_at, now, now),
    )
    conn.commit()
    return int(cur.lastrowid), True


def start_run(conn: sqlite3.Connection, kind: str) -> int:
    cur = conn.execute("INSERT INTO runs(kind,started_at) VALUES(?,?)", (kind, now_iso()))
    conn.commit()
    return int(cur.lastrowid)


def finish_run(conn: sqlite3.Connection, run_id: int, stats: dict, error: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=?,stats_json=?,error=? WHERE id=?",
        (now_iso(), json.dumps(stats, ensure_ascii=False), error, run_id),
    )
    conn.commit()


def cleanup(conn: sqlite3.Connection, *, articles_days: int, selected_days: int, keep_pinned_forever: bool, run_logs_days: int, now: datetime | None = None) -> dict:
    now = now or utcnow()
    normal_cut = (now - timedelta(days=articles_days)).isoformat()
    selected_cut = (now - timedelta(days=selected_days)).isoformat()
    runs_cut = (now - timedelta(days=run_logs_days)).isoformat()
    pin_filter = "AND pinned=0" if keep_pinned_forever else ""

    cur1 = conn.execute(
        f"""DELETE FROM articles WHERE selected=0 {pin_filter}
             AND COALESCE(published_at,fetched_at) < ?""",
        (normal_cut,),
    )
    cur2 = conn.execute(
        f"""DELETE FROM articles WHERE selected=1 {pin_filter}
             AND COALESCE(published_at,fetched_at) < ?""",
        (selected_cut,),
    )
    cur3 = conn.execute("DELETE FROM runs WHERE started_at < ?", (runs_cut,))
    conn.commit()
    return {"deleted_normal": cur1.rowcount, "deleted_selected": cur2.rowcount, "deleted_runs": cur3.rowcount}
