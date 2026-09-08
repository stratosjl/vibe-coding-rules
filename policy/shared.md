# Shared engineering policy

Keep changes within the user's requested outcome and preserve unrelated work.
Read the project's shared facts, operating contracts and current task records
before changing the affected area. Keep durable facts in those documents so all
tools use the same project state.

Scale planning and verification to the change's size and consequences. Use the
project's technical prerequisites and evidence-based release checks. Test the
behavior that could fail, including a negative case when changing a guard.
Report failed checks and limits on what was verified. A completed edit alone is
not evidence that a deployment or live operation succeeded.

Keep secrets out of logs and source control. Preserve data provenance and report
the scope of searches, measurements and tests. Record decisions and remaining
work in the project's existing records when continuity requires it.

Authorization applies to the requested work and its necessary steps. Honor
authorization already given; ask only when the action exceeds it or required
information is missing. Technical prerequisites remain in force after approval.
Runtime permissions are controlled by the active tool environment; an instruction
file cannot grant access or change that environment's approval policy.
