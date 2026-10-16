"""scripts/overlay_private.py: copies only manifest paths, refuses unsafe input, marks installed last."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location("overlay_private_under_test", ROOT / "scripts" / "overlay_private.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ov = _load()
REQUIRED = ("maker_strategy.py", "maker_monitor.py", "maker_config.json", "arb_scanner.py", "config.py",
            "kalshi_client.py", "markets.py")


def _private(tmp_path: Path, *, standin=False) -> Path:
    p = tmp_path / "private"
    (p / "kalshi_bot" / "tests").mkdir(parents=True)
    for name in REQUIRED:
        (p / "kalshi_bot" / name).write_text(f"# private {name}\n", encoding="utf-8")
    if standin:
        (p / "kalshi_bot" / "maker_strategy.py").write_text("IS_STANDIN = True\n", encoding="utf-8")
    (p / "kalshi_bot" / "tests" / "test_private.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (p / "kalshi_bot" / "tests" / "__pycache__").mkdir()
    (p / "kalshi_bot" / "tests" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (p / "kalshi_bot" / "bot.env").write_text("SECRET=1\n", encoding="utf-8")        # never listed, never copied
    (p / "control_api" / "data").mkdir(parents=True)
    (p / "control_api" / "data" / "cell_expectations.json").write_text("{}", encoding="utf-8")
    return p


def _target(tmp_path: Path) -> Path:
    t = tmp_path / "public"
    (t / "kalshi_bot").mkdir(parents=True)
    (t / "control_api").mkdir()
    (t / "kalshi_bot" / "maker_strategy.py").write_text("IS_STANDIN = True\n", encoding="utf-8")
    return t


def test_overlay_copies_manifest_files_and_writes_marker_last(tmp_path):
    priv, tgt = _private(tmp_path), _target(tmp_path)
    rels = ov.overlay(priv, tgt, with_tests=True)
    assert "kalshi_bot/maker_strategy.py" in rels and "kalshi_bot/tests/test_private.py" in rels
    assert "control_api/data/cell_expectations.json" in rels
    assert (tgt / "kalshi_bot" / "maker_strategy.py").read_text(encoding="utf-8") == "# private maker_strategy.py\n"
    marker = (tgt / "kalshi_bot" / "_overlay.py").read_text(encoding="utf-8")
    assert "IS_STANDIN = False" in marker and "kalshi_bot/maker_strategy.py" in marker
    assert not (tgt / "kalshi_bot" / "bot.env").exists()                       # not in the manifest
    assert not (tgt / "kalshi_bot" / "tests" / "__pycache__").exists()
    assert not list(tgt.rglob("*.tmp"))


def test_skiptest_entries_are_not_copied_even_with_tests(tmp_path):
    priv, tgt = _private(tmp_path), _target(tmp_path)
    for n in ("test_bot_risk_gate.py", "test_sizing.py", "test_strategies.py"):
        (priv / "kalshi_bot" / "tests" / n).write_text("import gate\n", encoding="utf-8")
    rels = ov.overlay(priv, tgt, with_tests=True)
    assert "kalshi_bot/tests/test_private.py" in rels
    assert not any(r.endswith(("test_bot_risk_gate.py", "test_sizing.py", "test_strategies.py")) for r in rels)


def test_tests_only_with_flag_and_dry_run_changes_nothing(tmp_path):
    priv, tgt = _private(tmp_path), _target(tmp_path)
    rels = ov.overlay(priv, tgt, dry_run=True)
    assert "kalshi_bot/tests/test_private.py" not in rels
    assert (tgt / "kalshi_bot" / "maker_strategy.py").read_text(encoding="utf-8") == "IS_STANDIN = True\n"
    assert not (tgt / "kalshi_bot" / "_overlay.py").exists()


def test_missing_required_file_fails_and_never_marks_installed(tmp_path):
    priv, tgt = _private(tmp_path), _target(tmp_path)
    (priv / "kalshi_bot" / "arb_scanner.py").unlink()
    with pytest.raises(ov.OverlayError, match="arb_scanner"):
        ov.overlay(priv, tgt)
    assert not (tgt / "kalshi_bot" / "_overlay.py").exists()
    assert ov.main(["--private", str(priv), "--target", str(tgt)]) == 3


def test_standin_as_private_source_is_refused(tmp_path):
    priv, tgt = _private(tmp_path, standin=True), _target(tmp_path)
    with pytest.raises(ov.OverlayError, match="IS_STANDIN = True"):
        ov.overlay(priv, tgt)
    assert not (tgt / "kalshi_bot" / "_overlay.py").exists()


def test_git_worktree_target_refused_unless_allowed(tmp_path):
    priv, tgt = _private(tmp_path), _target(tmp_path)
    (tgt / ".git").mkdir()
    with pytest.raises(ov.OverlayError, match="git work tree"):
        ov.overlay(priv, tgt)
    assert ov.overlay(priv, tgt, allow_git_worktree=True)
    assert (tgt / "kalshi_bot" / "_overlay.py").exists()


def test_stale_marker_removed_before_copy(tmp_path, monkeypatch):
    priv, tgt = _private(tmp_path), _target(tmp_path)
    (tgt / "kalshi_bot" / "_overlay.py").write_text("IS_STANDIN = False\n", encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(ov, "_atomic_copy", boom)
    with pytest.raises(OSError):
        ov.overlay(priv, tgt)
    assert not (tgt / "kalshi_bot" / "_overlay.py").exists()                  # half-copied = still the stand-in


@pytest.mark.parametrize("line", [
    "required ../secrets.txt", "required /etc/passwd", "required kalshi_bot/../../x", "required control_api/main.py",
    "required C:/x/kalshi_bot/a.py", "required kalshi_bot\\a.py", "copy kalshi_bot/a.py", "required",
    "required kalshi_bot/_overlay.py", "required render.yaml",
])
def test_manifest_rejects_unsafe_lines(line):
    with pytest.raises(ov.OverlayError) as e:
        ov.parse_manifest(line)
    assert e.value.code == 2


def test_shipped_manifest_is_valid_and_limited_to_the_bot():
    entries = ov.parse_manifest(ov.MANIFEST.read_text(encoding="utf-8"))
    assert {k for k, _ in entries} <= set(ov.KINDS)
    assert all(rel.startswith(ov.ALLOWED_PREFIXES) for _, rel in entries)
    assert ("required", "kalshi_bot/maker_strategy.py") in entries and ("required", "kalshi_bot/maker_config.json") in entries


def test_same_directory_and_non_tree_targets_refused(tmp_path):
    priv = _private(tmp_path)
    with pytest.raises(ov.OverlayError):
        ov.overlay(priv, priv)
    with pytest.raises(ov.OverlayError, match="maker-console"):
        ov.overlay(priv, tmp_path / "nowhere")
