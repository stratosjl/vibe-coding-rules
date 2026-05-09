# Changelog

All notable changes to vc-roe (vibe-coding-rules-of-engagement).

The plugin follows semantic versioning. Version is single-source-of-truth in `.claude-plugin/plugin.json` and mirrored to the `ROUTINE_VERSION` constant in every hook under `hooks/`.

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
