# Shared Assurance Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define four assurance controls once, in a tool-neutral policy that the T1 to T4 slices, the audit-pass command and the Codex shared policy all reference, and ship a checker that refuses a record which labels missing, failed, stale or substitute evidence as passed.

**Architecture:** `policy/assurance.md` is the single normative definition. It carries the prose and one fenced `assurance-contract` JSON block (control IDs, statuses, evidence kinds, tier requirements), and `bin/check-assurance.py` loads its rules from that block rather than duplicating them. Projects record assurance in a fixed-column markdown table under `## Assurance` in an existing project record; no database, service or hook is added.

**Tech Stack:** Python 3 standard library only (like `bin/check-goal-block.py`); markdown.

**Spec:** `docs/proposals/shared-assurance-controls.md` (assessed against `533f568`), its "Implementation continuation brief" and "Acceptance criteria" sections.

**Base:** start from GitHub `main` after two releases have landed: the pending September port, then the SessionStart slice split (`feat/sessionstart-slice-split`). The version number is the next one after both. Do not start before the split is on `main`: Task 3 grows the slices, and the split's size test is what keeps them visible.

## Global Constraints

- One normative definition per control, in `policy/assurance.md`; slices, commands and `policy/shared.md` carry short references, never a second definition.
- Do not import Claude-specific hooks, banners, memories or confirmation rituals into Codex (`policy/shared.md`, `AGENTS.md`).
- Do not change provider permissions, runtime permission rules or global security settings. Preserve the existing authorization rule.
- Use existing project records for persistence. No new database, no mandatory automation service, no new hook.
- Structural validation must not claim to prove independent review or a successful restore. Say so in the policy, the checker's help and the README.
- Tests check behaviour (the checker accepts or rejects a record), not that a sentence appears in a markdown file. The one permitted document test is that each slice and command names control IDs that exist in the contract.
- Public repository: no operator, firm, machine or client identifiers. Run `bin/publish-audit.sh`, `bin/publish-audit-state.sh --history` and gitleaks before push.
- Markdown is one line per block, never hard-wrapped. English prose passes the humanized-text rules (no em-dashes).
- Version: bump `.claude-plugin/plugin.json` and `kimi.plugin.json` only; `test-version-lockstep.py` must pass; tag `v<version>` annotated, per CONTRIBUTING.
- Every public slice must still pass `test-session-start-split.py` (each part under 9,500 characters, at most 3 parts).

## Review Focus

- **Short against full commit ids.** A record written with `a1b2c3d` and a `--revision` given as the full 40-character sha are the same revision; a prefix match of at least 7 characters either way must count as equal. Owner: Task 2, test `revision_prefix_matches_both_ways`.
- **Pipes and code inside cells.** A cell holding `` `a\|b` `` or an escaped pipe must not shift the columns. Owner: Task 2, test `escaped_pipe_does_not_shift_columns`.
- **No table, or two tables.** A file with no `## Assurance` table exits 2 with a message; a file with two exits 2 rather than silently reading one. Owner: Task 2, tests `missing_table_exits_2`, `duplicate_table_exits_2`.
- **Case and spacing.** `Passed`, ` passed ` and `PASSED` are the same status; an unknown status is a violation, not a skip. Owner: Task 2, test `status_is_case_and_space_insensitive`.
- **An expired accepted risk.** An accepted risk whose trigger is an ISO date before today is a violation, because the exception has lapsed. Owner: Task 2, test `expired_accepted_risk_is_rejected` (with `--today` fixed).

---

### Task 1: The normative policy and its contract block

**Files:**
- Create: `policy/assurance.md`
- Create: `docs/examples/assurance-record.md`
- Test: `test-assurance.py` (created here, extended in Task 2)

