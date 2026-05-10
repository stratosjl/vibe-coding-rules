# Changelog

All notable changes to vc-roe (vibe-coding-rules-of-engagement).

The plugin follows semantic versioning. Version is single-source-of-truth in `.claude-plugin/plugin.json` and mirrored to the `ROUTINE_VERSION` constant in every hook under `hooks/`.

## 1.3.0 - 2026-05-10

Closes `OBS-vcroe-multi-chat-contamination-01`. Adds a chat-claim primitive so multiple Claude Code chats opened against the same project cannot silently contaminate each other's working tree. Minor-class semver because new hook behaviour is added (a SessionEnd hook entry, plus new content blocks in session-start.py and stop.py).

What changed:

- **SessionStart acquires a chat-claim.** On every session open, `hooks/session-start.py` writes a JSON claim file at `~/.claude/projects/<cwd-dashed>/chat-claim.json` containing `{"session_id": ..., "ts": ..., "host": ...}`. If a claim from a different session already exists and is younger than the configured TTL (default 8 hours, override via env var `VC_ROE_CLAIM_TTL_HOURS`), SessionStart prepends a `## CHAT-CLAIM CONFLICT` banner above the methodology slice so the assistant halts mutations and surfaces the conflict to the operator. Same-session resumes refresh the ts; stale orphans (TTL-expired or corrupt) are taken over without prompting.
- **Stop hook refreshes the claim ts per turn.** `hooks/stop.py` now refreshes the claim's ts on every assistant-turn-end so the claim stays alive across long user-think-time gaps. Refresh fires before any early-return path (no transcript / no anchor / silent-stop block) so claim hygiene is independent of heartbeat-cadence logic. Stop never deletes another session's claim.
- **New SessionEnd hook releases the claim cleanly.** `hooks/session-end.py` (added) fires on chat close and deletes the claim file iff its session_id matches the calling SessionEnd's session_id. Mismatch (another session has taken over via TTL) is a no-op. Corrupt claims are deleted defensively.
- **`hooks/hooks.json` registers the SessionEnd hook entry.** Confirmed `SessionEnd` is a supported event in Claude Code 2.1.138.
- **`test-chat-claim.py` added.** 11-case regression suite covering: take-new (no existing claim), same-session resume, refuse on within-TTL conflict, take-orphan on past-TTL conflict, take-orphan on corrupt JSON, env-var TTL override, Stop refreshes own claim, Stop leaves other-session claim alone, SessionEnd releases own claim, SessionEnd does not release other-session claim, SessionEnd no-session-id no-op. 36 assertions; all pass at v1.3.0.
- **`ROUTINE_VERSION` bumped to `1.3.0`** across all five hooks (session-start, user-prompt-submit, post-tool-use, stop, session-end); `.claude-plugin/plugin.json` `version` field bumped to match.

What this does NOT change: existing tier-detection logic, heartbeat-cadence semantics, silent-stop blocker (OBS-50-01), or any pre-push / post-publish audit behaviour. Hook code in `user-prompt-submit.py` and `post-tool-use.py` is byte-identical to v1.2.1 aside from the lockstep `ROUTINE_VERSION` constant. The chat-claim file lives entirely under the operator's `~/.claude/projects/...` tree; it is not git-tracked and not part of any project's working tree.

Why now: at session 54 close the operator observed empirically that another chat had committed s55-prep work into the working tree between s54 close and s55 open, contaminating session-handover sequencing. The pattern materialized again between s54 close and s55 open (commit `b6eea7b`, the W3 service-provider DENY-pattern adds, was authored by a sibling chat). Operator directive: "for the next session schedule to implement the simplest way to have multi-chat access to modifications to vc-roe. i can see that each chat contaminates the other".

Operator-side action required to pick up v1.3.0 hooks: `claude plugin update vc-roe@vibe-coding-rules` writes the new files but the running Claude Code process keeps invoking the v1.2.1 hooks from its in-memory snapshot. Close every Claude Code window/process and reopen to pick up v1.3.0 cleanly.

## 1.2.1 - 2026-05-10

