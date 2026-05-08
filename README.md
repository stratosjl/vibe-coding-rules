# vc-roe — vibe-coding-rules-of-engagement

A Claude Code plugin that auto-detects project complexity and injects the matching methodology slice as `additionalContext` at every SessionStart.

## What it does

Claude Code's default behavior is the same regardless of whether you're hacking on a throwaway script, a small personal project, or a regulated production system. That mismatch costs time on small projects (over-procedure) and risks shipping in big ones (under-procedure).

vc-roe scores every project on two dimensions:

- **Scope (S0..S3)** — ad-hoc / lightweight task / small project / serious project. From file-presence signals: `decisions.md`, `handovers/`, `BUILD_LOG.md`, `openapi.yaml`, etc.
- **Criticality (C0..C2)** — personal / professional / regulatory. From regulatory-keyword scan in CLAUDE.md (DORA, MiFID, AIFMD, GDPR, AML, etc.) and path signals (`compliance/`, `regulatory/`, `dpia/`).

The (S, C) → T mapping yields one of five tiers. The matching slice from `methodology-content/T<N>.md` is injected as `additionalContext`, so Claude knows exactly what discipline level applies before the first user message.

Auto-detection caps at T3. T4 is reached only by explicit operator decision (`tier: T4` in CLAUDE.md, or `/vc-roe:tier T4`). Tier inflation alienates operators more than tier deflation misses regulatory evidence; the cap is deliberate.

Display line printed by Claude on its first reply each session:

```
Detected tier: T2 (S2/C1), small project, professional. Override with /vc-roe:tier <T0..T4> if wrong.
```

## Per-tier controls

Each tier is **cumulative**: T1 inherits T0, T2 inherits T0+T1, etc. The table below lists what each tier ADDS on top of the previous one. The whole point of the plugin is making this explicit at session start so Claude doesn't drift between disciplines.

| Tier | Auto-detected when | Adds (cumulative) |
|---|---|---|
| **T0** ad-hoc | empty dir, no signals | humanized-text always-on |
| **T1** lightweight task | one or more git-tracked files; no `docs/` structure | plan-first; verify-with-evidence (every "shipped" / "works" claim has accompanying evidence); confirmation-gate for irreversible actions; capture-lessons inline; negative-scope at end |
| **T2** small project | `docs/decisions.md` or `docs/handovers/` present (persistent state) | `decisions.md` (`D-N` IDs, never renumbered, SUPERSEDED rows preserve history); `open-issues.md` (`I-N` IDs); per-session handover + opening-prompt pair; pre-next-chat verification block; five-step session close (handover → opening prompt → decisions update → open-issues update → lessons capture); session-health heartbeat every 15 min wall-clock (hook-enforced via SessionStart anchor + UserPromptSubmit clock-tag + Stop sentinel-grep + PostToolUse fallback); anomaly-callout inline at turn level; TaskCreate for plans ≥3 steps; subagent strategy for parallelism + context hygiene; tier-persistence in handover + opening prompt + project CLAUDE.md |
| **T3** serious project | `BUILD_LOG.md` or `openapi.yaml` at root; OR ≥1 regulatory keyword in CLAUDE.md (DORA, MiFID, GDPR, AIFMD, AML, etc.); OR multi-machine / multi-month scope; OR irreversible production actions in scope | audit-pass policy (`Audit-pass owed for THIS session: Yes / No` footer on every handover); lessons-promotion path (3+ recurrences of a pattern → standing rule); explicit irreversible-action enumeration in CLAUDE.md (the list the confirmation-gate matches against); four-pass closing matrix (positive-scope edits + forward obligations + scope discipline + end-of-session sweep); CA3 hybrid handover split (summary 1-3 KB + audit-trail 5-15 KB); anomaly-first reflex with formal `OBS-N-NN` row capture (pause + root-cause + capture before continuing; never paper over to ship); regulator-presentable exactness (canonicalise compliance counts on enumeration; propagate to all dependent artefacts); best-practices-first across four axes (performance, project management, security, scheduling); long-process status checks every 10 min; capture-every-idea inline (not at session end); three-same-day-override pattern (4th touch on a coupled architectural surface MUST be a comprehensive batch closing all known follow-on issues, not another isolated patch) |
| **T4** mission-critical regulated | operator-set only (`tier: T4` in CLAUDE.md or `/vc-roe:tier T4`); auto-detection caps at T3 | **eleven-element structural close**: (1) regression floor verified + (2) change log entry + (3) runbook update + (4) compliance trace + (5) public-contract verification (lint clean) + (6) architecture decisions captured + (7) architecture-section row updated + (8) next-handover skeleton + (9) user-facing docs reviewed + (10) final smoke check + (11) change log written last; mid-flight pause sub-procedure (significant scope shift = stop + document + re-confirm with operator); multi-module review cadence for the project's own methodology document; pre-drafted handover skeletons for next N sessions if on a known phase trajectory; audit-trail target widens to 10-30 KB with mandatory one-paragraph TL;DR at top |

