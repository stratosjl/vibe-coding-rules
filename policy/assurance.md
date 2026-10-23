# Shared assurance controls

This file is the one normative definition of four assurance controls. The vc-roe tier slices, the audit-pass command and the shared Codex policy refer to it and do not restate it. A control applies when its trigger is met, at any tier: a low tier cannot remove an applicable security or data-recovery control. A trigger also prompts a reassessment of the tier, but it never promotes a project on its own; moving a project to T4 stays an explicit owner decision.

The effective requirement for a piece of work is the union of three things: the tier minimum below, every control whose trigger the work meets, and the project's own obligations.

## The four controls

### AC-TRACE: requirements-to-test traceability

- **Trigger:** a requirement, acceptance criterion or contract is added or changed, and the change has consequences someone relies on.
- **Evidence:** the requirement's identifier, its acceptance criterion, the check that exercises it (an automated test, a review or a manual check where a test is inappropriate), the observed result and the revision it ran against. A test that merely repeats an edit is not evidence.
- **Closes when:** every in-scope requirement has a check whose result is recorded at the current revision, or a recorded gap with a reason.

### AC-REVIEW: independent policy and isolation review

- **Trigger:** the work changes authorization, egress, credentials, execution isolation, or a similar trust boundary, whatever the tier.
- **Evidence:** who or what reviewed the change, the revision or diff reviewed, the scope and access the reviewer had, the findings and their dispositions, and the basis for calling the review independent of the implementation. A different model family can reduce shared blind spots, but a second harness or model name alone does not establish independence. Human review remains a valid route.
- **Closes when:** a review meeting the above is recorded against the revision being integrated. A mandatory review that cannot be obtained stays blocked; it is not waived by its absence.

### AC-RESTORE: tested restoration

- **Trigger:** persistent state matters, or a change can destroy or migrate it. A stateless system, or one whose outputs are rebuilt from source, records the control as not applicable with that reason.
- **Evidence:** a restore into an isolated environment, a check of data and application consistency, proof that the credentials and configuration the restore needs can be recovered, and the measured recovery time where an objective is claimed. A restore must never overwrite production data to obtain evidence. A record that a backup succeeded is not evidence of a restore.
- **Closes when:** a restore test is recorded before the backup is relied on, and repeated after any material change to backup format, storage, encryption, migration or the recovery procedure, and on the project's documented schedule.

### AC-EVIDENCE: revision-bound evidence

- **Trigger:** any test, review or restore result is used to support a release or a claim of readiness.
- **Evidence:** the exact identity of what was checked: the commit, plus a digest of the tree or a changed-file manifest when the working tree was dirty; the relevant configuration and dependency versions; the command or check; the time and result; and its coverage. For model-based behaviour, also the model, provider and policy versions the claim depends on. Sensitive details stay in approved private storage and are referenced through a sanitised manifest.
- **Closes when:** a complete evidence manifest is retained for the release, and every result whose inputs have changed since is marked stale.

## Tier matrix

| Control | Lightweight floor | T3 | T4 |
|---|---|---|---|
| AC-TRACE | T1: state the acceptance and the check. T2: keep durable links for changed requirements. | Stable requirement IDs and test or review links for consequential behaviour and changed contracts. | Account for every in-scope release requirement, its evidence and any gap, with explicit reasons for exclusions. |
| AC-REVIEW | Applies whenever the trigger is met, regardless of a low tier. | Required before integrating affected changes. | As T3, and record reviewer independence, findings, dispositions and the reviewed revision in the release evidence. |
| AC-RESTORE | Applies when persistent state matters or a change can destroy or migrate it. | Demonstrate recovery before relying on the backup; retest after relevant changes and on a documented schedule. | Establish recovery objectives and demonstrate an application-consistent restore, reconciliation and rollback compatibility before operational readiness. |
| AC-EVIDENCE | T1: identify the tested content well enough to avoid ambiguity. T2: record the commit plus dirty-state identity or a file manifest. | Tie test and review evidence to the exact source, relevant configuration and artefact identity. | Retain a complete release evidence manifest and invalidate affected evidence when its inputs change. |

At T3 a project records an applicability decision for AC-REVIEW and AC-RESTORE. At T4 it records one for all four controls. A decision may be "not applicable" with a reason: a document-only or stateless case does not incur unrelated runtime tests or recovery exercises.

## The record

Projects keep assurance in an existing record, such as `open-issues.md` or a release document, as one markdown table under a heading `## Assurance`, one row per control and scope. No separate database is needed. The columns, in this order:

| Column | Holds |
|---|---|
| Control | One control ID from the contract below. |
| Requirements | The requirement IDs the row covers, or `-`. |
| Applies | `yes` or `no`. |
| Reason | Why the control applies or does not; required when `Applies` is `no` and for an accepted risk. |
| Owner | Who is accountable for closing the row. |
| Status | One of the statuses below. |
| Evidence | Where the evidence is: a test name, a report path, a review link. |
| Kind | What the evidence is: one of the kinds below. |
| Result | `pass`, `fail` or `n/a`, as observed. |
| Revision | `<sha>`, or `<sha>+dirty:<digest>` with a digest of at least 12 hex characters; `n/a` when not applicable. |
| Reviewer | For AC-REVIEW: who or what reviewed. |
| Independence | For AC-REVIEW: the basis for calling the review independent. |
| Trigger | For an accepted risk or a blocked row: the closure trigger, as an ISO date, a version gate or a coupling to another event. |

Statuses: `planned`, `passed`, `failed`, `blocked`, `stale` (passed once, but at a revision or with inputs that have since changed), `not-applicable`, and `accepted-risk`. An accepted risk needs a reason, an owner and a trigger, and it lapses when its date passes. **An accepted risk is never a passing result**, and a summary must count the two separately.

A row may say `passed` only when its evidence is present, of a kind that can pass that control, observed as `pass`, and bound to the revision being released.

## What a structural check can and cannot prove

`bin/check-assurance.py` in this repository validates a record against the contract below. It can show that a row labelled `passed` has evidence of an acceptable kind, a `pass` result and the right revision, that a review row names a reviewer and an independence basis that is more than a tool name, and that a non-applicable row gives a reason. It cannot show that a review was independent in fact, or that a restore succeeded. Those remain judgements a person makes from the evidence the row points to, and a clean run of the checker must be reported as a structural result, not as proof of either.

## Contract

The checker reads its rules from this block. Edit the block and the prose together.

```json assurance-contract
{
  "version": 1,
  "controls": {
    "AC-TRACE": {"name": "Requirements-to-test traceability", "passing_kinds": ["test", "review", "manual"]},
    "AC-REVIEW": {"name": "Independent policy and isolation review", "passing_kinds": ["review"]},
    "AC-RESTORE": {"name": "Tested restoration", "passing_kinds": ["restore-test"]},
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
