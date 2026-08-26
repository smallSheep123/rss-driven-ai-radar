from radar.rss import parse_feed_bytes

RSS = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>T</title>
<item><guid>a1</guid><title>Hello Blender</title><link>https://example.com/a1</link><description>Summary</description><pubDate>Mon, 24 Aug 2026 10:00:00 GMT</pubDate></item>
</channel></rss>'''


def test_parse_feed_bytes():
    items = parse_feed_bytes(RSS)
    assert len(items) == 1
    assert items[0]["guid"] == "a1"
    assert items[0]["title"] == "Hello Blender"
    assert items[0]["url"] == "https://example.com/a1"
    assert items[0]["summary"] == "Summary"
