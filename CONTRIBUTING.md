# Contributing to vc-roe

Pull requests welcome. Before you open one, three things make merging easier.

## Run the validation suite cleanly

```bash
python3 test-detection.py
```

The runner spins up synthetic project fixtures under a temp dir and exercises the SessionStart hook against each. Any failure means the detection logic regressed; fix or document before opening the PR.

## Install the cross-machine pre-push hook (once per clone)

vc-roe ships a version-controlled pre-push hook at `.githooks/pre-push` that runs `bin/publish-audit-combined.sh` and blocks any push that leaks operator-private content or credential patterns. The hook is activated per-clone by a one-time bootstrap:

```bash
bash bin/install-hooks.sh
```

The bootstrap sets `git config --local core.hooksPath .githooks`. It is idempotent — safe to re-run. After install, every `git push` from this clone runs the combined-audit harness (six tools: `publish-audit.sh`, `publish-audit-state.sh`, `gitleaks` HEAD + history, `test-audit-patterns.py`, inline credential heuristics) and blocks on any FAIL. WARN-only results (e.g., `gitleaks` not installed) do not block.

Run the harness manually any time:

```bash
bash bin/publish-audit-combined.sh           # quiet, hook-mode
bash bin/publish-audit-combined.sh --verbose # full per-tool output
```

## Follow the lockstep version-bump rule

Whenever you change anything in:

- `hooks/*.py`
- `detection-rules.json`
- `methodology-content/*.md`
- `commands/*.md` (anything affecting slash-command behaviour)

bump these values in lockstep:

1. `.claude-plugin/plugin.json` → `version`
2. `hooks/session-start.py` → `ROUTINE_VERSION`
3. `hooks/user-prompt-submit.py` → `ROUTINE_VERSION`
4. `hooks/post-tool-use.py` → `ROUTINE_VERSION`
5. `hooks/stop.py` → `ROUTINE_VERSION`
6. `hooks/session-end.py` → `ROUTINE_VERSION`

The version field in `plugin.json` is single-source-of-truth; the five `ROUTINE_VERSION` constants are mirrors that travel with the hook code. Drift between them surfaces as confusing log entries and stale-cache symptoms downstream. If a PR touches one, it touches all six.

(`detection-rules.json` carries its own independent `version` field tracking the detection-logic schema only; it does not move in lockstep with plugin releases.)

## Don't paste regulator-presentable claims into the public methodology slices

The plugin's public slices (`methodology-content/T0.md` through `T4.md`) describe the SHAPE of methodology at each tier, not specific compliance regimes. If your contribution wants to add specific worked examples ("at firm X, the T4 close instantiates as Y"), keep that as your own private add-on layer, not a PR to this repo. The public version stays domain-agnostic so any operator at any firm can adopt it.

What IS welcome:

- Bug fixes in the detection logic.
- New detection signals (e.g., recognising a new file-presence indicator of project scope) — keep the patch general.
- Hook-contract improvements (tighter timeouts, better error handling, new hook lifecycles wired through `hooks/hooks.json`).
- Cross-platform parity fixes (Linux/macOS/Windows).
- Documentation clarifications, typo fixes, link updates.

What ISN'T welcome:

- Hard-coded firm-specific paths or examples.
- Personal email or other identifying data in commits.
- Changes that depend on a private add-on layer being present at runtime.

## Commit hygiene

Sign your commits if your `gitconfig` is set up for it:

```bash
git commit -S -m "..."
```

Concise commit message; lead with the *why* in the subject. Body for details.

## License

By contributing, you agree your changes are licensed under the same dual scheme:

- Code (Python, Bash, JSON config) → GPL-3.0-or-later (`LICENSE-CODE`).
- Content (Markdown methodology slices, README, CHANGELOG) → CC BY-SA 4.0 (`LICENSE-CONTENT`).

See `LICENSING.md` for the file-by-file boundary.
