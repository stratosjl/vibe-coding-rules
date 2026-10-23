---
description: Declare an audit-pass owed for the previous session. Locks the next session's agenda accordingly per feedback_audit_pass.md.
---

User invoked `/audit-pass`.

(Registered as `/vc-roe:audit-pass`: the CLI namespaces plugin commands with the plugin id; the bare `/audit-pass` alias resolves to this command.)

Action:

1. State that the previous session's handover footer should declare `Audit-pass owed for THIS session: Yes` with the trigger that caused it.
2. Confirm the trigger by asking the operator: `Which of the audit-pass triggers applies for the previous session? (architecture / theme defaults / deploy pipeline / security boundaries / irreversible production change / other)` Pause for the answer.
3. Once the operator answers, add a forward-obligation row to the project's `open-issues.md`: `OBS-AUDIT-N | OPEN | Forward obligation (next session) | Audit-pass owed for session NN. Reason: <trigger>. Mandate per feedback_audit_pass.md`.
   The row names the boundary or area that changed. When the audit-pass session runs, record the pass itself as an `AC-REVIEW` row in the project's `## Assurance` table (shared assurance controls, `policy/assurance.md` in the vc-roe plugin): who or what reviewed, the revision reviewed, the scope, the findings and their dispositions, and the basis for calling the review independent. A second tool or model name alone is not that basis.
4. State explicitly: the next session's opening prompt should reference this audit-pass mandate. Optimisation work should NOT begin until the audit-pass session has run and the operator has confirmed the pass.
5. The audit-pass policy is INTER-session and has no scope gate (per the standing rule). Operator confirms the pass before any optimisation begins.
