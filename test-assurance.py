#!/usr/bin/env python3
"""Tests for the shared assurance controls.

`policy/assurance.md` is the one normative definition of the four controls.
It carries a single fenced `assurance-contract` JSON block, and
`bin/check-assurance.py` loads its rules from that block. These tests check
the contract, then drive the checker as a real process against small record
fixtures: each acceptance criterion of the proposal is a rejection or an
acceptance here, not a sentence looked up in a document.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
POLICY = ROOT / "policy" / "assurance.md"

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS: {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  FAIL: {label}")


def contract_blocks(text: str) -> list[str]:
    return re.findall(r"^```json assurance-contract[ \t]*\n(.*?)^```[ \t]*$", text, re.M | re.S)


def contract_block_loads_and_is_complete() -> None:
    print("contract_block_loads_and_is_complete")
    if not POLICY.is_file():
        check(False, "policy/assurance.md exists")
        return
    blocks = contract_blocks(POLICY.read_text(encoding="utf-8"))
    check(len(blocks) == 1, f"exactly one assurance-contract block ({len(blocks)})")
    if len(blocks) != 1:
        return
    try:
        c = json.loads(blocks[0])
    except json.JSONDecodeError as e:
        check(False, f"contract block is valid JSON ({e})")
        return
    check(c.get("version") == 1, "contract version is 1")
    check(sorted(c.get("controls", {})) == ["AC-EVIDENCE", "AC-RESTORE", "AC-REVIEW", "AC-TRACE"],
          "the four control IDs")
    check(c.get("statuses") == ["planned", "passed", "failed", "blocked", "stale",
                                "not-applicable", "accepted-risk"], "the seven statuses")
    check(c.get("columns") == ["Control", "Requirements", "Applies", "Reason", "Owner", "Status",
                               "Evidence", "Kind", "Result", "Revision", "Reviewer",
                               "Independence", "Trigger"], "the thirteen columns")
    kinds = set(c.get("kinds", []))
    passing = [k for ctl in c.get("controls", {}).values() for k in ctl.get("passing_kinds", [])]
    check(bool(passing) and all(k in kinds for k in passing), "every passing kind is a known kind")
    check(c.get("controls", {}).get("AC-RESTORE", {}).get("passing_kinds") == ["restore-test"],
          "only a restore test can pass AC-RESTORE")
    check(set(c.get("decision_required", {}).get("T4", [])) == set(c.get("controls", {})),
          "T4 requires a decision on every control")


CHECKER = ROOT / "bin" / "check-assurance.py"
EXAMPLE = ROOT / "docs" / "examples" / "assurance-record.md"
COLUMNS = ["Control", "Requirements", "Applies", "Reason", "Owner", "Status", "Evidence",
           "Kind", "Result", "Revision", "Reviewer", "Independence", "Trigger"]
GOOD = {
    "AC-TRACE": dict(Requirements="R-1", Applies="yes", Reason="contract", Owner="o",
                     Status="passed", Evidence="t.py", Kind="test", Result="pass",
                     Revision="1111111", Reviewer="-", Independence="-", Trigger="-"),
    "AC-REVIEW": dict(Requirements="R-2", Applies="yes", Reason="token scope", Owner="o",
                      Status="passed", Evidence="review.md", Kind="review", Result="pass",
                      Revision="1111111", Reviewer="second maintainer",
                      Independence="did not write the change; read-only access", Trigger="-"),
    "AC-RESTORE": dict(Requirements="-", Applies="yes", Reason="database", Owner="o",
                       Status="passed", Evidence="restore.log", Kind="restore-test",
                       Result="pass", Revision="1111111", Reviewer="-", Independence="-",
                       Trigger="-"),
    "AC-EVIDENCE": dict(Requirements="-", Applies="yes", Reason="release", Owner="o",
                        Status="passed", Evidence="manifest.json", Kind="manifest",
                        Result="pass", Revision="1111111", Reviewer="-", Independence="-",
                        Trigger="-"),
}
TMP = Path(__import__("tempfile").mkdtemp(prefix="vcroe-assurance-test-"))


def table(rows: list[dict[str, str]]) -> str:
    lines = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for r in rows:
        lines.append("| " + " | ".join(r.get(c, "-") for c in COLUMNS) + " |")
    return "\n".join(lines) + "\n"


def row(control: str, **changes: str) -> dict[str, str]:
    r = {"Control": control, **GOOD[control]}
    r.update(changes)
    return r


def record(rows: list[dict[str, str]], name: str, extra: str = "") -> Path:
    p = TMP / f"{name}.md"
    p.write_text(f"# Record\n\n## Assurance\n\n{table(rows)}{extra}", encoding="utf-8")
    return p


def run(path: Path, *args: str) -> tuple[int, str]:
    import subprocess
    proc = subprocess.run([sys.executable, str(CHECKER), str(path), *args],
                          capture_output=True, text=True, encoding="utf-8", timeout=30)
    return proc.returncode, proc.stdout + proc.stderr


def rejects(name: str, rows: list[dict[str, str]], *args: str, names: str) -> None:
    print(name)
    rc, out = run(record(rows, name), *args)
    check(rc == 1 and names in out, f"{name}: exit 1 naming {names} (rc {rc}: {out.strip()[:160]!r})")


def accepts(name: str, rows: list[dict[str, str]], *args: str) -> str:
    print(name)
    rc, out = run(record(rows, name), *args)
    check(rc == 0, f"{name}: exit 0 (rc {rc}: {out.strip()[:160]!r})")
    return out


def checker_tests() -> None:
    R = "--revision"
    rejects("passed_without_evidence_is_rejected", [row("AC-TRACE", Evidence="")], names="AC-TRACE")
    rejects("passed_with_failed_result_is_rejected", [row("AC-TRACE", Result="fail")], names="AC-TRACE")
    rejects("passed_at_other_revision_must_be_stale", [row("AC-TRACE", Revision="0000000")],
            R, "1111111", names="AC-TRACE")
    rejects("dirty_revision_without_digest_is_rejected", [row("AC-TRACE", Revision="1111111+dirty")],
            names="AC-TRACE")
    rejects("backup_cannot_pass_restore", [row("AC-RESTORE", Kind="backup")], names="AC-RESTORE")
    rejects("review_needs_reviewer_and_basis", [row("AC-REVIEW", Reviewer="")], names="AC-REVIEW")
    rejects("harness_name_alone_is_not_independence", [row("AC-REVIEW", Independence="codex")],
            names="AC-REVIEW")
    rejects("not_applicable_needs_reason",
            [row("AC-RESTORE", Applies="no", Reason="", Status="not-applicable", Kind="-",
                 Result="n/a", Revision="n/a", Evidence="-")], names="AC-RESTORE")
    rejects("applies_no_must_be_not_applicable", [row("AC-TRACE", Applies="no")], names="AC-TRACE")
    rejects("accepted_risk_needs_owner_and_trigger",
            [row("AC-RESTORE", Status="accepted-risk", Trigger="", Result="n/a")], names="AC-RESTORE")
    rejects("expired_accepted_risk_is_rejected",
            [row("AC-RESTORE", Status="accepted-risk", Trigger="2026-01-01", Result="n/a")],
            "--today", "2026-10-23", names="AC-RESTORE")
    rejects("unknown_status_or_kind_is_rejected", [row("AC-TRACE", Status="done")], names="AC-TRACE")
    rejects("t4_requires_every_control",
            [row("AC-TRACE"), row("AC-REVIEW"), row("AC-EVIDENCE")], "--tier", "T4", names="AC-RESTORE")
    rejects("t3_requires_review_and_restore_decisions", [row("AC-RESTORE")], "--tier", "T3",
            names="AC-REVIEW")
    rejects("release_rejects_open_applicable_control", [row("AC-TRACE", Status="planned", Result="n/a")],
            "--release", names="AC-TRACE")

    print("justified_not_applicable_passes")
    rows = [row("AC-REVIEW"),
            row("AC-RESTORE", Applies="no", Reason="stateless: rebuilt from the repository",
                Status="not-applicable", Kind="-", Result="n/a", Revision="n/a", Evidence="-")]
    rc, out = run(record(rows, "justified_not_applicable_passes"), "--tier", "T3", "--release")
    check(rc == 0, f"justified_not_applicable_passes: exit 0 with --tier T3 --release (rc {rc}: {out.strip()[:160]!r})")

    out = accepts("accepted_risk_is_counted_but_not_passed",
                  [row("AC-RESTORE", Status="accepted-risk", Trigger="2027-03-31", Result="n/a")],
                  "--release", "--today", "2026-10-23")
    check("1 accepted-risk" in out and "0 passed" in out,
          f"accepted_risk_is_counted_but_not_passed: summary counts it apart ({out.strip()[-120:]!r})")
    accepts("t2_record_needs_no_decision_rows", [row("AC-TRACE")], "--tier", "T2")

    # Review Focus
    accepts("revision_prefix_matches_both_ways",
            [row("AC-TRACE", Revision="1111111aaaa")], R, "1111111")
    accepts("revision_prefix_matches_both_ways_full",
            [row("AC-TRACE", Revision="1111111")], R, "1111111" + "a" * 33)
    accepts("escaped_pipe_does_not_shift_columns",
            [row("AC-TRACE", Evidence="`grep a\\|b`")], R, "1111111")
    print("missing_table_exits_2")
    p = TMP / "missing_table.md"
    p.write_text("# Record\n\nNo assurance here.\n", encoding="utf-8")
    rc, out = run(p)
    check(rc == 2 and "no '## Assurance' table" in out, f"missing_table_exits_2: exit 2 with the reason (rc {rc}: {out.strip()[:120]!r})")
    print("duplicate_table_exits_2")
    rc, out = run(record([row("AC-TRACE")], "duplicate_table",
                         extra="\n## Assurance\n\n" + table([row("AC-TRACE")])))
    check(rc == 2 and "more than one '## Assurance' table" in out, f"duplicate_table_exits_2: exit 2 with the reason (rc {rc}: {out.strip()[:120]!r})")
    accepts("status_is_case_and_space_insensitive",
            [row("AC-TRACE", Status=" Passed ", Kind="TEST", Result="Pass")])

    # The example from Task 1
    print("example_record_is_well_formed")
    rc, out = run(EXAMPLE, R, "1111111")
    check(rc == 0, f"example_record_is_well_formed: exit 0 (rc {rc}: {out.strip()[:160]!r})")
    print("example_record_is_not_release_ready")
    rc, out = run(EXAMPLE, R, "1111111", "--release")
    gaps = sorted(set(re.findall(r"^(AC-[A-Z]+):", out, re.M)))
    check(rc == 1 and gaps == ["AC-EVIDENCE", "AC-REVIEW"],
          f"example_record_is_not_release_ready: exit 1 naming AC-REVIEW and AC-EVIDENCE only (rc {rc}, {gaps})")
    print("self_test")
    import subprocess
    st = subprocess.run([sys.executable, str(CHECKER), "--self-test"], capture_output=True, text=True)
    check(st.returncode == 0, f"self_test: --self-test exits 0 (rc {st.returncode})")


TESTS = [contract_block_loads_and_is_complete, checker_tests]


def main() -> int:
    for t in TESTS:
        t()
    print(f"\n{PASS} pass / {FAIL} fail")
    for f in FAILURES:
        print(f"  - {f}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
