from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import load_feeds, load_settings
from .db import connect, finish_run, start_run
from .opml import parse_opml, write_feeds_yaml
from .rss import update_feeds
from .service import cleanup_from_settings, run_pipeline, score_unscored


def print_json(obj, indent: int = 2) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=indent, default=str))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rss-radar", description="RSS-driven AI Radar")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--feeds", default="feeds.yaml")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    for name in ("init-db", "update", "run", "score", "cleanup", "stats", "doctor", "feeds"):
        sub.add_parser(name)

    latest = sub.add_parser("latest")
    latest.add_argument("--limit", type=int, default=30)
    latest.add_argument("--category")
    latest.add_argument("--selected", action="store_true")

    article = sub.add_parser("article")
    article.add_argument("id", type=int)

    for name in ("pin", "unpin"):
        x = sub.add_parser(name)
        x.add_argument("id", type=int)

    opml = sub.add_parser("import-opml")
    opml.add_argument("path")
    opml.add_argument("--out", default="feeds.yaml")
    return p


def run_with_log(conn, kind: str, fn):
    run_id = start_run(conn, kind)
    try:
        stats = fn()
        finish_run(conn, run_id, stats)
        print_json(stats)
    except Exception as exc:
        finish_run(conn, run_id, {}, str(exc))
        raise


def main() -> None:
    args = parser().parse_args()

    if args.cmd == "import-opml":
        feeds = parse_opml(args.path)
        write_feeds_yaml(feeds, args.out)
        print_json({"ok": True, "imported": len(feeds), "output": str(Path(args.out).resolve())})
        return

    settings = load_settings(args.config)
    conn = connect(settings.db_path)

    if args.cmd == "init-db":
        print_json({"ok": True, "db": str(settings.db_path)})
        return

    if args.cmd == "doctor":
        ai = settings.raw.get("ai", {})
        checks = {
            "python": sys.version.split()[0],
            "db_writable": settings.db_path.parent.exists() and os_access_writable(settings.db_path.parent),
            "config_exists": Path(args.config).exists(),
            "feeds_exists": Path(args.feeds).exists(),
            "ai_enabled": bool(ai.get("enabled", False)),
            "ai_key_present": bool(settings.api_key) if ai.get("enabled", False) else None,
            "curl_or_http_client": bool(shutil.which("curl")) or True,
        }
        base_ok = checks["db_writable"] and checks["config_exists"] and checks["feeds_exists"]
        ai_ok = (checks["ai_key_present"] is not False)
        checks["ok"] = bool(base_ok and ai_ok)
        print_json(checks)
        return

    if args.cmd == "feeds":
        rows = conn.execute("SELECT * FROM feeds ORDER BY category,name").fetchall()
        print_json([dict(r) for r in rows])
        return

    if args.cmd == "update":
        feeds = load_feeds(args.feeds)
        app = settings.raw.get("app", {})
        def work():
            stats = update_feeds(
                conn,
                feeds,
                user_agent=str(app.get("user_agent", "RSSDrivenAIRadar/0.1")),
                timeout=int(app.get("request_timeout_seconds", 20)),
                workers=int(app.get("max_feed_workers", 8)),
            )
            if settings.raw.get("retention", {}).get("cleanup_after_run", True):
                stats["cleanup"] = cleanup_from_settings(conn, settings)
            return stats
        run_with_log(conn, "update", work)
        return

    if args.cmd == "score":
        run_with_log(conn, "score", lambda: score_unscored(conn, settings))
        return

    if args.cmd == "run":
        feeds = load_feeds(args.feeds)
        run_with_log(conn, "pipeline", lambda: run_pipeline(conn, settings, feeds))
        return

    if args.cmd == "cleanup":
        print_json(cleanup_from_settings(conn, settings))
        return

    if args.cmd == "stats":
        result = {
            "feeds": conn.execute("SELECT COUNT(*) c FROM feeds").fetchone()["c"],
            "feeds_with_errors": conn.execute("SELECT COUNT(*) c FROM feeds WHERE consecutive_failures>0").fetchone()["c"],
            "articles": conn.execute("SELECT COUNT(*) c FROM articles").fetchone()["c"],
            "selected": conn.execute("SELECT COUNT(*) c FROM articles WHERE selected=1").fetchone()["c"],
            "with_fulltext": conn.execute("SELECT COUNT(*) c FROM articles WHERE fulltext IS NOT NULL").fetchone()["c"],
            "pinned": conn.execute("SELECT COUNT(*) c FROM articles WHERE pinned=1").fetchone()["c"],
            "last_run": dict(conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone() or {}),
            "db": str(settings.db_path),
        }
        print_json(result)
        return

    if args.cmd == "latest":
        q = """SELECT a.id,a.title,a.url,a.summary,a.published_at,a.fetched_at,a.ai_score,a.ai_topic,a.ai_reason,a.selected,a.pinned,
                      f.name AS source,f.category
               FROM articles a JOIN feeds f ON f.id=a.feed_id WHERE 1=1"""
        params: list = []
        if args.category:
            q += " AND f.category=?"
            params.append(args.category)
        if args.selected:
            q += " AND a.selected=1"
        q += " ORDER BY COALESCE(a.published_at,a.fetched_at) DESC LIMIT ?"
        params.append(max(1, args.limit))
        print_json([dict(r) for r in conn.execute(q, params).fetchall()])
        return

    if args.cmd == "article":
        row = conn.execute(
            """SELECT a.*,f.name AS source,f.category FROM articles a JOIN feeds f ON f.id=a.feed_id WHERE a.id=?""",
            (args.id,),
        ).fetchone()
        print_json(dict(row) if row else {"error": "not found"})
        return

    if args.cmd in {"pin", "unpin"}:
        value = 1 if args.cmd == "pin" else 0
        conn.execute("UPDATE articles SET pinned=? WHERE id=?", (value, args.id))
        conn.commit()
        print_json({"ok": True, "id": args.id, "pinned": bool(value)})
        return


def os_access_writable(path: Path) -> bool:
    try:
        probe = path / ".radar-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    main()
