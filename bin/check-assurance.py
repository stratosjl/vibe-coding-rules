#!/usr/bin/env python3
"""Check a project's assurance record against the shared contract.

The four controls (AC-TRACE, AC-REVIEW, AC-RESTORE, AC-EVIDENCE) are defined
once in policy/assurance.md, which carries a fenced `assurance-contract` JSON
block. This script reads its rules from that block and validates the single
markdown table under `## Assurance` in the record you name.

It refuses a row labelled `passed` whose evidence is missing, of a kind that
cannot pass that control (a backup offered for AC-RESTORE), observed as a
failure, at a different revision than the one being released, or, for
AC-REVIEW, without a reviewer or with only a tool or model name as the basis
for independence. It accepts a not-applicable row that gives a reason and
demands nothing else of it. An accepted risk is counted apart from passes.

The check is structural. It does not prove that a review was independent in
fact or that a restore succeeded.

Exit codes: 0 the record is well formed (and, with --release, every
applicable control is passed or a valid accepted risk); 1 at least one
violation or release gap; 2 the record or the contract could not be read.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

DEFAULT_CONTRACT = Path(__file__).resolve().parent.parent / "policy" / "assurance.md"
REVISION_RE = re.compile(r"^[0-9a-f]{7,40}(\+dirty:[0-9a-f]{12,64})?$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FILLER = {"and", "by", "reviewed", "review", "model", "harness", "different", "second",
          "another", "a", "an", "the", "with", "using", "via", "family", "other"}
NOTE = ("note: this check is structural; it does not prove that a review was "
        "independent or that a restore succeeded")


class RecordError(Exception):
    """The record cannot be read as an assurance table."""


class ContractError(Exception):
    """The contract block is missing or invalid."""


def load_contract(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ContractError(f"cannot read contract {path}: {e}") from e
    blocks = re.findall(r"^```json assurance-contract[ \t]*\n(.*?)^```[ \t]*$", text, re.M | re.S)
    if len(blocks) != 1:
        raise ContractError(f"{path} must hold exactly one assurance-contract block, found {len(blocks)}")
    try:
        return json.loads(blocks[0])
    except json.JSONDecodeError as e:
        raise ContractError(f"contract block in {path} is not valid JSON: {e}") from e


def split_cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    return [c.replace("\\|", "|").strip() for c in re.split(r"(?<!\\)\|", body)]


def parse_record(path: Path, contract: dict) -> list[dict[str, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise RecordError(f"cannot read {path}: {e}") from e
    headings, fence = [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fence = not fence
        elif not fence and re.match(r"^## Assurance\s*$", line):
            headings.append(i)
    if not headings:
        raise RecordError(f"{path}: no '## Assurance' table")
    if len(headings) > 1:
        raise RecordError(f"{path}: more than one '## Assurance' table ({len(headings)})")
    i = headings[0] + 1
    while i < len(lines) and not lines[i].lstrip().startswith("|"):
        if lines[i].startswith("#"):
            raise RecordError(f"{path}: no '## Assurance' table under the heading")
        i += 1
    table = []
    while i < len(lines) and lines[i].lstrip().startswith("|"):
        table.append(lines[i])
        i += 1
    if len(table) < 2:
        raise RecordError(f"{path}: no '## Assurance' table under the heading")
    columns = contract["columns"]
    header = split_cells(table[0])
    if header != columns:
        raise RecordError(f"{path}: columns must be {columns}, found {header}")
    rows = []
    for n, line in enumerate(table[2:], start=1):
        cells = split_cells(line)
        if len(cells) != len(columns):
            raise RecordError(f"{path}: row {n} has {len(cells)} cells, expected {len(columns)}")
        rows.append(dict(zip(columns, cells)))
    return rows


def present(value: str) -> bool:
    return value.strip() not in ("", "-")


def same_revision(a: str, b: str) -> bool:
    a, b = a.split("+dirty:")[0].lower(), b.split("+dirty:")[0].lower()
    shorter = min(len(a), len(b))
    return shorter >= 7 and a[:shorter] == b[:shorter]


def harness_only(basis: str, contract: dict) -> bool:
    text = " " + re.sub(r"[^a-z0-9]+", " ", basis.lower()) + " "
    for name in sorted(contract.get("harness_names", []), key=len, reverse=True):
        text = text.replace(f" {name} ", " ")
    return all(word in FILLER for word in text.split())


def validate(rows: list[dict[str, str]], contract: dict, *, tier: str | None = None,
             revision: str | None = None, today: date | None = None,
             release: bool = False) -> list[str]:
    today = today or date.today()
    controls, statuses = contract["controls"], contract["statuses"]
    kinds, results = contract["kinds"], contract["results"]
    out: list[str] = []
    for r in rows:
        ctl = r["Control"]
        status, kind = r["Status"].lower(), r["Kind"].lower()
        result, applies = r["Result"].lower(), r["Applies"].lower()
        rev = r["Revision"].strip()

        def bad(msg: str) -> None:
            out.append(f"{ctl}: {msg}")

        if ctl not in controls:
            bad("unknown control")
            continue
        if status not in statuses:
            bad(f"unknown status {r['Status']!r}")
            continue
        if present(kind) and kind not in kinds:
            bad(f"unknown kind {r['Kind']!r}")
        if result not in results:
            bad(f"result must be one of {results}, found {r['Result']!r}")
        if applies not in ("yes", "no"):
            bad(f"Applies must be yes or no, found {r['Applies']!r}")
        if applies == "no" and status != "not-applicable":
            bad(f"Applies is no, so the status must be not-applicable, not {status}")
        if status == "not-applicable":
            if applies == "yes":
                bad("status is not-applicable but Applies is yes")
            if not present(r["Reason"]):
                bad("not-applicable needs a reason")
            continue
        if present(rev) and rev.lower() != "n/a" and not REVISION_RE.match(rev.lower()):
            bad(f"revision {rev!r} must be <sha> or <sha>+dirty:<digest of 12+ hex>")
        if status == "passed":
            if not present(r["Evidence"]):
                bad("passed without evidence")
            if kind not in controls[ctl]["passing_kinds"]:
                hint = "; a backup is not a restore test" if ctl == "AC-RESTORE" and kind == "backup" else ""
                bad(f"evidence of kind {r['Kind']!r} cannot pass {ctl}{hint}")
            if result != "pass":
                bad(f"labelled passed but the observed result is {r['Result']!r}")
            if not present(rev) or not REVISION_RE.match(rev.lower()):
                bad("passed without a revision")
            elif revision and not same_revision(rev, revision):
                bad(f"passed at {rev} but the revision checked is {revision}: mark it stale")
            if ctl == "AC-REVIEW":
                if not present(r["Reviewer"]):
                    bad("review passed without naming the reviewer")
                if not present(r["Independence"]):
                    bad("review passed without a basis for independence")
                elif harness_only(r["Independence"], contract):
                    bad("a tool or model name alone does not establish independence")
        if status == "accepted-risk":
            for col in ("Owner", "Reason", "Trigger"):
                if not present(r[col]):
                    bad(f"accepted risk needs {col.lower()}")
            trig = r["Trigger"].strip()
            if ISO_DATE_RE.match(trig) and date.fromisoformat(trig) < today:
                bad(f"accepted risk expired on {trig}")
        if release and applies == "yes" and status not in ("passed", "accepted-risk"):
            bad(f"applicable and {status}; a release needs it passed or a valid accepted risk")
    if tier:
        seen = {r["Control"] for r in rows}
        for ctl in contract.get("decision_required", {}).get(tier.upper(), []):
            if ctl not in seen:
                out.append(f"{ctl}: {tier.upper()} requires an applicability decision; no row found")
    return out


def summary(rows: list[dict[str, str]], contract: dict) -> str:
    counts = {s: 0 for s in contract["statuses"]}
    for r in rows:
        s = r["Status"].lower()
        if s in counts:
            counts[s] += 1
    return f"summary: {len(rows)} row(s); " + ", ".join(f"{n} {s}" for s, n in counts.items())


def run(record: Path, contract_path: Path, tier: str | None, revision: str | None,
        today: date, release: bool) -> int:
    try:
        contract = load_contract(contract_path)
        rows = parse_record(record, contract)
    except (ContractError, RecordError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    problems = validate(rows, contract, tier=tier, revision=revision, today=today, release=release)
    for p in problems:
        print(p)
    print(summary(rows, contract))
    print(NOTE)
    return 1 if problems else 0


def self_test() -> int:
    contract_path = DEFAULT_CONTRACT
    contract = load_contract(contract_path)
    head = "| " + " | ".join(contract["columns"]) + " |\n|" + "---|" * len(contract["columns"]) + "\n"
    good = "| AC-TRACE | R-1 | yes | r | o | passed | t.py | test | pass | 1111111 | - | - | - |\n"
    cases = {
        "clean": (f"## Assurance\n\n{head}{good}", 0),
        "mislabelled pass": (f"## Assurance\n\n{head}{good.replace('| pass |', '| fail |')}", 1),
        "missing table": ("# nothing here\n", 2),
    }
    failed = 0
    with tempfile.TemporaryDirectory() as td:
        for name, (text, want) in cases.items():
            p = Path(td) / "record.md"
            p.write_text(text, encoding="utf-8")
            got = run(p, contract_path, None, None, date.today(), False)
            ok = got == want
            failed += not ok
            print(f"self-test {name}: {'PASS' if ok else 'FAIL'} (exit {got}, expected {want})")
    print(f"self-test: {len(cases) - failed} pass / {failed} fail")
    return 0 if failed == 0 else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("record", nargs="?", type=Path)
    ap.add_argument("--tier", choices=["T0", "T1", "T2", "T3", "T4"])
    ap.add_argument("--revision", help="the revision being checked or released")
    ap.add_argument("--release", action="store_true", help="also require every applicable control to be passed or a valid accepted risk")
    ap.add_argument("--today", type=date.fromisoformat, default=date.today())
    ap.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if a.record is None:
        ap.error("a record path is required")
    return run(a.record, a.contract, a.tier, a.revision, a.today, a.release)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
