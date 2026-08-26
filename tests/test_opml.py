from pathlib import Path
from radar.opml import parse_opml


def test_parse_opml(tmp_path: Path):
    p = tmp_path / "x.opml"
    p.write_text('''<opml version="2.0"><body><outline text="Blender"><outline text="BN" xmlUrl="https://x/feed"/></outline></body></opml>''', encoding="utf-8")
    feeds = parse_opml(p)
    assert feeds == [{"name": "BN", "url": "https://x/feed", "category": "Blender", "enabled": True}]
