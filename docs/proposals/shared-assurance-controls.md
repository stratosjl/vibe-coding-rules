# Proposal: shared assurance controls

Date: 2026-09-21. Recommendation: define four shared assurance controls, apply
tier-based minimums and activate additional requirements when the work touches
the relevant risk. Make their applicability explicit at T4; do not reserve all
four controls exclusively for T4 or introduce T5.

Status: proposed, not implemented. This document changes no active tier,
hook, permission or release behavior. It is a source-repository implementation
brief, not a plugin release.

## Scope and evidence

Assessment baseline: source revision
`533f568e1900cffe842c732ac0b473a55d0574b0`.
Examined the distributed T0-T4 slices, README, shared Codex policy, audit-pass
command and relevant hook references. Tier slices were examined as methodology
source, not imported as Codex session instructions.

The separately referenced canonical methodology v4.1 and private standing-rule
documents are outside this source set. Findings apply to the checked repository
and its shared Codex contract, not every historical or private VC-ROE document.

## What exists now

| Control | Existing provision | Assessment |
|---|---|---|
| Requirements-to-test traceability | T3 tracks edits, obligations and scope. T4 requires regression coverage and conditional compliance trace updates. | Partial. Session accounting and compliance-event rows do not establish a requirement -> acceptance criterion -> test -> result chain. |
| Independent policy/isolation review | T3 requires an audit pass for security-boundary and architecture changes; T4 inherits it. | Partial. The checked audit-pass contract does not specify reviewer independence, review scope or revision-bound findings. |
| Tested backup restoration | T4 requires operating-runbook updates and a project-defined smoke check; T3 references infrastructure-specific recovery routing. | No explicit general restore-test obligation found in the inspected tier rules. A backup destination or runbook is not evidence of successful recovery. |
| Evidence tied to exact revisions | T1 requires verification evidence. T2 handovers can check the last commit hash. T3 checks canonical forge state. T4 strengthens coverage reporting. | Partial. These do not consistently bind each test/review result to its source tree, relevant configuration and tested artifact. |

The [shared Codex engineering policy](../../policy/shared.md)
requires proportionate checks, provenance and honest reporting, but does not
explicitly encode these four controls. Updating only Claude tier slices would
leave a cross-tool gap.

The relevant T4 provisions are in
[the structural close](../../methodology-content/T4.md).
The existing security audit trigger is in
[T3](../../methodology-content/T3.md), with
the procedure in [audit-pass](../../commands/audit-pass.md).

## Recommended tier placement

| Control | Lightweight floor | T3 | T4 |
|---|---|---|---|
| Requirements-to-test traceability | T1: state acceptance and the check. T2: keep durable links for changed requirements. | Stable requirement IDs and test/review links for consequential behavior and changed contracts. | Account for every in-scope release requirement, its evidence and any gap; explicit reasons for exclusions. |
| Independent policy/isolation review | Activate whenever work changes authorization, egress, credentials, execution isolation or a similar trust boundary, regardless of a low label. | Required before integrating affected changes. | Retain that requirement and record reviewer independence, findings, dispositions and reviewed revision in release evidence. |
| Tested restoration | Activate when persistent state matters or a change can destroy/migrate it. Stateless or reproducible outputs may have a justified non-applicable result. | Demonstrate recovery before relying on the backup; retest after relevant changes and on a documented schedule. | Establish recovery objectives and demonstrate application-consistent restore, reconciliation and rollback compatibility before operational readiness. |
| Revision-bound evidence | T1: identify tested content sufficiently to avoid ambiguity. T2: record commit plus dirty-state identity or a file manifest. | Tie test/review evidence to exact source, relevant configuration and artifact identity. | Retain a complete release evidence manifest and invalidate affected evidence when those inputs change. |

These are proposed additions, not a claim that the distributed methodology
already mandates them. A risk trigger should activate the control immediately
and prompt tier reassessment where appropriate; it must not silently promote a
project to T4, which remains an explicit owner decision.

## One shared assurance mechanism

Define the controls once in a tool-neutral shared contract, for example an
`assurance` section or a linked `policy/assurance.md`. Have tier descriptions and
each native tool adapter reference it. Keep the required controls separate from
Claude-specific hooks, session formatting or confirmation rituals.

The effective requirements are the union of the tier minimum, risk-triggered
controls and project-specific obligations. A low tier cannot remove an applicable
security or data-recovery requirement. T4 requires an applicability decision for
all four controls; it does not require a backup restore for a system with no
state to restore, or automated tests for a prose-only requirement.

