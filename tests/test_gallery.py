import os

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from gramdesk import gallery  # noqa: E402


def test_scan_ignores_symlinks_and_other_types(tmp_path):
    root = tmp_path / "Gramdesk"
    acc = root / "Pessoal"
    (acc / ".thumbs").mkdir(parents=True)
    (acc / "a_post_X.jpg").write_bytes(b"x")
    (acc / "b_post_Y.mp4").write_bytes(b"x")
    (acc / "notas.txt").write_text("x")
    (acc / ".thumbs" / "b_post_Y.jpg").write_bytes(b"x")
    secret = tmp_path / "fora.jpg"
    secret.write_bytes(b"x")
    try:
        os.symlink(secret, acc / "link.jpg")
        os.symlink(tmp_path, root / "escape")
    except OSError:  # Windows sem permissão de criar symlink
        pytest.skip("symlink indisponível")
    names = sorted(e.path.name for e in gallery.scan(root))
    assert names == ["a_post_X.jpg", "b_post_Y.mp4"]
    vid = next(e for e in gallery.scan(root) if e.video)
    assert vid.thumb.name == "b_post_Y.jpg" and vid.folder == "Pessoal"


def test_scan_missing_root(tmp_path):
    assert gallery.scan(tmp_path / "nada") == []


def test_research_username_parsing():
    from gramdesk import research

    assert research.clean_username("@profa.ana") == "profa.ana"
    assert research.clean_username("https://www.instagram.com/profa.ana/") == "profa.ana"
    assert research.clean_username("instagram.com/p/ABC/") is None
    assert research.clean_username("../etc") is None
    assert research.username_from_url("https://www.instagram.com/nasa/") == "nasa"
    assert research.username_from_url("https://www.instagram.com/p/XYZ/") is None


def test_research_cli_args():
    from gramdesk import app

    r = app._research_args(["--research", "nasa", "--rid", "ab" * 8, "--posts", "30", "--no-comments", "--videos"])
    assert r["username"] == "nasa" and r["rid"] == "ab" * 8
    assert r["opts"] == {"posts": 30, "comments": False, "highlights": True, "videos": True}
    assert app._research_args(["https://www.instagram.com/x/"]) is None


def test_scan_includes_profile_subfolders_from_browse(tmp_path):
    root = tmp_path / "Gramdesk"
    sub = root / "Pessoal" / "profa.ana"
    sub.mkdir(parents=True)
    (sub / "profa.ana_AAA111aaa.jpg").write_bytes(b"x")
    (root / "Pesquisas" / "perfil-1" / "media").mkdir(parents=True)
    (root / "Pesquisas" / "perfil-1" / "x.jpg").write_bytes(b"x")
    entries = gallery.scan(root)
    assert [(e.path.name, e.folder) for e in entries] == [("profa.ana_AAA111aaa.jpg", "Pessoal")]
