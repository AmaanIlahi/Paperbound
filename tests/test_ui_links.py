"""The page (web/index.html, style.css, app.js) run under node on a DOM stub: the same renderer must handle a recorded
sample and a real upload's event stream, so both are played through it, plus the landing, upload and error states."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from paperbound.demo import replay
from paperbound.pipeline.classify import Classification
from tests.lease_fakes import TODAY
from tests.live import live  # noqa: F401
from tests.test_routing import recipe_pdf

UI = Path(__file__).parent / "ui"
WEB = Path(__file__).resolve().parent.parent / "web"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def run_node(script: str, *args: str):
    return subprocess.run(["node", str(UI / script), *args], capture_output=True, text=True, timeout=90)


def write_events(path, events) -> str:
    path.write_text(json.dumps([[name, data] for name, data in events]), encoding="utf-8")
    return str(path)


def sample_events(fixture: str):
    return [(name, data) for _, name, data in replay.build_events(replay.load_snapshot(fixture))]


@needs_node
def test_every_because_of_link_goes_to_its_own_page():
    done = run_node("links.test.js")
    assert done.returncode == 0, done.stdout + done.stderr


@needs_node
@pytest.mark.parametrize("fixture", ["lease", "insurance_certificate"])
def test_a_sample_plays_through_the_renderer_and_approves_in_the_browser(tmp_path, fixture):
    done = run_node("stream.test.js", write_events(tmp_path / "e.json", sample_events(fixture)), "sample", fixture)
    assert done.returncode == 0, done.stdout + done.stderr


@needs_node
def test_a_live_run_plays_through_the_same_renderer_and_approves_on_the_server(live, tmp_path):
    run_id = live.upload_and_run()["run_id"]
    events = [(name, data) for _, name, data in live.events(run_id)]
    done = run_node("stream.test.js", write_events(tmp_path / "e.json", events), "live", "lease")
    assert done.returncode == 0, done.stdout + done.stderr


@needs_node
def test_states_landing_upload_errors_and_unsupported_documents(live, tmp_path):
    live.use_pipeline(lambda: {"today": TODAY, "classifier": lambda pages: Classification("other", "llm", "A pancake recipe.")})
    other = [(n, d) for _, n, d in live.events(live.upload_and_run(recipe_pdf(), "recipe.pdf")["run_id"])]
    done = run_node("states.test.js", write_events(tmp_path / "other.json", other), write_events(tmp_path / "lease.json", sample_events("lease")))
    assert done.returncode == 0, done.stdout + done.stderr


# ---- layout and design, read straight from the files ----

def test_the_landing_page_has_what_the_brief_asks_for():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for text in ("Drop in any PDF. See what you owe, and by when.", "Supports leases and insurance certificates", "Try a sample",
                 "Files are deleted after 24 hours. Don't upload sensitive documents.", "How it works", "Evals", "GitHub"):
        assert text in html, text
    assert 'href="https://github.com/AmaanIlahi/Paperbound"' in html and 'href="/evals"' in html
    strip = re.findall(r'<li class="step-card"[^>]*>.*?</li>', html, re.S)
    assert len(strip) == 5
    labels = [re.search(r"<strong>(.*?)</strong>", s).group(1) for s in strip]
    assert labels == ["Read", "Extract", "Verify", "Decide", "Act"]
    tags = [re.search(r'<span class="tag [^"]*">(.*?)</span>', s).group(1) for s in strip]
    assert tags == ["code", "LLM", "code", "code", "LLM drafts, you approve"]
    assert all('role="tooltip"' in s and len(re.search(r'role="tooltip">(.*?)</span>', s, re.S).group(1)) > 40 for s in strip)
    assert "<script" in html and html.count("<script") == 1 and "framework" not in html.lower()


def test_the_run_view_is_a_stepper_a_sticky_viewer_and_four_tabs():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    stepper = re.findall(r'id="stp-(\w+)"', html)
    assert stepper == ["read", "extract", "verify", "decide", "act"]
    tabs = re.findall(r'<button class="tab[^"]*" role="tab"[^>]*data-tab="(\w+)"', html)
    assert tabs == ["summary", "actions", "fields", "trace"]
    assert 'class="tab active" role="tab" type="button" data-tab="summary"' in html  # Summary is the default
    assert re.search(r'id="tab-summary" role="tabpanel"><div', html) and re.search(r'id="tab-actions" role="tabpanel" hidden', html)
    assert 'id="viewer"' in html and html.index('id="viewer"') < html.index('class="panel"')  # the viewer is on the left
    table = re.search(r"<thead>(.*?)</thead>", html, re.S).group(1)
    assert re.findall(r"<th>(.*?)</th>", table) == ["Field", "Value", "Confidence", "Flags"]
    assert html.count('<link rel="stylesheet" href="/web/style.css">') == 1 and "fonts.googleapis.com/css2?family=Inter" in html


def test_the_design_has_tokens_dark_mode_and_a_375px_layout():
    css = (WEB / "style.css").read_text(encoding="utf-8")
    root = re.search(r":root\s*{(.*?)}", css, re.S).group(1)
    for token in ("--bg", "--surface", "--border", "--text", "--muted", "--accent", "--s1: 8px", "--s2: 16px", "--s3: 24px", "--radius", "--font"):
        assert token in root, token
    assert "@media (prefers-color-scheme: dark)" in css
    dark = re.search(r"@media \(prefers-color-scheme: dark\)\s*{\s*:root\s*{(.*?)}", css, re.S).group(1)
    assert all(t in dark for t in ("--bg", "--surface", "--text", "--accent"))
    assert "Inter" in css
    assert "@media (max-width: 860px)" in css and "@media (max-width: 480px)" in css
    stacked = re.search(r"@media \(max-width: 860px\)\s*{(.*?)\n}", css, re.S).group(1)
    assert "flex-direction: column" in stacked  # tabs stack under the viewer
    assert "prefers-reduced-motion" in css
    # spacing is on the 8px scale: every padding/margin/gap length in px is a multiple of 4 and the main ones of 8
    lengths = [int(x) for x in re.findall(r"(?:padding|margin|gap)[^;{}]*?(\d+)px", css)]
    assert lengths and all(x % 4 == 0 or x in (1, 2, 6) for x in lengths), sorted(set(lengths))


def test_the_run_view_does_not_scroll_the_page_on_desktop():
    css = (WEB / "style.css").read_text(encoding="utf-8")
    assert 'body[data-view="run"] { overflow: hidden; }' in css
    assert re.search(r"\.runview\s*{[^}]*height: calc\(100dvh - var\(--header-h\)\)", css)
    assert re.search(r"\.split\s*{[^}]*min-height: 0", css) and re.search(r"\.tabpane\s*{[^}]*overflow: auto", css)
    assert re.search(r"\.viewer\s*{[^}]*overflow: auto", css)
