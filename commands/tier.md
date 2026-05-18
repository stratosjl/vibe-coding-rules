---
description: Display the current tier (no args) or override the tier for this session (with T0..T4 arg). At v0.5.0 (v4.1), asks at runtime whether ELEVATION applies to THIS chat only OR to the whole project; demotion preserves D-MET-62 both-scope semantics.
argument-hint: "[T0|T1|T2|T3|T4]"
---

User invoked `/tier` with arguments: `$ARGUMENTS`

If `$ARGUMENTS` is empty:

- Recall the tier you saw in the SessionStart `additionalContext` block at the start of this chat. Print the current display line in the canonical format: `Current tier: T<N> (S<x>/C<y>), <label>. Source: <auto|claude.md|claude-config|env|slash>.`
- If the SessionStart trace is not in your context (the hook may not have fired), say so explicitly: `No methodology context loaded; the SessionStart hook may not have fired this session. Check ~/.claude/methodology-hook.log for diagnosis.`

If `$ARGUMENTS` matches one of `T0`, `T1`, `T2`, `T3`, `T4`:

1. Identify the current effective tier from your context (SessionStart trace, or any prior `/tier` / `/raise-tier` / `/lower-tier` overrides during this session).
2. Compare `$ARGUMENTS` (target) to the current effective tier and compute direction:
   - **Elevation** if target > current (e.g., current T2, `$ARGUMENTS` T3 or T4).
   - **Demotion or no-change** if target ≤ current.
3. **Elevation case ONLY** — ask the operator the elevation-scope question (v0.5.0 OBS-MET-AH closure; replaces v0.4.0 implicit project-floor write for elevations). Emit verbatim and pause for the answer:
   ```
   Apply elevation to $ARGUMENTS:
     (S) THIS session only — anchor rewrite; project floor unchanged.
     (P) Whole project from now on — anchor rewrite + floor write; sticky across all future sessions of this project.
   Reply S or P.
   ```
   For **demotion or no-change**, skip the prompt — D-MET-62 both-scope semantics apply (explicit setting writes both anchor and floor; no ASK).
4. Acknowledge the override in your context: `Tier set to $ARGUMENTS for this session. Direction: <elevation|demotion|no-change>. Scope: <session|project>.` From this point on, follow the methodology rules for the requested tier.
5. **Rewrite the anchor TIER via helper script (always; all directions apply this).** The v0.1.7 UserPromptSubmit hook reads `TIER=` from `/tmp/claude-methodology-anchor-<session_id>` and short-circuits to no-op at T0/T1; without rewriting that line, an override from auto-T0 to T2+ leaves heartbeat enforcement silent. The helper carries v0.3.0 ups-marker Layer 1 + v0.2.0 transcript-derived Layer 2 + size tie-break Layer 3:
   ```bash
   PLUGIN_BIN="$HOME/.claude/plugins/marketplaces/vibe-coding-rules/bin/anchor-rewrite.sh"
   [ -x "$PLUGIN_BIN" ] || PLUGIN_BIN=$(find "$HOME/.claude/plugins" -name anchor-rewrite.sh -path '*vc-roe*' -type f 2>/dev/null | sort -V | tail -1)
   [ -x "$PLUGIN_BIN" ] && bash "$PLUGIN_BIN" "$ARGUMENTS" || echo "anchor-rewrite.sh not resolvable in user-scope plugin install; manual heartbeat discipline applies."
   ```
6. **Set the project tier floor**:
   - **Elevation + operator answered `S`**: SKIP floor write. Floor unchanged.
   - **Elevation + operator answered `P`**: write floor.
   - **Demotion or no-change**: write floor (D-MET-62 both-scope semantics for explicit setting; preserves v4.0 / v0.4.0 behaviour).
   When floor write applies, run:
   ```bash
   PROJ_DIRNAME=$(python -c 'import os, re; print(re.sub(r"[^A-Za-z0-9-]", "-", os.path.realpath(os.getcwd())))')  # OBS-MET-AJ
   PROJ_DIR="$HOME/.claude/projects/$PROJ_DIRNAME"
   mkdir -p "$PROJ_DIR" 2>/dev/null
   echo "$ARGUMENTS" > "$PROJ_DIR/methodology-tier-floor"
   echo "Project tier floor set to $ARGUMENTS at $PROJ_DIR/methodology-tier-floor (sticky across all future sessions of this project)."
   ```
7. Disclose:
   - **Elevation + S**: `Tier set to $ARGUMENTS for THIS SESSION ONLY. Project tier floor unchanged. Re-run /tier $ARGUMENTS and answer P if you want sticky behaviour, or rely on HWM auto-elevation when next-session auto-detection picks up the higher tier.`
   - **Elevation + P**: `Tier set to $ARGUMENTS for this session AND project tier floor raised to $ARGUMENTS for all future sessions of this project. CLAUDE.md tier: sentinel remains the absolute override if present.`
   - **Demotion or no-change**: `Tier set to $ARGUMENTS for this session AND project tier floor written (D-MET-62 explicit-setting semantic). CLAUDE.md tier: sentinel remains the absolute override if present.`

If `$ARGUMENTS` is anything else:

- Print: `Invalid tier: "$ARGUMENTS". Expected one of T0, T1, T2, T3, T4.`
