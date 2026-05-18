---
description: Demote the effective tier by one step (T4->T3, T3->T2, T2->T1, T1->T0). Default at v0.5.0 (v4.1) is session-scope only (preserves v0.4.0 default); use --project to also lower the project tier floor for all future sessions (HWM is elevation-only by definition; demotion-via-flag preserved). Asks for a one-line reason.
argument-hint: "[--project]"
---

User invoked `/lower-tier` with arguments: `$ARGUMENTS`

Action:

1. Identify the current effective tier from your context (SessionStart trace, or any prior `/tier` / `/raise-tier` / `/lower-tier` overrides during this session).
2. Compute the next-lower tier: `T4->T3`, `T3->T2`, `T2->T1`, `T1->T0`. If currently `T0`, refuse: `Already at T0; there is no lower tier.`
3. Determine scope from `$ARGUMENTS`. If the literal string `--project` appears in `$ARGUMENTS`, this is a project-floor demotion (writes the floor marker, sticky across all future sessions). Otherwise this is a session-scope-only demotion (rewrites the per-session anchor only; floor unchanged). The session-scope-only default originates in v0.4.0 OBS-MET-AG closure (a sibling session running `/lower-tier` no longer silently lowers the project floor for all future sessions; previous behaviour caused observed cross-session floor-drift at sessions 15→16 and 18→19) and is preserved at v0.5.0 (v4.1) — HWM auto-elevation is elevation-only by definition, so demotion-via-flag is the only path to demote the floor.
4. Ask the operator: `What is the one-line reason for demoting to T<N-1>?` Pause for the answer.
5. Once the operator answers, log the demotion in your context: `Tier demoted from T<N> to T<N-1>. Reason: <reason>.` From this point on, apply the methodology rules for the new tier for the rest of the session.
6. **Rewrite the anchor TIER via helper script (always; this is the session-scope effect, applies for both default and `--project` invocations).** Use the Bash tool to run the block below, substituting the computed new tier (e.g. `T1` if demoting from T2) for the literal `<NEW_TIER>` token. The helper script `bin/anchor-rewrite.sh` carries the v0.3.0 ups-marker Layer 1 + v0.2.0 transcript-derived Layer 2 + size tie-break Layer 3:
   ```bash
   PLUGIN_BIN="$HOME/.claude/plugins/marketplaces/vibe-coding-rules/bin/anchor-rewrite.sh"
   [ -x "$PLUGIN_BIN" ] || PLUGIN_BIN=$(find "$HOME/.claude/plugins" -name anchor-rewrite.sh -path '*vc-roe*' -type f 2>/dev/null | sort -V | tail -1)
   [ -x "$PLUGIN_BIN" ] && bash "$PLUGIN_BIN" "<NEW_TIER>" || echo "anchor-rewrite.sh not resolvable in user-scope plugin install; manual heartbeat discipline applies."
   ```
   Without this rewrite, the UserPromptSubmit hook keeps reading the original anchor TIER and may continue emitting clock tags for a tier the operator just demoted out of.
7. **Set the project tier floor — ONLY IF `$ARGUMENTS` contains `--project`** (v0.4.0 OBS-MET-AG closure; default is session-scope only). If `--project` is present, run the Bash tool block below substituting the new tier for `<NEW_TIER>`:
   ```bash
   PROJ_DIRNAME=$(python -c 'import os, re; print(re.sub(r"[^A-Za-z0-9-]", "-", os.path.realpath(os.getcwd())))')  # OBS-MET-AJ
   PROJ_DIR="$HOME/.claude/projects/$PROJ_DIRNAME"
   mkdir -p "$PROJ_DIR" 2>/dev/null
   echo "<NEW_TIER>" > "$PROJ_DIR/methodology-tier-floor"
   echo "Project tier floor lowered to <NEW_TIER> at $PROJ_DIR/methodology-tier-floor (--project flag provided; sticky across all future sessions of this project)."
   ```
   If `--project` is NOT present, SKIP this block. Do not write the floor marker. The demotion takes effect for THIS session only via the anchor rewrite in step 6.
8. Disclose to the operator:
   - Without `--project`: `Tier demoted to T<N-1> for THIS SESSION ONLY (v0.4.0 default). Project tier floor unchanged. Use /lower-tier --project if you want the demotion to stick across all future sessions of this project. Re-raise via /tier or /raise-tier if the session-scope demotion was an accident.`
   - With `--project`: `Tier demoted to T<N-1> for this session AND project tier floor lowered to T<N-1> for all future sessions of this project (--project flag provided). Re-raise via /tier or /raise-tier --project if the demotion was temporary.`
