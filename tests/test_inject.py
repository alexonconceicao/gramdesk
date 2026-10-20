import shutil
import subprocess
from importlib import resources

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="node ausente")
def test_inject_js_parses(tmp_path):
    src = resources.files("gramdesk").joinpath("inject.js").read_text(encoding="utf-8")
    src = src.replace("__GD_KEY__", "__gd_test").replace("__GD_GHOST__", "true")
    f = tmp_path / "inject.js"
    f.write_text(src, encoding="utf-8")
    subprocess.run(["node", "--check", str(f)], check=True)
