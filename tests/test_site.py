"""Phase 10: the public demo page and its incident player."""

import json
import re
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


def test_player_tells_four_real_stories_in_two_languages(tmp_path: Path) -> None:
    page = site.build(copy_repo_data(tmp_path / "repo"))
    for key in ("payments-down", "postgres-down", "bad-release", "injection"):
        assert f'id="sc-{key}"' in page
    # payments-down: the real recorded times and answer
    assert "15:28:52 UTC" in page and "scaled to 0 replicas, causing upstream errors" in page
    assert (
        "Volver a encender pagos (1 copia)" in page
        and "Scale payments back up to 1 replica" in page
    )
    # postgres-down: the model suggested touching the database, the policy blocked it
    assert "<p class='struck'>" in page and "Bloqueada por la política" in page
    # the bad release: the real wrong proposal, then today's rule
    assert "Propuesta incorrecta" in page and "NEW ROLLOUT" in page
    # the injection: the attacker's text is shown escaped, with the measured result
    assert "IGNORE ALL PREVIOUS INSTRUCTIONS" in page and "coupon &quot;" in page
    assert re.search(r"<div class='good'><dt>.*?</dt><dd>0</dd>", page)
    assert page.count('class="es"') == page.count('class="en"')


def test_static_files_and_no_emoji(tmp_path: Path) -> None:
    out = tmp_path / "out"
    page = site.write(copy_repo_data(tmp_path / "repo"), out).read_text()
    for name in ("geist.woff2", "geist-mono.woff2", "OFL.txt", "player.js"):
        assert (out / name).stat().st_size > 0
    assert "fonts.googleapis" not in page
    assert not any(ord(ch) >= 0x2600 for ch in page)  # no emoji or pictographs


def test_only_the_local_player_script_and_untrusted_text_is_escaped(tmp_path: Path) -> None:
    root = copy_repo_data(tmp_path / "repo")
    case = root / "eval" / "cases" / "chaos-payments-down.json"
    data = json.loads(case.read_text())
    data["live"]["summary"] = '<script>alert("pwned")</script>'
    data["fault"] = "<img src=x onerror=alert(1)>"
    case.write_text(json.dumps(data))
    page = site.build(root)
    assert re.findall(r"<script[^>]*>", page) == ['<script src="player.js" defer>']
    assert "<img" not in page
    assert "&lt;script&gt;alert(&quot;pwned&quot;)&lt;/script&gt;" in page
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', page)
    assert csp is not None
    policy = csp.group(1)
    assert "default-src 'none'" in policy and "script-src 'self'" in policy
    assert "unsafe-inline" not in policy.split("script-src", 1)[1].split(";")[0]
    assert "connect-src" not in policy  # falls back to default-src 'none': no network at all
