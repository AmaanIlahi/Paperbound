import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent / "ui" / "links.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_every_because_of_link_goes_to_its_own_page():
    """Runs the real inline script of web/index.html on a DOM stub and clicks each source link."""
    done = subprocess.run(["node", str(SCRIPT)], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