For each control, record its stable ID, applicability and reason, owner,
requirement IDs, closure trigger, evidence references, tested/reviewed revision
and status. Useful statuses are planned, passed, failed, blocked, stale and
not-applicable. Any exception needs a rationale, authorized owner and expiry or
coupling trigger. An accepted risk is not a passing test result.

Use existing project records instead of adding a second task database. A small
project can use a short table; a larger system can validate a machine-readable
manifest and render the same information. Build automation only after the
contract is stable. A hook can check evidence structure and stale revisions; it
cannot prove that a review was independent or a recovery actually succeeded.

## Practical meanings

Traceability should connect a requirement to its acceptance criteria, checks,
observed results and release. Include review or manual evidence when automated
tests are inappropriate. Do not require a test that merely repeats an edit.

Independent review must be separate from implementation and inspect the actual
boundary changes. A different model family can reduce shared blind spots, but
does not itself establish independence or correctness. Record who or what
reviewed the change, access/scope, findings and their disposition. Human review
remains a possible route. Mandatory unavailable review stays blocked.

A restore exercise must restore into an isolated environment, check data and
application consistency, verify needed credentials/configuration can be
recovered, and measure the recovery objective where one is claimed. It must not
overwrite production data just to obtain evidence. Repeat when backup format,
storage, encryption, migration or recovery procedures materially change.

Exact evidence identity includes more than a branch name or HEAD SHA if the
working tree was dirty. Record a tree/artifact digest or changed-file manifest,
relevant config and dependency versions, command/check identity, timestamp,
result and coverage. For model-based behavior, include the model, provider and
policy versions relevant to the claim. Keep sensitive details on approved
private storage and reference them through sanitized manifests where necessary.

## Implementation path

1. Add the shared normative control definitions and risk triggers. Preserve the
   existing user-authorization rule and runtime permission boundaries.
2. Update T1/T2 with lightweight evidence identity and acceptance links; update
   T3 with explicit boundary review and recovery applicability.
3. Make T4's regression, runbook and compliance-trace closure reference the same
   controls, including complete coverage and stale-evidence handling.
4. Update Codex's shared policy and other tool adapters so these requirements
   are reachable without importing another harness's session behavior.
5. Add focused checks for omitted requirements, stale revision evidence,
   self-review being mislabeled independent, and a backup being mislabeled as a
   tested restore. Exercise justified non-applicability too.
6. Review the generic methodology change separately from private project facts;
   release it through the repository's normal validation and publication route.

## Implementation continuation brief

Start from the current canonical branch after reading AGENTS.md, PROJECT.md and
CONTRIBUTING.md. Reconcile local and remote work before editing. This proposal
was assessed against the revision above; recheck findings against newer source.

Implement the shared assurance contract and tier references described here.
Keep one normative definition for each control, with concise references from
native adapters. Preserve existing authorization and runtime permission rules.
Do not import Claude-specific hooks, banners, memories or confirmation rituals
into Codex. Do not change provider permissions or global security settings.

Begin with the shared policy, control IDs, applicability rules, evidence fields
and tier matrix. Add a small synthetic example showing an applicable control,
a justified non-applicable control, a failed check and stale revision evidence.
Use existing project records for persistence. Do not build a new database or
mandatory automation service for this change.

Then add focused validation where it can check the contract, without claiming
that structural validation proves independent review or a successful restore.
Test both rejection and justified non-applicability. Avoid brittle tests that
only assert that a sentence appears in a Markdown file.

## Acceptance criteria

- Each control has one shared definition, an applicability trigger, required
  evidence and a closure rule that tier and adapter references can reach.
- T2 retains lightweight requirement/evidence links; T3 makes boundary review
  and relevant recovery obligations explicit; T4 accounts for all applicable
  controls and gaps in release evidence.
- Missing evidence, a mismatched revision and a failed check cannot be labeled
  passed. An accepted risk remains distinct from a passing test.
- A review record identifies the reviewer and reviewed content. A second
  harness name alone cannot establish independent review.
- A backup-success record cannot substitute for a restore-test result.
- A stateless or document-only case can record non-applicability with a reason;
  it does not incur unrelated runtime tests or recovery exercises.
- Codex's shared policy and the supported native adapters have consistent
  access to the controls without acquiring another tool's session behavior.
- Documentation distinguishes proposed, implemented and verified behavior.

Run the repository's required validation and relevant adapter/policy checks.
Inspect the outgoing content, run Gitleaks and the combined publication audit.
If implementation changes versioned methodology or behavior, follow
CONTRIBUTING.md's version, tag and installation rules. Merely publishing this
proposal does not require a plugin version bump or release tag.

At completion, update this proposal's status with implementation revision and
validation references. Record unresolved work in the project's shared records.
