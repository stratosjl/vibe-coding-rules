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

### Operator-local post-* hook fire-through (since v1.9.1)

`bin/install-hooks.sh` also activates three thin forwarder dispatchers under `.githooks/`: `post-commit`, `post-merge`, `post-checkout`. Each dispatcher resolves the repo root via `git rev-parse --show-toplevel` and `exec`s the operator-local hook at `.git/hooks/post-<event>` when that file exists and is executable; otherwise it silent-no-ops. Positional arguments (`post-checkout`'s `prev_head new_head branch_flag` triple, `post-merge`'s `is_squash_merge` flag) are passed through to the operator-local hook untouched; exit code propagates natively.

This dispatcher chain is the standard pattern for keeping the version-controlled pre-push gate at `.githooks/pre-push` while preserving any operator-local post-event automation (sync hooks, notification scripts, custom logging) that the contributor installs into `.git/hooks/post-*` per clone. There is no need to re-install operator-local post-* hooks after running `bin/install-hooks.sh`; the dispatchers forward to whatever already exists there.

If you do not have operator-local post-* hooks, the dispatchers are inert and require no additional configuration.

## Bump ONE version, and let the guard prove it

Whenever you change anything in:

- `hooks/*.py`
- `detection-rules.json`
- `methodology-content/*.md`
- `commands/*.md` (anything affecting slash-command behaviour)

bump **`.claude-plugin/plugin.json` → `version`**. That is the whole ritual for
seven of the nine carriers, which now derive their value from that manifest at
load rather than mirroring it as a literal:

| carrier | how it gets its version |
|---|---|
| `.claude-plugin/plugin.json` → `version` | **source of truth — bump this** |
| `hooks/session-start.py` → `ROUTINE_VERSION` | derived at load |
| `hooks/user-prompt-submit.py` → `ROUTINE_VERSION` | derived at load |
| `hooks/post-tool-use.py` → `ROUTINE_VERSION` | derived at load |
| `hooks/stop.py` → `ROUTINE_VERSION` | derived at load |
| `hooks/session-end.py` → `ROUTINE_VERSION` | derived at load |
| `hooks/kimi/_adapter.py` → `KIMI_ADAPTER_VERSION` | derived at load |
| `bin/publish-audit-combined.sh` → `HARNESS_VERSION` | derived at start |
| `kimi.plugin.json` → `version` | **hand-maintained — bump this too** |

`kimi.plugin.json` is a second static manifest consumed by Kimi Code, so it
cannot derive from the first. It is the only carrier that can still drift, and
it is exactly what the guard catches.

**Never reintroduce a hand-maintained version literal.** `test-version-lockstep.py`
fails on one even when its value is currently correct, because a correct literal
is simply a drift that has not happened yet.

**The guard, and why it exists.** `test-version-lockstep.py` runs as tool 7 of
`bin/publish-audit-combined.sh`, so a drifted release cannot be pushed through
the `pre-push` hook. It checks three things: no hand-maintained literal survives
(ARM 1); every hook, executed for real, resolves the manifest version and not
the `unknown` fail-soft sentinel (ARM 2); and every static manifest mirror
agrees (ARM 3). `--self-test` injects one defect per arm into throwaway copies
of the tree and asserts each arm reports it — the harness always passes that
flag, so the negative proof runs on every push rather than only when someone
remembers.

This replaces the "9-constant lockstep" that stood from v1.1.1 to v1.21.0. It
was re-broken by every content-only release, most recently and most visibly at
v1.21.0: the manifest read `1.21.0` while all eight mirrors still read `1.20.2`,
in the working tree AND in the installed plugin cache, so every hook log entry
stamped the wrong version and no test noticed. Recorded as I-22.

After tagging a release, also sync any managed install copies of the plugin on the release machine (the Kimi Code managed plugins directory, the Claude Code plugin cache). They are plain mirrors of the tree, and a stale copy silently runs the previous release's hooks. Re-running the installer or an `rsync -a --delete --exclude .git` from the repo root both work.

(`detection-rules.json` carries its own independent `version` field tracking the detection-logic schema only; it does not move in lockstep with plugin releases.)

## Optional machine-local SessionStart addon (extension point)

Since v1.15.0 the SessionStart hook exposes a fail-soft extension point so an operator can contribute machine-local session context (infrastructure state, a private resumption audit, environment banners) **without** patching the public hook or shipping anything into this repo.

The contract is intentionally tiny. At SessionStart, if the directory `~/.claude/vc-roe-addons` exists, the hook puts it on `sys.path` and calls:

```python
# ~/.claude/vc-roe-addons/vc_roe_local_addons.py
def session_start_block(detection: dict, tier: str) -> tuple[str, str]:
    """Return (block, state).

    block: a Markdown string spliced into additionalContext immediately
           before the "## Tier detection trace" section. Return "" to add
           nothing. End multi-line blocks with a trailing blank line so the
           following section stays separated.
    state: a one-word trace token rendered on the `resumption_audit:` line
           of the tier-detection trace (e.g. "clean", "drift", "stale").
    """
    return ("", "none")
```

- `detection` is the resolved tier-detection dict (keys include `tier`, `scope`, `crit`, `source`, `signals`, `project_root`); `tier` is the effective tier string.
- The seam is **opt-in and machine-local**. This repo ships **no** `vc_roe_local_addons` module, and a plain install has no `~/.claude/vc-roe-addons` directory, so the seam is skipped entirely and `resumption_audit:` reads `none`.
- It is **fully fail-soft**: any import or runtime error degrades to an empty block and `resumption_audit: error`, and never affects session start. This is covered by `test-session-start-addon.py`.

This is the supported way to layer private behaviour onto SessionStart. The "Changes that depend on a private add-on layer being present at runtime" exclusion below still holds: the *hook* must work identically with the addon absent, which the fail-soft contract guarantees.

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
