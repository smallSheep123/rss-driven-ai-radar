from radar.fulltext import extract_text


def test_extract_text_prefers_article():
    html = '''<html><body><nav>noise</nav><article><h1>Title</h1><p>''' + ('Useful text ' * 40) + '''</p></article><footer>noise</footer></body></html>'''
    text = extract_text(html, max_chars=5000, min_chars=100)
    assert "Title" in text
    assert "Useful text" in text
    assert "noise" not in text
