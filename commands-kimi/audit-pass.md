---
description: Declare an audit-pass owed for the previous session. Locks the next session's agenda accordingly per feedback_audit_pass.md.
---

User invoked `/audit-pass`.

Action:

1. State that the previous session's handover footer should declare `Audit-pass owed for THIS session: Yes` with the trigger that caused it.
2. Confirm the trigger by asking the operator: `Which of the audit-pass triggers applies for the previous session? (architecture / theme defaults / deploy pipeline / security boundaries / irreversible production change / other)` Pause for the answer.
3. Once the operator answers, add a forward-obligation row to the project's `open-issues.md`: `OBS-AUDIT-N | OPEN | Forward obligation (next session) | Audit-pass owed for session NN. Reason: <trigger>. Mandate per feedback_audit_pass.md`.
4. State explicitly: the next session's opening prompt should reference this audit-pass mandate. Optimisation work should NOT begin until the audit-pass session has run and the operator has confirmed the pass.
5. The audit-pass policy is INTER-session and has no scope gate (per the standing rule). Operator confirms the pass before any optimisation begins.