Closes `OBS-vcroe-publish-audit-overlay-fp-01`. The pre-publish leak scan in `bin/publish-audit.sh` now uses `git grep` instead of `grep -rnE` so it scans only git-tracked files. The previous behaviour walked the entire working-tree filesystem and picked up gitignored operator-side overlay files (most prominently `methodology-content/T4-[OPERATOR].md`, an operator-private addon that is gitignored but lives inside the working tree) as phantom DENY-pattern hits, blocking pre-push with up to 54 false-positive deny hits per run. The temp-move-aside workaround used during the v1.2.0 ship-cycle is no longer needed; future ships run pre-push cleanly with the overlay applied. Patch-class semver (no plugin runtime change; hook code byte-identical to v1.2.0 aside from the lockstep `ROUTINE_VERSION` constant).

What changed:

- **`bin/publish-audit.sh` scans git-tracked files only.** Both the DENY loop (line 55) and the WARN loop (line 65) replace `grep -rnE "$pat" .` with `git grep -nE "$pat"`. The downstream `grep -vE "$SCAN_EXCLUDE"` filter and the formatting / counting pipeline are unchanged. The fresh-clone post-publish state audit at `bin/publish-audit-state.sh` is unaffected because it operates inside a temp-clone of the public repo where no untracked overlay files exist; its tracked-file output set is identical to before.
- **`ROUTINE_VERSION` bumped to `1.2.1`** across all four hooks; `.claude-plugin/plugin.json` `version` field bumped to match. Hook code is byte-identical to v1.2.0 aside from the constant.

What this does NOT change: pre-push DENY pattern set; post-publish state audit semantics (still scans tracked files in a fresh clone); WARN-set output for the public commit (92 WARN at HEAD, 87 WARN per-commit in history walk, same numbers as v1.2.0). The operator-side ignore-pattern that hides `T4-[OPERATOR].md` from `--exclude-standard` listings is unrelated to this fix and remains in place.

Plugin-snapshot reminder: `claude plugin update vc-roe@vibe-coding-rules` writes the new files but the running Claude Code process keeps invoking the v1.2.0 hooks from its in-memory snapshot. Close every Claude Code window/process and reopen to pick up v1.2.1 cleanly. (Hook code is byte-identical aside from the `ROUTINE_VERSION` constant; the practical effect of staying on v1.2.0 hooks is only the version field reported in log entries.)

## 1.2.0 - 2026-05-10

Closes `OBS-vcroe-historical-leak-01`. Two operator-internal project codenames baked into the public repo at the v1.1.0 baseline (and surfaced as 21 of the 113 WARN hits at v1.1.5) have been scrubbed from every pre-v1.2.0 commit via a `git filter-repo --replace-text` history rewrite, with both codename patterns now promoted from `WARN_PATTERNS` to `DENY_PATTERNS` in `bin/audit-patterns.sh` so any future leak is blocked at pre-push. Minor-class semver to signal the commit-hash discontinuity (every commit on `main` now has a different SHA than at v1.1.5; tags `v1.1.2` through `v1.1.5` re-pointed at the rewritten signed commits). No functional change to the plugin runtime.

What changed:

- **History rewrite via `git filter-repo --replace-text`.** All eight commits on `main` from `Initial public release of vc-roe v1.1.0` through `v1.1.5` were rewritten with two textual substitutions, replacing the two operator-internal codenames with opaque placeholders (`[INT-A]` and `[EXAMPLE-PROJ]`) across CHANGELOG narrative, code-comment forensic tags, methodology-content pedagogical examples, and one test-fixture docstring. After the substitution, every rewritten commit was re-created via `git filter-branch --commit-filter 'git commit-tree -S "$@"'` so all eight commits carry the new SSH-key signature (`SHA256:/2bwM1kx6Rk…`). Annotated signed tags `v1.1.2`–`v1.1.5` were re-created at the new commit hashes with their original messages preserved (the v1.1.2 tag annotation itself contained one residual codename reference that the in-tree-only regex did not catch; that reference was scrubbed in the re-created tag message). `git push --force-with-lease=main:b91c565…` against `origin/main` and `git push --force --tags origin` landed the rewritten tree.
- **Both codename patterns promoted from `WARN_PATTERNS` to `DENY_PATTERNS` in `bin/audit-patterns.sh`.** Future commits attempting either codename are blocked at pre-push by `bin/publish-audit.sh`. The path-prefix DENY entry for the second codename (which was inadvertently substituted to its placeholder form by the regex during the history rewrite, because the regex matched with non-word boundaries on either side) is restored to its original semantic form. The WARN-block "scheduled for scrub" comments are removed.
- **`ROUTINE_VERSION` bumped to `1.2.0`** across all four hooks; `.claude-plugin/plugin.json` `version` field bumped to match. Hook code byte-identical to rewritten v1.1.5 aside from the constant.