**Interfaces:**
- Produces: a fenced block opened with ```` ```json assurance-contract ```` in `policy/assurance.md`, exactly one, parsed by `load_contract(path: Path) -> dict` in Task 2. Its keys:

```json
{
  "version": 1,
  "controls": {
    "AC-TRACE":    {"name": "Requirements-to-test traceability", "passing_kinds": ["test", "review", "manual"]},
    "AC-REVIEW":   {"name": "Independent policy and isolation review", "passing_kinds": ["review"]},
    "AC-RESTORE":  {"name": "Tested restoration", "passing_kinds": ["restore-test"]},
    "AC-EVIDENCE": {"name": "Revision-bound evidence", "passing_kinds": ["manifest"]}
  },
  "statuses": ["planned", "passed", "failed", "blocked", "stale", "not-applicable", "accepted-risk"],
  "kinds": ["test", "review", "manual", "restore-test", "backup", "manifest", "inspection"],
  "results": ["pass", "fail", "n/a"],
  "columns": ["Control", "Requirements", "Applies", "Reason", "Owner", "Status", "Evidence", "Kind", "Result", "Revision", "Reviewer", "Independence", "Trigger"],
  "decision_required": {"T3": ["AC-REVIEW", "AC-RESTORE"], "T4": ["AC-TRACE", "AC-REVIEW", "AC-RESTORE", "AC-EVIDENCE"]},
  "harness_names": ["claude", "claude code", "codex", "kimi", "gemini", "gpt", "chatgpt", "opus", "sonnet", "haiku", "fable", "copilot", "cursor"]
}
```

- [ ] **Step 1: Write the failing test** `contract_block_loads_and_is_complete` in `test-assurance.py`: it reads `policy/assurance.md`, finds exactly one `assurance-contract` block, parses it, and asserts the four control IDs, the seven statuses and the thirteen columns above, and that every `passing_kinds` entry is in `kinds`. Use the same `check(cond, label)` and `PASS/FAIL` shape as `test-session-start-split.py`.
- [ ] **Step 2: Run it.** `python3 test-assurance.py` → FAIL (file missing).
- [ ] **Step 3: Write `policy/assurance.md`.** Sections, in this order: purpose (one paragraph: what the controls are and that a low tier cannot remove an applicable security or recovery control); the four controls, each with its trigger, required evidence and closure rule, taken from the proposal's "Recommended tier placement" and "Practical meanings"; the tier matrix (the proposal's table, T1 and T2 floor, T3, T4); the record format (the thirteen columns, one row per control, the meaning of each status, and that `accepted-risk` is never a pass); what the checker can and cannot prove; the contract block. The trigger rule: a risk trigger activates the control at any tier and prompts tier reassessment, and never promotes a project silently. Revision format: `<sha>` or `<sha>+dirty:<digest>`, where the digest is at least 12 hex characters of a tree or file-manifest hash.
- [ ] **Step 4: Write `docs/examples/assurance-record.md`.** A synthetic stateless documentation project with one `## Assurance` table of four rows, all at revision `1111111` except where stated: `AC-TRACE` passed (kind test, result pass); `AC-RESTORE` not-applicable with the reason "stateless: the site is rebuilt from the repository; no data to restore"; `AC-REVIEW` stale (passed review, but its revision is `0000000`); `AC-EVIDENCE` failed (kind manifest, result fail). Above the table, one sentence saying it is synthetic.
- [ ] **Step 5: Run it.** `python3 test-assurance.py` → PASS.
- [ ] **Step 6: Commit.** `git add policy/assurance.md docs/examples/assurance-record.md test-assurance.py && git commit -m "policy: shared assurance controls, one definition with a contract block"`

### Task 2: The checker

**Files:**
- Create: `bin/check-assurance.py`
- Test: `test-assurance.py`

**Interfaces:**
- Consumes: the contract block from Task 1.
- Produces: `load_contract(path: Path) -> dict`; `parse_record(path: Path, contract: dict) -> list[dict[str, str]]` (rows keyed by column name, raises `RecordError` for no table, two tables or wrong columns); `validate(rows, contract, *, tier: str | None, revision: str | None, today: date, release: bool) -> list[str]` (one message per violation, empty when clean); CLI `check-assurance.py RECORD [--tier T0..T4] [--revision SHA] [--release] [--today YYYY-MM-DD] [--contract PATH]`. Exit 0 clean, 1 any violation (or, with `--release`, any applicable control neither passed nor a valid accepted risk), 2 unreadable record or contract. The default `--contract` is `../policy/assurance.md` relative to the script.

- [ ] **Step 1: Write the failing tests**, each building a record from a one-row fixture string and asserting the exit code and that the message names the control:

