from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET
import yaml


def parse_opml(path: str | Path) -> list[dict]:
    root = ET.parse(path).getroot()
    body = root.find("body")
    if body is None:
        return []
    feeds: list[dict] = []

    def walk(node, category: str = "") -> None:
        for child in node.findall("outline"):
            xml_url = child.attrib.get("xmlUrl")
            text = child.attrib.get("text") or child.attrib.get("title") or ""
            if xml_url:
                feeds.append({"name": text or xml_url, "url": xml_url, "category": category, "enabled": True})
            else:
                walk(child, text or category)

    walk(body)
    return feeds


def write_feeds_yaml(feeds: list[dict], output: str | Path) -> None:
    with Path(output).open("w", encoding="utf-8") as f:
        yaml.safe_dump({"feeds": feeds}, f, allow_unicode=True, sort_keys=False)