The cumulative property means a T3 project always inherits T2's heartbeat cadence + decisions ledger; a T4 project always inherits T3's anomaly-first reflex + four-pass closing matrix. Lower-tier rules don't get dropped at higher tiers; they stack.

## Why each tier needs what it adds

Quick justification for the additions, in case you're choosing whether to commit to a tier escalation:

- **T1** adds *plan-first* and *confirmation-gate* because once a project is tracked in git, the cost of a wrong destructive action (force-push, schema migration, deploy) is no longer trivial.
- **T2** adds the *decisions ledger* and *heartbeat cadence* because multi-session work needs a record of choices that survives chat-context resets, and long sessions drift without an external clock.
- **T3** adds the *audit-pass policy* and *four-pass closing matrix* because a project at this size has visible-to-stakeholders surfaces — public deploys, regulator-adjacent compliance traces, multi-author coordination — and silent regressions are no longer recoverable next session. The *three-same-day-override pattern* specifically catches the "we'll just hot-fix once more" trap that produces death-by-a-thousand-patches when an under-batched ship needed comprehensive bundling instead.
- **T4** adds the *eleven-element structural close* because regulator-compellable artefacts demand provability after the fact: every claim made at close needs evidence on disk, not just chat history. *Mid-flight pause* exists because at T4, an unannounced scope shift mid-session can leave a partial regulator-reportable state that's worse than no work at all.

A higher-tier project running with lower-tier ceremonies is a leak: the controls that would have caught a problem aren't running. A lower-tier project running with higher-tier ceremonies is friction: ceremony cost without proportional risk.

## Install

### Linux/macOS

```bash
curl -fsSL https://raw.githubusercontent.com/stratosjl/vibe-coding-rules/main/install.sh | bash
```

### Windows (PowerShell)

```powershell
iwr -useb https://raw.githubusercontent.com/stratosjl/vibe-coding-rules/main/install.ps1 | iex
```

### Manual install via Claude Code marketplace

```bash
claude code marketplace add https://github.com/stratosjl/vibe-coding-rules.git
claude code plugin install vc-roe@vibe-coding-rules --scope user
```

The install scripts are equivalent to the manual marketplace flow; they exist so first-time users have a single command.

## Slash commands

- `/vc-roe:tier` — display the current tier and (S, C) trace
- `/vc-roe:tier T<N>` — override the tier for the rest of the session
- `/vc-roe:raise-tier` — promote one step with a one-line reason
- `/vc-roe:lower-tier` — demote one step with a one-line reason
- `/vc-roe:audit-pass` — declare an audit-pass owed for the previous session

## Override precedence (closer-file-wins)

1. Live `/vc-roe:tier T<N>` slash command in current turn.
2. `tier: T<N>` line in any CLAUDE.md from cwd up to project_root (closer wins).
3. `.claude/methodology.json` `{ "tier": "T<n>" }` at project_root.
4. `CLAUDE_TIER` environment variable.
5. Auto-detected (S, C) → matrix lookup.
6. Default T0 if no signals at all.

## How the heartbeat works

T2+ tiers run a session-health heartbeat every ~15 min wall-clock. The hook layer makes this self-enforcing:

- `SessionStart` writes a LAST_HEARTBEAT anchor.
- `UserPromptSubmit` fires on every user turn and tags the elapsed minutes.
- `Stop` greps the assistant transcript for a `[heartbeat-fired:T+<n>m]` literal sentinel; if it sees one, it advances the anchor.

If the assistant fails to emit a heartbeat at the cadence point, the next user turn surfaces an `OVERDUE` flag in the elapsed-time tag, prompting recovery without the assistant having to remember.

## Updating

Claude Code holds an in-memory snapshot of plugin hooks across each Claude Code process. `claude plugin update` writes the new version to disk but the running process keeps invoking the old hook code from its snapshot until restart. `/clear` does NOT refresh this snapshot.

To pick up a new version cleanly:

1. `claude plugin update vc-roe@vibe-coding-rules`
2. Close every Claude Code window/process (CLI, desktop app, IDE extension)
3. Reopen Claude Code. The next session start will read the new hooks, slash-command markdown, and detection rules from disk.

## Logs

Every hook run logs to `~/.claude/methodology-hook.log` (one JSON line per run). Useful for diagnosing missing methodology context.

Sample line:

```json
{"ts":1777644562.123,"duration_ms":1,"session_id":"...","source_event":"startup","cwd":"/path/to/project","tier":"T2","scope":"S2","criticality":"C1","tier_source":"auto","label":"small project, professional","signals":["S2:decisions.md","S2:handovers","C:GDPR"],"project_root":"/path/to/project","git_root_found":true,"routine_version":"0.3.0"}
```

Failure entries use `{"ts":..., "phase":"load_rules|detect|stdin_parse|stdout_write", "error":"<msg>"}`. Hook always exits 0; SessionStart in Claude Code is never blocked by hook failure.

## Detection rule editing

Keyword sets, file-presence signals, and the (S, C) → T matrix live in `detection-rules.json`. Editable without re-shipping plugin code; bump `plugin.json` `version` on push.

## Adding new hook lifecycles

Three steps to add another Claude Code hook event (PreToolUse, PostToolUse, SubagentStop, etc.):

1. **Write the hook script** at `hooks/<event-name>.py`. Mirror the existing contract: pure stdlib; never throw; read stdin JSON; write JSON to stdout (or empty for no-op); always exit 0; append one-line JSON log entry to `~/.claude/methodology-hook.log` with `ts`, `hook`, `session_id`, `routine_version` fields.
2. **Register in `hooks/hooks.json`** under the matching event name, with the same `${CLAUDE_PLUGIN_ROOT}/hooks/<event-name>.py` invocation pattern and a tight timeout (SessionStart 10s, UserPromptSubmit 5s, Stop 5s; pick proportional to expected work).
3. **Bump `ROUTINE_VERSION`** in the new file in lockstep with `plugin.json`, `detection-rules.json`, and the existing hook files. Single-source-of-truth lives in `plugin.json`.

The plugin's `hooks/` directory is the canonical home for all hook scripts. Do not introduce `settings.json` hook entries on the user side; they bypass the plugin's lockstep discipline. Any new hook should preserve the fail-soft contract: a hook crash never blocks Claude Code itself.

## Local development

For testing changes before pushing a new version: clone the repo and symlink `~/.claude/plugins/vc-roe/` to your local clone. The hook scripts self-locate via `__file__.resolve()` so they read `detection-rules.json` and `methodology-content/` from the symlink target.

```bash
git clone https://github.com/stratosjl/vibe-coding-rules.git
ln -sf "$(pwd)/vibe-coding-rules" ~/.claude/plugins/vc-roe
python3 vibe-coding-rules/test-detection.py
```

The validation runner spins up synthetic project fixtures under a temp dir; clean across all five expected tiers means the detection logic is intact.

## Licensing

Dual-licensed:

- **Code** (Python hooks, bash scripts, JSON config, install scripts): GPL-3.0-or-later. See `LICENSE-CODE`.
- **Methodology content** (`methodology-content/T*.md`, README prose, CHANGELOG): CC BY-SA 4.0. See `LICENSE-CONTENT`.

See `LICENSING.md` for the file-by-file boundary mapping. The dual-license mirrors the Wikipedia split (MediaWiki software is GPL; Wikipedia content is CC BY-SA), and Creative Commons themselves recommend GPL over CC for software.

Anyone modifying or distributing the plugin must publish their changes back under the same licenses (share-alike). For the spirit of why: tier-based methodology is the kind of thing that benefits from public iteration, and copyleft keeps that iteration accessible to the next operator.

## Contributing

PRs welcome. Three things that make a PR easier to merge:

- **Run `python3 test-detection.py` cleanly** — the synthetic fixture suite is what gates regressions on the detection logic.
- **Follow the lockstep version-bump rule** when changing hook code, detection rules, or methodology slices.
- **Don't paste regulator-presentable claims into the public methodology slices** — the operator-private addon layer is where firm-specific worked examples live.

## Origin

The plugin started as a private internal tool for managing scale-of-rigour mismatch across mixed personal + professional + regulated work. The public version is the abstracted methodology shape; operator-specific worked examples and reference instances live as a private add-on layer that joins with the public plugin at runtime.