```python
# rejections (exit 1)
passed_without_evidence_is_rejected          # Status passed, Evidence empty
passed_with_failed_result_is_rejected        # Status passed, Result fail
passed_at_other_revision_must_be_stale       # Revision 0000000, --revision 1111111
dirty_revision_without_digest_is_rejected    # Revision "1111111+dirty"
backup_cannot_pass_restore                   # AC-RESTORE passed, Kind backup
review_needs_reviewer_and_basis              # AC-REVIEW passed, Reviewer empty
harness_name_alone_is_not_independence       # AC-REVIEW passed, Independence "codex"
not_applicable_needs_reason                  # Applies no, Reason empty
applies_no_must_be_not_applicable            # Applies no, Status passed
accepted_risk_needs_owner_and_trigger        # Status accepted-risk, Trigger empty
expired_accepted_risk_is_rejected            # Trigger 2026-01-01, --today 2026-10-23
unknown_status_or_kind_is_rejected           # Status "done"
t4_requires_every_control                    # --tier T4, AC-RESTORE row missing
t3_requires_review_and_restore_decisions     # --tier T3, AC-REVIEW row missing
release_rejects_open_applicable_control      # --release, AC-TRACE planned
# acceptances (exit 0)
justified_not_applicable_passes              # stateless AC-RESTORE, reason given, no test demanded
accepted_risk_is_counted_but_not_passed      # --release passes, summary line says "1 accepted risk, 0 passed" for that row
t2_record_needs_no_decision_rows             # --tier T2, AC-TRACE only
# Review Focus
revision_prefix_matches_both_ways
escaped_pipe_does_not_shift_columns
missing_table_exits_2
duplicate_table_exits_2
status_is_case_and_space_insensitive
# the example from Task 1
example_record_is_well_formed                # exit 0 with --revision 1111111
example_record_is_not_release_ready          # exit 1 with --release, messages name AC-REVIEW stale and AC-EVIDENCE failed, and nothing else
```

- [ ] **Step 2: Run them.** `python3 test-assurance.py` → the Task 1 test passes, every new arm FAILS (script missing).
- [ ] **Step 3: Implement `bin/check-assurance.py`.** Table parsing: the first table after a line matching `^## Assurance\s*$`, cells split on unescaped `|`, trimmed; `Applies` is `yes` or `no`; statuses, kinds and results compared case-insensitively after trimming. Revision equality is a prefix match of at least 7 characters in either direction on the part before `+dirty:`. Independence is rejected when, lowercased and stripped of punctuation, it equals a `harness_names` entry or a list of them. The script prints one line per violation, then a summary per status, and a fixed note that a passing check is structural and proves neither independence nor a restore. Add `--self-test` that runs three built-in fixtures (one clean, one mislabelled pass, one missing table) and asserts 0, 1 and 2, like `check-goal-block.py --self-test`.
- [ ] **Step 4: Run them.** `python3 test-assurance.py` → all PASS; `python3 bin/check-assurance.py --self-test` → exit 0.
- [ ] **Step 5: Negative test of the suite.** Temporarily make `validate` skip the Result check; `python3 test-assurance.py` must report exactly `passed_with_failed_result_is_rejected` failing. Restore with `command cp -f` from a copy taken first, and confirm with `cmp`.
- [ ] **Step 6: Commit.** `git add bin/check-assurance.py test-assurance.py && git commit -m "bin: check-assurance.py validates assurance records against the policy contract"`

### Task 3: Tier slices and the audit-pass command reference the controls

**Files:**
- Modify: `methodology-content/T1.md` (new item 7), `T2.md` (new item 18), `T3.md` (new item 14, item 1 extended), `T4.md` (element 1 and element 4 extended, inherited line), `T3.md` and `T4.md` inherited lists
- Modify: `commands/audit-pass.md`, `commands-kimi/audit-pass.md`
- Test: `test-assurance.py`, `test-session-start-split.py`

**Interfaces:**
- Consumes: control IDs and the checker's CLI from Tasks 1 and 2.

- [ ] **Step 1: Write the failing test** `slices_and_commands_name_only_contract_ids`: collect every `AC-[A-Z]+` token in `methodology-content/T[1-4].md`, `commands/audit-pass.md` and `commands-kimi/audit-pass.md`; assert the set is non-empty for T3, T4 and both commands, and that every token is a contract control ID. This is the one permitted document test: it catches a renamed or invented ID, not wording.
- [ ] **Step 2: Run it.** → FAIL (no IDs yet).
- [ ] **Step 3: Edit the slices.** Each addition is at most about 600 characters, names the control IDs, and points to the policy by the same `find "$HOME/.claude/plugins" ... -path '*vc-roe*'` form T2 item 17 uses for `check-goal-block.py`. Content: **T1 item 7**: state the acceptance and the check, and identify what was checked (commit or file) well enough to avoid ambiguity; a boundary or persistent-state trigger activates `AC-REVIEW` or `AC-RESTORE` at any tier. **T2 item 18**: changed requirements keep a durable link to their check (`AC-TRACE`), and evidence records the commit plus dirty-state identity (`AC-EVIDENCE`). **T3 item 14**: `AC-REVIEW` before integrating a trust-boundary change, `AC-RESTORE` before relying on a backup, evidence tied to source, configuration and artefact, all recorded in the project's `## Assurance` table; T3 item 1 (audit-pass) gains that the pass records reviewer, reviewed revision, scope and findings. **T4 element 1**: the close states the applicability of all four controls and runs `check-assurance.py <record> --tier T4 --release --revision <release sha>`; **element 4**: compliance trace links each control row; evidence whose inputs changed is marked stale. Update the T3 and T4 "Inherited" lines to name T1 item 7 and T2 item 18.
- [ ] **Step 4: Edit both audit-pass commands.** Step 3 of each gains: the `OBS-AUDIT-N` row names the boundary changed, and when the pass runs it is recorded as an `AC-REVIEW` row (reviewer, reviewed revision, independence basis, findings and dispositions). Keep the two files identical apart from the registration line they already differ in.
- [ ] **Step 5: Run.** `python3 test-assurance.py` → PASS; `python3 test-session-start-split.py` → PASS (every part under 9,500, at most 3); `python3 bin/check-goal-block.py --self-test` → PASS.
- [ ] **Step 6: Commit.** `git add methodology-content commands commands-kimi test-assurance.py && git commit -m "slices: T1-T4 and audit-pass reference the shared assurance controls"`

