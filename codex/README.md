# Native Codex continuation

This integration checks an explicitly registered work contract when a native
Codex turn ends. Missing deliverables or failed required checks keep the contract
incomplete. If authorized work can proceed, the Stop hook requests continuation.
It does not invoke Claude hooks or activate Claude methodology slices.

## Enforcement boundary

Codex's supported Stop response, `decision: block`, creates continuation feedback.
It does not retract an assistant message already displayed. An installation must
therefore prove both a blocked Stop event and subsequent execution. Hook discovery
or a model promising to continue is insufficient. See the
[native lifecycle contract](https://learn.chatgpt.com/docs/hooks).

The hook cannot infer a complete task graph from arbitrary natural language. The
prompt hook reminds the agent to register work with multiple deliverables. Until
registration occurs, Stop enforcement is inactive. The quality of the selected
acceptance criteria and the truth of declared external blockers still require
review. File existence alone establishes presence; meaningful quality checks
belong in the contract. This is a guard against premature completion of registered
work, not a proof that every user request has been captured correctly.

## Install and verify

Run `python codex/test_continuation.py` before installation. Then run
`python codex/install_continuation.py` from this checkout. The installer copies
the runtime into `CODEX_HOME/vc-roe/runtime/`, backs up the existing hook file and
merges five native handlers. It preserves other hooks and does not change Claude
configuration, native memory, plugin availability or sandbox settings.

Review and trust the exact new definitions through Codex's native hook review.
Start a fresh native session and check discovery and execution. The installer
does not bypass trust or claim that a running desktop consumer refreshed its
configuration. Keep the installer receipt and the runtime file hash with the
local verification record. Roll back by reviewing the backup against current
bytes and restoring only this integration's changes; do not overwrite later edits.

The repeatable native probes are:

```text
python codex/probe_continuation.py positive
python codex/probe_continuation.py negative
python codex/probe_continuation.py lifecycle
```

Each creates a disposable native thread and synthetic files in Codex-owned
temporary storage. The positive case must observe a blocked Stop followed by
artifact completion and a successful required check. The negative case disables
only this Stop handler through a process-local override while preserving the
other hook settings; B must remain absent. The lifecycle case interrupts an
active turn, compacts its context and submits an explicit stop; B must stay absent.
The probes return nonzero when their acceptance conditions fail and retain raw
evidence locally. They select full file access only for their disposable threads
so the model can write both the fixture and its contract. Global sandbox settings
are unchanged.

## Register authorized work

Keep shared project facts in their existing documents. The contract is a temporary
execution record stored under `CODEX_HOME/vc-roe/continuation/<session-id>.json`.
It records the active objective, evidence requirements and unresolved inputs.
Do not put private runtime contracts in a public repository.

Create a plan JSON with an absolute `cwd`, an `objective` and a `tasks` array:

```json
{
  "cwd": "/absolute/workspace",
  "objective": "Prepare and verify both reports",
  "questions": {
    "report_date": {"text": "Which reporting date should report B use?", "status": "pending"}
  },
  "tasks": [
    {
      "id": "report_a",
      "action": "Create report-a.txt and run its content check",
      "artifacts": [{"path": "report-a.txt"}],
      "checks": [{"id": "content", "argv": ["python", "check_report_a.py"], "inputs": ["check_report_a.py"]}]
    },
    {
      "id": "report_b",
      "action": "Create report-b.txt using the supplied date",
      "wait_for": ["report_date"],
      "artifacts": [{"path": "report-b.txt"}]
    }
  ]
}
```

Use the interpreter available on the current machine. Invoke the installed script:

```text
python continuation.py init --session SESSION_ID --plan plan.json
python continuation.py status --session SESSION_ID
python continuation.py check --session SESSION_ID --task report_a --check content
python continuation.py answer --session SESSION_ID --question report_date --answer ACTUAL_USER_ANSWER
```

Inside a native session, `CODEX_THREAD_ID` supplies the default identifier when
available. An explicit `--session` avoids ambiguity in external validation tools.
The check command is an explicit tool operation; hooks never execute registered
check commands themselves. Checks have a 60-second bound. Their receipts record
exit status, output hashes and the checked inputs and artifacts. A later change
invalidates the receipt. A check that changes its own evidence does not pass.

`depends_on` lists preceding task IDs. `wait_for` lists question IDs. Record a
concrete `external_block` only when a real prerequisite prevents that task.
Other independent work continues. When everything remaining needs input, the hook
reports incomplete status and the actual question text or external blockers.
It does not retry indefinitely or label that state complete.

Artifacts must be nonempty files inside the workspace, without link traversal.
An optional `sha256` requires exact content. A `done` or `status` flag does not
replace artifact and check evidence. Guard receipts are local operational evidence;
they are not cryptographic attestations against a malicious process with access
to the same user's files.

## Changes, interruptions and resumption

Use `revise --plan revised.json` to change constraints or add tasks. Existing
check receipts are retained but must still match current evidence. Removing
tasks or replacing the objective requires `--user-instruction` recording the
actual scope change. Revision preserves a prior-plan snapshot and does not resume
stopped work.

The UserPromptSubmit hook preserves the objective across ordinary side questions.
Direct stop commands, including `stop everything else`, `close session` and the
tested Greek forms, halt continuation. The parser intentionally does not interpret
quoted text or arbitrary prose as commands. Other stop phrasings still depend on
the assistant applying the user's instruction and recording stopped state.
Use the runtime interrupt control when stopping an active turn.

The Interrupt hook writes an atomic halt marker even while a check holds the
contract lock. It records interruption, not termination of every possible tool,
process or child agent. This integration starts no background jobs. The operator
of other jobs must stop them and verify their termination separately.

Explicit resumption uses `resume --user-instruction ACTUAL_USER_INSTRUCTION`.
SessionStart and UserPromptSubmit supply the contract summary to the model.
PostCompact reports preserved state through its supported warning output. A side question cannot
silently reactivate stopped work. Three continuation attempts per user input are
the maximum; exhaustion produces an incomplete-state warning. That limit protects
against repeated ineffective continuations and is not a completion signal.

The supported enforcement scope is the main native thread with a contract bound
to its session and workspace. This integration does not claim independent
subagent lifecycle enforcement or refreshed desktop behavior without separate
consumer evidence.

Both prose passes completed: mechanical scan and reader review for fluency,
clear references, technical terminology and verification limits.
