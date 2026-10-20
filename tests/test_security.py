import pytest

from gramdesk import security as s


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.instagram.com/", True),
        ("https://instagram.com/p/abc/", True),
        ("https://www.facebook.com/login", True),
        ("http://www.instagram.com/", False),
        ("https://instagram.com.evil.io/", False),
        ("https://evilinstagram.com/", False),
        ("javascript:alert(1)", False),
    ],
)
def test_nav(url, expected):
    assert s.is_nav_allowed(url) is expected


def test_classify():
    kw = dict(block_third_party=True, block_telemetry=True, ghost=True)
    assert s.classify_request("https://scontent.cdninstagram.com/a.jpg", **kw) is None
    assert s.classify_request("https://www.google-analytics.com/x", **kw) == "third-party"
    assert s.classify_request("http://www.instagram.com/", **kw) == "insecure-scheme"
    assert s.classify_request("https://graph.instagram.com/logging_client_events", **kw) == "telemetry"
    assert s.classify_request("https://www.instagram.com/api/v1/stories/reel/seen", **kw) == "story-seen"
    assert s.classify_request("https://www.google.com/recaptcha/api.js", **kw) is None
    assert s.classify_request("https://www.google.com/search", **kw) == "third-party"
    assert s.classify_request("data:image/png;base64,AA", **kw) is None
    off = dict(block_third_party=False, block_telemetry=False, ghost=False)
    assert s.classify_request("https://www.instagram.com/api/v1/stories/reel/seen", **off) is None


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://scontent-gru1-1.cdninstagram.com/v/t51/123_n.jpg?stp=x", True),
        ("https://instagram.fssa1-1.fna.fbcdn.net/o1/v/t16/f2/m86/AQ.mp4?efg=1", True),
        ("http://scontent.cdninstagram.com/a.jpg", False),
        ("https://evil.com/a.jpg", False),
        ("https://scontent.cdninstagram.com/a.exe", False),
        ("https://user:pw@scontent.cdninstagram.com/a.jpg", False),
        ("https://scontent.cdninstagram.com:8443/a.jpg", False),
        (None, False),
    ],
)
def test_media_url(url, ok):
    assert s.is_safe_media_url(url) is ok


def test_filename():
    assert s.safe_filename("../../etc/passwd", ".jpg") == "etc_passwd.jpg"
    assert s.safe_filename("", ".mp4") == "midia.mp4"
    assert s.safe_filename("user_story_123", ".mp4") == "user_story_123.mp4"
    assert len(s.safe_filename("a" * 999, ".jpg")) == 124
    with pytest.raises(ValueError):
        s.safe_filename("x", ".sh")


def test_account_name():
    assert s.clean_account_name("  Pessoal\x00\n ") == "Pessoal"
    assert len(s.clean_account_name("x" * 100)) == 40
    with pytest.raises(ValueError):
        s.clean_account_name("\x01")


def test_ids():
    assert s.is_account_id(s.new_account_id())
    assert not s.is_account_id("../etc")
    assert s.is_request_id(s.new_request_id())


def test_password():
    rec = s.hash_password("segredo123")
    assert s.verify_password("segredo123", rec)
    assert not s.verify_password("errada", rec)
    assert not s.verify_password("x", None)
    assert not s.verify_password("x", {"alg": "scrypt", "salt": "zz", "hash": "zz"})


@pytest.mark.parametrize(
    "text,expected",
    [
        ("https://www.instagram.com/p/ABC123/?igsh=xyz", "https://www.instagram.com/p/ABC123/?igsh=xyz"),
        ("instagram.com/reel/XYZ/", "https://www.instagram.com/reel/XYZ/"),
        ("http://instagram.com/stories/fulano/123/", "https://www.instagram.com/stories/fulano/123/"),
        ("@nasa", "https://www.instagram.com/nasa/"),
        ("nasa", "https://www.instagram.com/nasa/"),
        ("fulano.silva", "https://www.instagram.com/fulano.silva/"),
        ("https://instagr.am/p/ABC/", "https://www.instagram.com/p/ABC/"),
        ("https://instagram.com.evil.io/p/x", None),
        ("https://evil.com/?u=instagram.com", None),
        ("javascript:alert(1)", None),
        ("https://user:pw@instagram.com/", None),
        ("dois termos", None),
        ("", None),
    ],
)
def test_parse_instagram_input(text, expected):
    assert s.parse_instagram_input(text) == expected
