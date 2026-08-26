from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import xml.etree.ElementTree as ET

import httpx

from .db import ensure_feed, get_feed_http_state, mark_feed_result, upsert_article


def _clean_text(value: str | None) -> str:
    if not value:
        return ""
    return html.unescape(value).strip()


def _parse_date(raw: str | None) -> str | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
    except Exception:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _inner_xml_text(node: ET.Element | None) -> str:
    if node is None:
        return ""
    parts: list[str] = []
    if node.text:
        parts.append(node.text)
    for child in list(node):
        parts.append(ET.tostring(child, encoding="unicode", method="html"))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts).strip()


def _tag_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_feed_bytes(content: bytes) -> list[dict]:
    root = ET.fromstring(content)
    local = _tag_local(root.tag).lower()
    out: list[dict] = []

    if local == "rss" or root.find("channel") is not None:
        channel = root.find("channel")
        if channel is None:
            return []
        for item in channel.findall("item"):
            title = _clean_text(item.findtext("title")) or "(untitled)"
            link = _clean_text(item.findtext("link"))
            if not link:
                continue
            guid = _clean_text(item.findtext("guid")) or None
            desc = item.find("description")
            content_encoded = None
            for child in item:
                if _tag_local(child.tag).lower() == "encoded":
                    content_encoded = child
                    break
            summary = _inner_xml_text(content_encoded) or _inner_xml_text(desc)
            published = item.findtext("pubDate") or item.findtext("date")
            out.append({
                "guid": guid,
                "url": link,
                "title": title,
                "summary": summary,
                "published_at": _parse_date(published),
            })
        return out

    if local == "feed":
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}", 1)[0] + "}"
        for entry in root.findall(f"{ns}entry"):
            title = _clean_text(entry.findtext(f"{ns}title")) or "(untitled)"
            link = ""
            for link_node in entry.findall(f"{ns}link"):
                rel = link_node.attrib.get("rel", "alternate")
                href = link_node.attrib.get("href", "")
                if href and rel in {"alternate", ""}:
                    link = href.strip()
                    break
            if not link:
                continue
            guid = _clean_text(entry.findtext(f"{ns}id")) or None
            summary_node = entry.find(f"{ns}summary") or entry.find(f"{ns}content")
            summary = _inner_xml_text(summary_node)
            published = entry.findtext(f"{ns}published") or entry.findtext(f"{ns}updated")
            out.append({
                "guid": guid,
                "url": link,
                "title": title,
                "summary": summary,
                "published_at": _parse_date(published),
            })
        return out

    raise ValueError(f"unsupported feed root: {root.tag}")


def _fetch_one(url: str, *, user_agent: str, timeout: int, etag: str | None, last_modified: str | None) -> dict:
    headers = {
        "User-Agent": user_agent,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        if response.status_code == 304:
            return {"not_modified": True, "entries": [], "etag": etag, "last_modified": last_modified}
        response.raise_for_status()
        return {
            "not_modified": False,
            "entries": parse_feed_bytes(response.content),
            "etag": response.headers.get("etag"),
            "last_modified": response.headers.get("last-modified"),
        }


def update_feeds(conn, feeds: list[dict], *, user_agent: str, timeout: int, workers: int = 8) -> dict:
    prepared = []
    for feed in feeds:
        feed_id = ensure_feed(
            conn,
            name=str(feed.get("name") or feed["url"]),
            url=str(feed["url"]),
            category=str(feed.get("category") or ""),
        )
        etag, modified = get_feed_http_state(conn, feed_id)
        prepared.append((feed, feed_id, etag, modified))

    stats = {"feeds": len(prepared), "not_modified": 0, "feed_errors": 0, "entries": 0, "new_articles": 0, "updated_articles": 0}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_fetch_one, str(feed["url"]), user_agent=user_agent, timeout=timeout, etag=etag, last_modified=modified): (feed, feed_id)
            for feed, feed_id, etag, modified in prepared
        }
        for future in as_completed(futures):
            _, feed_id = futures[future]
            try:
                result = future.result()
                if result["not_modified"]:
                    stats["not_modified"] += 1
                    mark_feed_result(conn, feed_id, ok=True)
                    continue
                for entry in result["entries"]:
                    _, is_new = upsert_article(conn, feed_id=feed_id, **entry)
                    stats["entries"] += 1
                    stats["new_articles" if is_new else "updated_articles"] += 1
                mark_feed_result(conn, feed_id, ok=True, etag=result.get("etag"), last_modified=result.get("last_modified"))
            except Exception as exc:
                stats["feed_errors"] += 1
                mark_feed_result(conn, feed_id, ok=False, error=str(exc))
    return stats
