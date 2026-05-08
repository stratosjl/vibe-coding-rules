# Changelog

All notable changes to vc-roe (vibe-coding-rules-of-engagement).

The plugin follows semantic versioning. Version is single-source-of-truth in `.claude-plugin/plugin.json` and mirrored to `detection-rules.json` `version` and the `ROUTINE_VERSION` constant in `hooks/session-start.py`.

## 0.3.0 — Initial public release

First release as an open-source Claude Code plugin under dual GPL-3.0 (code) + CC BY-SA 4.0 (content) licensing. The plugin began life as a private internal tool; the public version represents the abstracted methodology shape, with operator-specific worked examples and reference instances kept on the operator's local machine as a private add-on layer.

What ships:

- **Five methodology slices** in `methodology-content/T0..T4.md`. Cumulative (T4 inherits T0+T1+T2+T3+T4 content). Slice for the auto-detected tier is injected as `additionalContext` at every SessionStart.
- **SessionStart hook** (`hooks/session-start.py`). Walks up to the nearest `.git/` (capped at 6 levels), scores Scope (S0..S3) and Criticality (C0..C2) from file-presence + keyword-scan signals, looks up the (S, C) -> T matrix in `detection-rules.json`, applies override precedence, injects the matching slice. Pure stdlib, never throws, fail-safe on any error.
- **UserPromptSubmit hook** (`hooks/user-prompt-submit.py`). Emits the elapsed-time clock-tag for the session-health heartbeat (T2+ rule).
- **Stop hook** (`hooks/stop.py`). Greps the assistant transcript for the heartbeat-fired sentinel and advances the LAST_HEARTBEAT anchor (layered fail-safe so silent skipped heartbeats are caught).
- **Slash commands** in `commands/`: `/vc-roe:tier`, `/vc-roe:raise-tier`, `/vc-roe:lower-tier`, `/vc-roe:audit-pass`. Tier display + override + audit-pass declaration.
- **Detection rules** in `detection-rules.json`: regulatory-keyword list, scope-signal file-presence list, scope size signals, criticality path signals, locked (S, C) -> T matrix.
- **Helper scripts** in `bin/`: `anchor-rewrite.sh` (heartbeat anchor manipulation), `sync-user-aliases.sh` (idempotent re-copy of slash-command files to `~/.claude/commands/` so unqualified `/tier` works alongside namespaced `/vc-roe:tier`).
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