What this does NOT change: pre-rewrite commit hashes (e.g. `b91c565`, `a44d819`, `5ef1add`) remain visible via direct URL on GitHub for some time before garbage collection runs, per GitHub's standard semantics. A local backup ref `pre-leak-scrub-backup-20260510` retains the pre-rewrite tree for any forensic recovery; this ref is local-only and not pushed. Plugin runtime and hook behaviour are unchanged from v1.1.5; the codename substitutions land only in code comments, log strings, CHANGELOG narrative, and methodology-content pedagogical examples — never in execution paths.

What this WILL change for any external clones: every existing clone diverges from `origin/main`. `git fetch && git reset --hard origin/main` syncs a clone to the rewritten tree (this repo's standard sync recipe). At publication time of v1.2.0 the maintainer was the only known user; if any forks or downstream consumers exist, they need the same reset.

Plugin-snapshot reminder: `claude plugin update vc-roe@vibe-coding-rules` writes the new files but the running Claude Code process keeps invoking the v1.1.5 hooks from its in-memory snapshot. Close every Claude Code window/process and reopen to pick up v1.2.0 cleanly. (Hook code is byte-identical aside from the `ROUTINE_VERSION` constant; the practical effect of staying on v1.1.5 hooks is only the version field reported in log entries.)

## 1.1.5 - 2026-05-10

Closes OBS-vcroe-audit-state-emailcheck-fp-01 (false-positive DENY on `git config user.email`) and the related semantic asymmetry between pre-push and post-publish audit exit-criteria surfaced during the s52 audit-pass discharge. Patch-class semver (no plugin runtime change; hook code byte-identical to v1.1.4 aside from the lockstep `ROUTINE_VERSION` constant).

What changed:

- **`bin/publish-audit-state.sh` pins the cloned tempdir's `user.email` to the canonical public author before the inner audit runs.** After cloning the public repo into the ephemeral tempdir and validating the cloned tree contains `bin/publish-audit.sh` + `bin/audit-patterns.sh`, the script now sources `$CLONED_PATTERNS` to read `PUBLIC_AUTHOR_EMAIL` and writes that value into the cloned tempdir's local git config via `git -C "$TMPDIR/repo" config user.email "$PUBLIC_AUTHOR_EMAIL"`. The inner audit (the cloned tree's `publish-audit.sh`, which inspects `git config user.email` for an author-mismatch DENY) then sees the canonical public-repo author rather than whatever the parent shell's global git config holds. The fix is contained to the caller; the inner audit is untouched, so pre-push semantics on the operator's local working repo are unchanged.
- **`bin/publish-audit-state.sh` no longer invokes the inner audit with `--strict`.** Both the HEAD audit and the optional `--history` walk now invoke `bash bin/publish-audit.sh` plain. This equalises post-publish audit semantics with the pre-push hook, which has always called the audit without `--strict` (the pre-push hook documents `--strict` as manual-only in its own comment header). Under the previous `--strict` invocation, the wrapper exited non-zero on any WARN hit and printed "DENY-pattern hits at public HEAD" — misleading because the actual DENY count was 0; the failure came from `--strict` treating WARN as failure. With v1.1.4's known WARN bucket of 113 hits (mostly canonical public-methodology references like `D-MET-N` + `OBS-MET-X` IDs that the plugin documents about itself, plus the deferred `OBS-vcroe-historical-leak-01` patterns), the wrapper could never exit clean while WARN-strict semantics held. After the drop, post-state audit exits 0 when DENY=0; WARN signals still surface in stdout for eyeball review (the OBS-row mechanism continues to track pertinent WARN hits separately).
- **`ROUTINE_VERSION` bumped to `1.1.5`** across all four hooks; `.claude-plugin/plugin.json` `version` field bumped to match. Hook code is byte-identical to v1.1.4 aside from the constant.

What this does NOT change: `OBS-vcroe-historical-leak-01` (`[INT-A]` + `[INT-H]` baked into the public repo at v1.1.3 baseline; 21 of the 113 WARN hits) remains forwarded as a separate OBS row pending a dedicated remediation session. With the email-FP closed AND `--strict` dropped, `bash bin/publish-audit-state.sh --history` now produces a noise-free per-commit walk that can scope the remediation surface concretely. The pre-push hook on the operator's local working repo is unchanged; the DENY pattern set is unchanged; compliance posture is unchanged (WARN tracking continues via OBS-row mechanism, not via wrapper exit code).

Plugin-snapshot reminder: `claude plugin update vc-roe@vibe-coding-rules` writes the new files but the running Claude Code process keeps invoking the v1.1.4 hooks from its in-memory snapshot. Close every Claude Code window/process and reopen to pick up v1.1.5 cleanly. (Hook code is byte-identical aside from the `ROUTINE_VERSION` constant; the practical effect of staying on v1.1.4 hooks is only the version field reported in log entries.)

## 1.1.4 - 2026-05-10

Audit-infrastructure ship. The pre-publish audit gains a sourceable pattern file shared with a new post-publish state audit so the two audits cannot drift apart, plus broader DENY coverage targeting operator-private identifiers found in observed project-dir encodings. Patch-class semver (no plugin runtime change; hook code untouched aside from the lockstep `ROUTINE_VERSION` bump per the project's standing convention).

What changed:

- **`bin/audit-patterns.sh` added.** Single source of truth for `DENY_PATTERNS`, `WARN_PATTERNS`, `PUBLIC_AUTHOR_EMAIL`, and `SCAN_EXCLUDE`. Both `publish-audit.sh` (pre-push) and the new `publish-audit-state.sh` (post-push) `source` this file so the two audits scan against an identical pattern set; no copy-paste drift between the two consumers.
- **`bin/publish-audit.sh` refactored to source the pattern file** instead of inlining the deny / warn arrays. No behaviour change in the deny-set the script scans for at v1.1.3 baseline; the broader pattern coverage is in `audit-patterns.sh`.
- **`bin/publish-audit-state.sh` added.** Post-push verification: clones the public repo via HTTPS into an ephemeral tempdir and runs the cloned tree's `bin/publish-audit.sh --strict` against itself. Optional `--history` flag walks every commit on `main` and reports per-commit DENY hits as historical leaks. SSH→HTTPS URL translation is built in so the audit clone needs no SSH key in the agent (read-only public-repo access).
- **DENY pattern set extended** in `bin/audit-patterns.sh` to cover operator-private identifiers observed in real Claude Code project-directory encodings on the maintainer's machines: alternate-charset firm-name forms, native path fragments (Windows + Linux), additional path-prefix anchors, client-identifier strings sourced from observed project-dir names, and personal-side path roots. The pattern list itself is operator-private (its presence in DENY is what protects it); the file is excluded from the scan via `SCAN_EXCLUDE`.
- **WARN set extended** with two additional decision-ID prefix families and a regulator-name eyeball flag for soft review.
- **`ROUTINE_VERSION` bumped to `1.1.4`** across all four hooks; `.claude-plugin/plugin.json` `version` field bumped to match. Hook code itself is byte-identical to v1.1.3 aside from the constant.

What this does NOT do: scrub historical operator-private references already baked into the public repo at v1.1.3 baseline. Two project-name forensic tags (`[INT-A]`, `[INT-H]`) appear in `hooks/`, `methodology-content/T*.md`, `test-heartbeat.py`, and `CHANGELOG.md` historically; they are surfaced as WARN hits at v1.1.4 (not DENY) so the new audit infrastructure can ship without self-blocking. A dedicated remediation session (logged as OBS-vcroe-historical-leak-01) decides between (a) scrub-and-force-push history rewrite, (b) new-repo-with-clean-history, or (c) accept-and-document. Promotion of those two patterns from WARN to DENY follows whichever path is chosen.

Plugin-snapshot reminder: `claude plugin update vc-roe@vibe-coding-rules` writes the new files but the running Claude Code process keeps invoking the v1.1.3 hooks from its in-memory snapshot. Close every Claude Code window/process and reopen to pick up v1.1.4 cleanly. (Hook code is byte-identical aside from the `ROUTINE_VERSION` constant; the practical effect of staying on v1.1.3 hooks is only the version field reported in log entries.)

## 1.1.3 - 2026-05-09

Closes OBS-50-01 (silent-stop blocker missing in v1.1.2 Stop hook). Surfaced during the [INT-A] M0 build at session 75286faf-5ab6-429a-b797-fe6e7cf4900e on 2026-05-09: the assistant called `pnpm add -D vitest` via Bash, the tool result returned, and the next assistant turn produced zero content blocks. Claude Code ended the turn silently and no hook intercepted; the methodology log shows fifty minutes of zero hook activity for that session until the operator typed "where are we" and the next UserPromptSubmit re-engaged the cadence machinery. Semver patch (no API change, no behaviour change for healthy paths; new behaviour is strictly defensive).

What changed:

- **`hooks/stop.py` gained a silent-stop blocker.** A new `last_assistant_blocks()` helper mirrors the v1.1.2 walk-rule (HALT only on a true user-prompt boundary; tool_result lines and synthetic Claude Code metadata are transparent) but returns the raw content-block list rather than concatenated text. The new guard runs in `main()` after the anchor read and before the existing sentinel-grep path: if the most-recent agent loop's gathered blocks contain at least one `tool_use` block and zero non-empty text blocks, Stop emits `{"decision":"block","reason":"..."}` so Claude Code re-prompts the assistant for follow-up text rather than ending the chat silently. The `reason` string instructs the assistant to either continue toward the active milestone OR emit an explicit `[awaiting-user]` / `[turn-complete]` sentinel if it intends to stop. `last_assistant_text()` is refactored to derive its result from `last_assistant_blocks()` for code reuse; behaviour is preserved for all v1.1.2 inputs (text-block joins with `\n` produce the same string under either gathering order).
- **`ROUTINE_VERSION` bumped to `1.1.3`** across all four hooks; `.claude-plugin/plugin.json` `version` field bumped to match.
- **`test-heartbeat.py` extended** with three new regression cases: `case_silent_stop_after_tool_blocks_returns_block` (the [INT-A] M0 fixture shape; asserts `decision == "block"` is emitted), `case_silent_stop_with_text_does_not_block` (negative control: text in the same loop suppresses the block), and `case_silent_stop_no_tool_use_does_not_block` (edge case: an empty-content assistant turn must not trigger a false positive). The `write_transcript` helper gained a `blocks` override so a test can compose any content-block layout. Test suite is now 10 cases; all pass at v1.1.3.

What this does NOT change: the v1.1.2 limitation around long pure-text agent loops (zero tool calls for over 14 min wall-clock) remains. Neither PostToolUse nor Stop fire in that window; the OVERDUE-2X auto-advance fail-safe at the next UserPromptSubmit or PostToolUse continues to handle it cleanly. The new silent-stop guard is precisely scoped to the post-tool-result silent-end shape and does not regress any healthy sentinel-grep path.

Plugin-snapshot reminder: `claude plugin update vc-roe@vibe-coding-rules` writes the new files but the running Claude Code process keeps invoking the v1.1.2 hooks from its in-memory snapshot. Close every Claude Code window/process and reopen to pick up v1.1.3 cleanly.

## 1.1.2 - 2026-05-09

Two fixes shipped together. Both close bugs or forensic gaps in the existing heartbeat fail-safe; semver patch (no API change, no behaviour change for healthy paths).

What changed:

- **Walk-halt rule fixed in `hooks/post-tool-use.py` and `hooks/stop.py` (closes OBS-46-02).** The backwards-walk in `agent_loop_assistant_text()` (PostToolUse) and `last_assistant_text()` (Stop) previously halted at any non-assistant / non-user-with-tool_result role. Real Claude Code transcripts interleave synthetic metadata line types (`attachment`, `last-prompt`, `ai-title`, `permission-mode`, `file-history-snapshot`) between assistant text and the most-recent tool_result. The old rule halted at the first such metadata line and missed pre-tool sentinel emissions, producing repeated false OVERDUE alarms during long agent loops. New rule: HALT only on a true user-prompt boundary (role="user" whose content is NOT a tool_result-bearing block list). Any other role/type, including future unknown synthetic types Claude Code may add, is treated as transparent and skipped past. Confirmed via live reproduction at [EXAMPLE-PROJ] sessions 48 + 49 where the assistant's own heartbeat sentinel was missed by the immediately-following PostToolUse despite being on disk in the transcript.
- **`hooks/post-tool-use.py` gained a `check_marker_mismatch` helper.** Mirrors the existing helper in `stop.py` so PostToolUse also surfaces cross-process anchor-rewrite races and other cwd-related drift in soak data. Recording-only; no behaviour change. Calls inserted in `main()` after session_id validation, before anchor read.
- **`ROUTINE_VERSION` bumped to `1.1.2`** across all four hooks; `.claude-plugin/plugin.json` `version` field bumped to match.
- **`test-heartbeat.py` extended** with two new regression cases: `case_synthetic_metadata_lines_do_not_halt_walk` (confirms the new walk-rule reaches sentinels past synthetic metadata; the s46 transcript fragment that surfaced the bug is the fixture shape) and `case_user_prompt_boundary_halts_walk` (regression guard against the new permissive walk over-walking past a real user-prompt boundary into an earlier agent loop). Test suite is now 7 cases; all pass at v1.1.2.

Known limitation re OBS-48-01: when Claude Code launches with a `cwd` that does not match the project the chat is for, plus a tier auto-detect mismatch that triggers `/clear`, two SessionStart events fire for the same chat with different session_ids in different cwds. Subsequent PostToolUse and Stop events keep firing against the FIRST session_id (the auto-detected one), reading the wrong transcript_path. The new PTU `check_marker_mismatch` does not catch this case because each session's marker is keyed by its own cwd. The actual fix is operator-side: launch `claude` from inside the project directory the chat is for. Code-side detection of the OBS-48-01 pattern is queued for a future release if the operator-side workaround proves brittle.

What this does NOT change: long conversational segments inside a single agent loop with zero tool calls (the assistant is in pure-text mode for over 14 min wall-clock). Neither PostToolUse nor Stop fire in that window; there is no Claude Code hook event to bridge it. The OVERDUE-2X auto-advance fail-safe at the next UserPromptSubmit or PostToolUse continues to handle it cleanly without operator intervention; the heartbeat block discipline at the start of the recovery reply remains the assistant's responsibility per D-MET-41.

Plugin-snapshot reminder: `claude plugin update vc-roe@vibe-coding-rules` writes the new files but the running Claude Code process keeps invoking the v1.1.1 hooks from its in-memory snapshot. Close every Claude Code window/process and reopen to pick up v1.1.2 cleanly.

## 1.1.1 — 2026-05-09

Fixes the heartbeat-cadence reliability gap surfaced at [EXAMPLE-PROJ] session 38 (six OVERDUE alarms in one session, four "silent drops" of spec-correct sentinel emissions). Forensics on `~/.claude/methodology-hook.log` for that session showed Stop fired exactly once for a 110-minute agent loop while six PostToolUse fires reported OVERDUE. Root cause: Claude Code fires Stop on agent-loop yield, not after every assistant turn; intermediate-turn heartbeat sentinels were therefore invisible to LAST_HEARTBEAT until loop-end.

What changed:

- **`hooks/post-tool-use.py` grew transcript-grep responsibility.** It now reads the transcript and walks back over the current agent loop's assistant text (skipping `tool_result` user lines) before evaluating cadence. If a heartbeat sentinel newer than the current `LAST_HEARTBEAT` is present, the anchor is advanced to the PostToolUse fire-time before the OVERDUE check runs. Sentinel freshness is gated on the parsed minute exceeding the current `LAST_HEARTBEAT_MIN`, so stale loop-history sentinels cannot suppress a legitimate cadence miss. New `extract_assistant_text`, `agent_loop_assistant_text`, and `max_sentinel_minute` helpers mirror stop.py's existing walk shape.
- **`hooks/stop.py` and `hooks/user-prompt-submit.py` now preserve `LAST_PTU_TAG_SEC`** across LAST_HEARTBEAT advances. Prior versions silently dropped that field, which let an immediately-following PostToolUse re-emit before the 60-second rate-limit was due.
- **`ROUTINE_VERSION` bumped to `1.1.1`** across all four hooks; `.claude-plugin/plugin.json` `version` field bumped to match.

What this does NOT fix: long conversational segments inside a single agent loop with zero tool calls (the assistant is in pure-text mode for >14 min wall-clock). Neither PostToolUse nor Stop fire in that window — there is no Claude Code hook event to bridge it. The OVERDUE-2X auto-advance fail-safe at the next UserPromptSubmit or PostToolUse continues to handle it cleanly without operator intervention; the heartbeat block discipline at the start of the recovery reply remains the assistant's responsibility per D-MET-41.

Plugin-snapshot reminder: `claude plugin update vc-roe@vibe-coding-rules` writes the new files but the running Claude Code process keeps invoking the v1.1.0 hooks from its in-memory snapshot. Close every Claude Code window/process and reopen to pick up v1.1.1 cleanly.

## 1.1.0 — Initial public release

First release as an open-source Claude Code plugin under dual GPL-3.0 (code) + CC BY-SA 4.0 (content) licensing. The plugin began life as a private internal tool; the public version represents the abstracted methodology shape, with operator-specific worked examples and reference instances kept on the operator's local machine as a private add-on layer.

What ships:

- **Five methodology slices** in `methodology-content/T0..T4.md`. Cumulative (T4 inherits T0+T1+T2+T3+T4 content). Slice for the auto-detected tier is injected as `additionalContext` at every SessionStart.
- **SessionStart hook** (`hooks/session-start.py`). Walks up to the nearest `.git/` (capped at 6 levels), scores Scope (S0..S3) and Criticality (C0..C2) from file-presence + keyword-scan signals, looks up the (S, C) -> T matrix in `detection-rules.json`, applies override precedence, injects the matching slice. Pure stdlib, never throws, fail-safe on any error.
- **UserPromptSubmit hook** (`hooks/user-prompt-submit.py`). Emits the elapsed-time clock-tag for the session-health heartbeat (T2+ rule); auto-advances LAST_HEARTBEAT on OVERDUE-2X as a layered fail-safe per D-MET-41.
- **PostToolUse hook** (`hooks/post-tool-use.py`). Fires after every tool call to surface OVERDUE / OVERDUE-2X clock-tags during long autonomous-work windows where no UserPromptSubmit fires (closes the autonomous-work heartbeat-silence gap surfaced at [EXAMPLE-PROJ] session 33). Rate-limited to once per 60 seconds. Tier-aware (T2+ only).
- **Stop hook** (`hooks/stop.py`). Greps the assistant transcript for the heartbeat-fired sentinel and advances the LAST_HEARTBEAT anchor (layered fail-safe so silent skipped heartbeats are caught).
- **Slash commands** in `commands/`: `/vc-roe:tier`, `/vc-roe:raise-tier`, `/vc-roe:lower-tier`, `/vc-roe:audit-pass`. Tier display + override + audit-pass declaration.
- **Detection rules** in `detection-rules.json`: regulatory-keyword list, scope-signal file-presence list, scope size signals, criticality path signals, locked (S, C) -> T matrix.
- **Helper scripts** in `bin/`: `anchor-rewrite.sh` (heartbeat anchor manipulation), `sync-user-aliases.sh` (idempotent re-copy of slash-command files to `~/.claude/commands/` so unqualified `/tier` works alongside namespaced `/vc-roe:tier`), `publish-audit.sh` (pre-publication leak scan against operator-private patterns).
- **Validation runner** (`test-detection.py`). Spins up synthetic project fixtures under a temp dir and exercises the SessionStart hook against each, checking the inferred tier matches expectation. Fully reproducible across machines.
- **Install scripts** (`install.sh` for Linux/macOS, `install.ps1` for Windows). One-liner curl-bash + iwr-iex installers that add the marketplace and install the plugin.

Override precedence (closer-file-wins):

1. Live `/vc-roe:tier T<N>` slash command in current turn.
2. `tier: T<N>` line in any CLAUDE.md from cwd up to project_root.
3. `.claude/methodology.json` `{ "tier": "T<N>" }` at project_root.
4. `CLAUDE_TIER` environment variable.
5. Auto-detected (S, C) -> matrix lookup.
6. Default T0.

Auto-detection caps at T3. T4 is reached only via `tier: T4` sentinel in CLAUDE.md (persistent) or live `/vc-roe:tier T4` (one-session). Tier inflation alienates operators more than tier deflation misses regulatory evidence; the cap is deliberate.

For Claude Code's plugin-snapshot semantics: `claude plugin update vc-roe@vibe-coding-rules` writes the new version to disk but the running Claude Code process keeps invoking the old hook code from its in-memory snapshot until restart. Close every Claude Code window/process, then reopen, to pick up an upgrade cleanly.
