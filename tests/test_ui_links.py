import json
import shutil
import subprocess
from pathlib import Path

import pytest

from paperbound.demo import replay

UI = Path(__file__).parent / "ui"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_node(script: str, *args: str):
    return subprocess.run(["node", str(UI / script), *args], capture_output=True, text=True, timeout=60)


@needs_node
def test_every_because_of_link_goes_to_its_own_page():
    """Runs the real inline script of web/index.html on a DOM stub and clicks each source link."""
    done = run_node("links.test.js")
    assert done.returncode == 0, done.stdout + done.stderr


@needs_node
def test_demo_page_replays_the_recorded_events_and_approves_in_the_browser(tmp_path):
    """Feeds the real recorded events to the page: progressive reveal, narration, groups, no server calls on approve."""
    events = tmp_path / "events.json"
    events.write_text(json.dumps(replay.build_events(replay.load_snapshot("lease"))), encoding="utf-8")
    done = run_node("demo.test.js", str(events))
    assert done.returncode == 0, done.stdout + done.stderr
