from __future__ import annotations

from .ai import AIClient
from .db import cleanup
from .fulltext import fetch_selected_fulltext
from .rss import update_feeds


def score_unscored(conn, settings) -> dict:
    ai = settings.raw.get("ai", {})
    if not ai.get("enabled", False):
        return {"enabled": False, "scored": 0, "selected": 0}

    rows = conn.execute(
        """SELECT a.id,a.title,a.summary,a.url,f.name AS source,f.category
           FROM articles a JOIN feeds f ON f.id=a.feed_id
           WHERE a.ai_score IS NULL
           ORDER BY COALESCE(a.published_at,a.fetched_at) DESC"""
    ).fetchall()
    if not rows:
        return {"enabled": True, "scored": 0, "selected": 0}

    timeout = int(settings.raw.get("app", {}).get("request_timeout_seconds", 20)) * 3
    client = AIClient(
        base_url=str(ai.get("api_base_url", "")),
        api_key=settings.api_key,
        model=str(ai.get("model", "")),
        system_prompt=str(ai.get("system_prompt", "")),
        timeout=timeout,
    )
    threshold = float(ai.get("threshold", 7))
    batch_size = max(1, int(ai.get("batch_size", 20)))
    max_summary = max(0, int(ai.get("max_summary_chars", 1200)))
    scored = selected = 0

    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        payload = [
            {
                "id": str(row["id"]),
                "title": row["title"],
                "summary": row["summary"][:max_summary],
                "source": row["source"],
                "category": row["category"],
                "url": row["url"],
            }
            for row in batch
        ]
        results = client.score_batch(payload)
        by_id = {str(x.get("id")): x for x in results if x.get("id") is not None}
        for row in batch:
            item = by_id.get(str(row["id"]))
            if not item:
                continue
            try:
                score = max(0.0, min(10.0, float(item.get("score", 0))))
            except Exception:
                score = 0.0
            fetch_full = bool(item.get("fetch_full", False))
            is_selected = score >= threshold and fetch_full
            conn.execute(
                "UPDATE articles SET ai_score=?,ai_topic=?,ai_reason=?,selected=? WHERE id=?",
                (
                    score,
                    str(item.get("topic", ""))[:200],
                    str(item.get("reason", ""))[:2000],
                    1 if is_selected else 0,
                    row["id"],
                ),
            )
            scored += 1
            selected += int(is_selected)
        conn.commit()
    return {"enabled": True, "scored": scored, "selected": selected}


def cleanup_from_settings(conn, settings) -> dict:
    r = settings.raw.get("retention", {})
    return cleanup(
        conn,
        articles_days=int(r.get("articles_days", 3)),
        selected_days=int(r.get("selected_days", 14)),
        keep_pinned_forever=bool(r.get("keep_pinned_forever", True)),
        run_logs_days=int(r.get("run_logs_days", 30)),
    )


def run_pipeline(conn, settings, feeds: list[dict]) -> dict:
    app = settings.raw.get("app", {})
    stats: dict = {}
    stats["rss"] = update_feeds(
        conn,
        feeds,
        user_agent=str(app.get("user_agent", "RSSDrivenAIRadar/0.1")),
        timeout=int(app.get("request_timeout_seconds", 20)),
        workers=int(app.get("max_feed_workers", 8)),
    )
    stats["ai"] = score_unscored(conn, settings)

    ft = settings.raw.get("fulltext", {})
    if ft.get("enabled", True) and ft.get("fetch_only_selected", True):
        stats["fulltext"] = fetch_selected_fulltext(
            conn,
            user_agent=str(app.get("user_agent", "RSSDrivenAIRadar/0.1")),
            timeout=int(app.get("request_timeout_seconds", 20)),
            max_chars=int(ft.get("max_chars", 50000)),
            min_chars=int(ft.get("min_chars", 300)),
            workers=int(app.get("max_fulltext_workers", 4)),
        )

    if settings.raw.get("retention", {}).get("cleanup_after_run", True):
        stats["cleanup"] = cleanup_from_settings(conn, settings)
    return stats
