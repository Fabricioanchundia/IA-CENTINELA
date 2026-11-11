"""Phase 10: the static public demo page."""

import json
import shutil
from pathlib import Path

from centinela import site

ROOT = Path(__file__).resolve().parents[1]


def copy_repo_data(tmp: Path) -> Path:
    for rel in ("eval/cases", "docs/eval", "docs/redteam"):
        shutil.copytree(ROOT / rel, tmp / rel)
    return tmp


def test_page_is_built_from_the_recorded_evidence(tmp_path: Path) -> None:
    root = copy_repo_data(tmp_path / "repo")
    page = site.write(root, tmp_path / "out").read_text()
    assert page.startswith("<!doctype html>") and (tmp_path / "out" / ".nojekyll").exists()
    for name in ("payments-down", "postgres-down", "orders-memory-leak"):
        assert f"<b>{name}</b>" in page
    assert "Exactly what the AI saw" in page and "[E1] alert:" in page
    assert "qwen2.5:7b" in page and "slow-orders" in page
    # the bad-release proposal from the live run, and what today's guards make of it
    assert "<code>restart:payments</code>" in page
    assert "With today's guards the same answer gives <code>none</code>" in page


def test_fonts_are_self_hosted_and_no_emoji(tmp_path: Path) -> None:
    root = copy_repo_data(tmp_path / "repo")
    out = tmp_path / "out"
    page = site.write(root, out).read_text()
    for name in ("geist.woff2", "geist-mono.woff2", "OFL.txt"):
        assert (out / name).stat().st_size > 0
    assert "font-src 'self'" in page and "fonts.googleapis" not in page
    assert "Incident 11 opened" in page  # the hero timeline of the real payments-down run
    assert not any(ord(ch) >= 0x2600 for ch in page)  # no emoji or pictographs


def test_no_script_and_untrusted_text_is_escaped(tmp_path: Path) -> None:
    root = copy_repo_data(tmp_path / "repo")
    case = root / "eval" / "cases" / "chaos-payments-down.json"
    data = json.loads(case.read_text())
    data["live"]["summary"] = '<script>alert("pwned")</script>'
    data["fault"] = "<img src=x onerror=alert(1)>"
    case.write_text(json.dumps(data))
    page = site.build(root)
    assert "<script" not in page and "<img" not in page
    assert "&lt;script&gt;alert(&quot;pwned&quot;)&lt;/script&gt;" in page
    assert "default-src 'none'" in page  # CSP: no script could run anyway