### Task 4: Codex policy, documentation status and release

**Files:**
- Modify: `policy/shared.md`, `README.md`, `docs/proposals/shared-assurance-controls.md` (status line only), `CHANGELOG.md`, `CONTRIBUTING.md` (validation list), `.claude-plugin/plugin.json`, `kimi.plugin.json`, `PROJECT.md` (pending-work section)
- Test: every `test-*.py`

**Interfaces:**
- Consumes: all of the above.

- [ ] **Step 1: `policy/shared.md`.** One paragraph: when work touches a trust boundary, persistent state or a release, apply the controls in `assurance.md`, record their applicability in the project's records, and report a structural check as structural. Tool-neutral: no hook, banner, tier name or Claude command.
- [ ] **Step 2: Status of each document.** Proposal: the status line becomes "implemented in v<version>; verified by `test-assurance.py`", with the rest unchanged as the historical brief. README: a section "Assurance controls" that says what is implemented (policy, checker, slice references), what is verified (by which tests) and what remains manual (independence of a reviewer, the success of a restore). PROJECT.md: the pending-work paragraph is replaced by a pointer to the policy.
- [ ] **Step 3: Version and changelog.** Bump both manifests to the next minor; changelog entry in the v1.26.0 shape (what changed, evidence with test counts and the Task 2 negative test); add `test-assurance.py` to CONTRIBUTING's validation list.
- [ ] **Step 4: Full verification.** Every `test-*.py` exits 0 (run under `bash -c` to read exit codes); `bash bin/publish-audit.sh` → 0 deny hits; `bash bin/publish-audit-state.sh --history` → rc 0; gitleaks clean; `python3 bin/check-assurance.py docs/examples/assurance-record.md --revision 1111111` → 0, and with `--release` → 1.
- [ ] **Step 5: Release.** Confirm with the operator, then push `main`, create and push the annotated tag, check the tag at the remote equals the manifest version, run `claude plugin update vc-roe@vibe-coding-rules`, and compare the installed copy with the tag.

---

## Self-review notes

- **Spec coverage.** Shared policy, IDs, applicability, evidence fields and tier matrix: Task 1. Synthetic example with an applicable, a justified non-applicable, a failed and a stale control: Task 1 step 4, checked in Task 2. Focused validation with rejections and justified non-applicability: Task 2. T2 links, T3 boundary review and recovery, T4 full accounting: Task 3. Codex reachability without session behaviour: Task 4 step 1. Proposed, implemented and verified distinguished: Task 4 step 2. Required validation, gitleaks, publish audit, version, tag and install: Task 4.
- **Acceptance criteria.** Missing evidence, mismatched revision and a failed check cannot be labelled passed: `passed_without_evidence_is_rejected`, `passed_at_other_revision_must_be_stale`, `passed_with_failed_result_is_rejected`. An accepted risk stays distinct from a pass: `accepted_risk_is_counted_but_not_passed`. The reviewer and reviewed content are identified, and a harness name alone is not independence: `review_needs_reviewer_and_basis`, `harness_name_alone_is_not_independence`. A backup is not a restore test: `backup_cannot_pass_restore`. Non-applicability with a reason incurs nothing else: `justified_not_applicable_passes`.
- **Not in scope.** Automating triggers (the checker cannot know that a change touched a boundary), machine-readable manifests beyond the markdown table, and any hook. The proposal says to build automation only after the contract is stable.
