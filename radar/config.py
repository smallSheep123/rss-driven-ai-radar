from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import yaml


@dataclass(frozen=True)
class Settings:
    raw: dict
    base_dir: Path

    @property
    def db_path(self) -> Path:
        raw = self.raw.get("app", {}).get("db_path", "./data/radar.db")
        p = Path(raw)
        return p if p.is_absolute() else (self.base_dir / p).resolve()

    @property
    def api_key(self) -> str:
        env_name = self.raw.get("ai", {}).get("api_key_env", "RADAR_API_KEY")
        return os.getenv(env_name, "")


def load_yaml(path: str | Path) -> dict:
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_settings(path: str | Path) -> Settings:
    path = Path(path).resolve()
    return Settings(raw=load_yaml(path), base_dir=path.parent)


def load_feeds(path: str | Path) -> list[dict]:
    data = load_yaml(path)
    feeds = data.get("feeds", [])
    if not isinstance(feeds, list):
        raise ValueError("feeds.yaml: 'feeds' must be a list")
    out: list[dict] = []
    for feed in feeds:
        if not isinstance(feed, dict) or not feed.get("enabled", True):
            continue
        if not feed.get("url"):
            continue
        out.append(feed)
    return out
