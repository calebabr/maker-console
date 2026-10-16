"""semgrep_triage.py: new findings fail, triaged ones pass, fingerprints survive line moves but not edits."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import semgrep_triage as st  # noqa: E402

RULE = "python.lang.security.audit.example"


def result(path="app.py", line=2, rule=RULE):
    return {"check_id": rule, "path": path, "start": {"line": line, "col": 1}, "end": {"line": line, "col": 9},
            "extra": {"severity": "WARNING", "message": "something"}}


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "app.py").write_text("import os\nvalue = os.getenv('X')   # flagged\nprint(value)\n", encoding="utf-8")
    return tmp_path


def entry(tree, rule=RULE, path="app.py", line=2, reason="reviewed: harmless"):
    return {"rule": rule, "path": path, "fingerprint": st.finding_key(result(path, line, rule), tree)[2], "reason": reason}


def run(tree, results, baseline=None, extra=()):
    rep = tree / "report.json"
    rep.write_text(json.dumps({"results": results, "errors": []}), encoding="utf-8")
    args = ["--report", str(rep), "--root", str(tree), "--baseline", str(tree / "base.json"), *extra]
    if baseline is not None:
        (tree / "base.json").write_text(json.dumps({"version": 1, "findings": baseline}), encoding="utf-8")
    return st.main(args)


def test_no_findings_passes(tree):
    assert run(tree, []) == 0


def test_new_finding_fails_even_without_a_baseline_file(tree):
    assert run(tree, [result()]) == 1


def test_triaged_finding_passes(tree):
    assert run(tree, [result()], [entry(tree)]) == 0


def test_finding_survives_a_line_move_but_not_an_edit(tree):
    base = [entry(tree)]
    (tree / "app.py").write_text("# new comment\nimport os\nvalue = os.getenv('X')   # flagged\nprint(value)\n", encoding="utf-8")
    assert run(tree, [result(line=3)], base) == 0                       # same text, new line number
    (tree / "app.py").write_text("import os\nvalue = os.getenv('Y')\n", encoding="utf-8")
    assert run(tree, [result(line=2)], base) == 1                       # the flagged text changed: needs a fresh review


def test_same_text_in_another_file_or_another_rule_is_not_triaged(tree):
    (tree / "other.py").write_text("import os\nvalue = os.getenv('X')   # flagged\n", encoding="utf-8")
    assert run(tree, [result(path="other.py")], [entry(tree)]) == 1
    assert run(tree, [result(rule="another.rule")], [entry(tree)]) == 1


def test_whitespace_changes_do_not_matter(tree):
    base = [entry(tree)]
    (tree / "app.py").write_text("import os\n    value   =   os.getenv('X')   # flagged\n", encoding="utf-8")
    assert run(tree, [result()], base) == 0


def test_entry_without_a_reason_is_a_failure(tree):
    assert run(tree, [result()], [entry(tree, reason="  ")]) == 1


def test_wrong_baseline_shape_fails_closed(tree):
    (tree / "base.json").write_text(json.dumps({"version": 2, "findings": []}), encoding="utf-8")
    assert run(tree, [], None) == 1


def test_stale_entries_only_warn(tree, capsys):
    assert run(tree, [], [entry(tree)]) == 0
    assert "stale" in capsys.readouterr().err


def test_unreadable_report_and_missing_results_fail_closed(tree):
    assert st.main(["--report", str(tree / "nope.json")]) == 1
    (tree / "r.json").write_text("{}", encoding="utf-8")
    assert st.main(["--report", str(tree / "r.json")]) == 1


def test_unreadable_source_never_matches_a_baseline_entry(tree):
    assert run(tree, [result(path="missing.py")], [entry(tree, path="missing.py")]) == 1


def test_path_traversal_in_a_report_is_not_read(tree, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "secret.py"
    outside.write_text("x = 1\n", encoding="utf-8")
    assert st.source_text(tree, "../" + outside.parent.name + "/secret.py", 1, 1) is None


def test_print_baseline_lists_only_new_findings(tree, capsys):
    assert run(tree, [result()], [], extra=("--print-baseline",)) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["findings"][0]["rule"] == RULE and out["findings"][0]["reason"].startswith("TODO")
