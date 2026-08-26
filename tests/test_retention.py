from datetime import datetime, timezone, timedelta
from pathlib import Path

from radar.db import connect, ensure_feed, upsert_article, cleanup


def test_retention_keeps_recent_selected_and_pinned(tmp_path: Path):
    conn = connect(tmp_path / "r.db")
    feed_id = ensure_feed(conn, name="x", url="https://x/feed", category="AI")
    now = datetime(2026, 8, 26, tzinfo=timezone.utc)

    ids = {}
    for name, days in [("old", 4), ("selected", 4), ("pinned", 10), ("recent", 1)]:
        article_id, _ = upsert_article(
            conn,
            feed_id=feed_id,
            guid=name,
            url=f"https://x/{name}",
            title=name,
            summary="",
            published_at=(now - timedelta(days=days)).isoformat(),
        )
        ids[name] = article_id
    conn.execute("UPDATE articles SET selected=1 WHERE id=?", (ids["selected"],))
    conn.execute("UPDATE articles SET pinned=1 WHERE id=?", (ids["pinned"],))
    conn.commit()

    result = cleanup(
        conn,
        articles_days=3,
        selected_days=14,
        keep_pinned_forever=True,
        run_logs_days=30,
        now=now,
    )
    titles = {r["title"] for r in conn.execute("SELECT title FROM articles")}
    assert "old" not in titles
    assert "selected" in titles
    assert "pinned" in titles
    assert "recent" in titles
    assert result["deleted_normal"] == 1
