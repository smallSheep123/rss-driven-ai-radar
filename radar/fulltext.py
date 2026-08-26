from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib

from bs4 import BeautifulSoup
import httpx

from .db import now_iso


NOISE_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form"}


def extract_text(html_doc: str, *, max_chars: int, min_chars: int) -> str:
    soup = BeautifulSoup(html_doc, "html.parser")
    for tag in soup.find_all(NOISE_TAGS):
        tag.decompose()
    node = soup.find("article") or soup.find("main") or soup.body or soup
    chunks = []
    for el in node.find_all(["h1", "h2", "h3", "p", "li", "blockquote"]):
        text = " ".join(el.stripped_strings)
        if text:
            chunks.append(text)
    text = "\n\n".join(chunks).strip()
    if len(text) < min_chars:
        text = " ".join(node.stripped_strings).strip()
    if len(text) < min_chars:
        raise ValueError(f"extracted text too short ({len(text)} chars)")
    return text[:max_chars]


def _fetch(url: str, *, user_agent: str, timeout: int, max_chars: int, min_chars: int) -> tuple[str | None, str | None]:
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers={"User-Agent": user_agent}) as client:
            response = client.get(url)
            response.raise_for_status()
        return extract_text(response.text, max_chars=max_chars, min_chars=min_chars), None
    except Exception as exc:
        return None, str(exc)[:2000]


def fetch_selected_fulltext(conn, *, user_agent: str, timeout: int, max_chars: int, min_chars: int, workers: int) -> dict:
    rows = conn.execute(
        """SELECT id,url FROM articles WHERE selected=1 AND fulltext IS NULL
           ORDER BY ai_score DESC, COALESCE(published_at,fetched_at) DESC"""
    ).fetchall()
    stats = {"requested": len(rows), "ok": 0, "failed": 0}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_fetch, row["url"], user_agent=user_agent, timeout=timeout, max_chars=max_chars, min_chars=min_chars): row
            for row in rows
        }
        for future in as_completed(futures):
            row = futures[future]
            text, error = future.result()
            if text:
                conn.execute(
                    "UPDATE articles SET fulltext=?,fulltext_fetched_at=?,fulltext_error=NULL,content_hash=? WHERE id=?",
                    (text, now_iso(), hashlib.sha256(text.encode("utf-8")).hexdigest(), row["id"]),
                )
                stats["ok"] += 1
            else:
                conn.execute("UPDATE articles SET fulltext_error=? WHERE id=?", (error, row["id"]))
                stats["failed"] += 1
            conn.commit()
    return stats
