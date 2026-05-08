---
description: Promote the effective tier by one step (T0->T1, T1->T2, T2->T3, T3->T4). At v0.5.0 (v4.1), asks at runtime whether elevation applies to THIS chat only OR to the whole project from now on (HWM-writes the floor). Asks for a one-line reason.
argument-hint: ""
---

User invoked `/raise-tier`.

Action:

1. Identify the current effective tier from your context (SessionStart trace, or any prior `/tier` / `/raise-tier` / `/lower-tier` overrides during this session).
2. Compute the next-higher tier: `T0->T1`, `T1->T2`, `T2->T3`, `T3->T4`. If currently `T4`, refuse: `Already at T4; there is no T5. If the project has grown beyond T4, split it into multiple T4 projects with higher-level governance.`
3. Ask the operator: `What is the one-line reason for promoting to T<N+1>?` Pause for the answer.
4. **Ask the operator the elevation-scope question** (v0.5.0 OBS-MET-AH closure; replaces v0.4.0 `--project` flag UX). Emit the following prompt verbatim and pause for the answer:
   ```
   Apply elevation to T<N+1>:
     (S) THIS session only — anchor rewrite; project floor unchanged.
     (P) Whole project from now on — anchor rewrite + floor write; sticky across all future sessions of this project.
   Reply S or P.
   ```
5. Once the operator answers (`S` or `P`), log the promotion in your context: `Tier promoted from T<N> to T<N+1>. Reason: <reason>. Scope: <session|project>.` From this point on, apply the methodology rules for the new tier for the rest of the session.
6. **Rewrite the anchor TIER via helper script (always; both scopes apply this).** Use the Bash tool to run the block below, substituting the computed new tier (e.g. `T2` if promoting from T1) for the literal `<NEW_TIER>` token. The helper script `bin/anchor-rewrite.sh` carries the v0.3.0 ups-marker Layer 1 + v0.2.0 transcript-derived Layer 2 + size tie-break Layer 3:
   ```bash
   PLUGIN_BIN="$HOME/.claude/plugins/marketplaces/vibe-coding-rules/vc-roe/bin/anchor-rewrite.sh"
   [ -x "$PLUGIN_BIN" ] || PLUGIN_BIN=$(find "$HOME/.claude/plugins/cache" -name anchor-rewrite.sh -path '*vc-roe*' -o -path '*vc-roe*' 2>/dev/null | sort -V | tail -1)
   [ -x "$PLUGIN_BIN" ] && bash "$PLUGIN_BIN" "<NEW_TIER>" || echo "anchor-rewrite.sh not resolvable in user-scope plugin install; manual heartbeat discipline applies."
   ```
   Without this rewrite, the UserPromptSubmit hook keeps reading the original anchor TIER and short-circuits at T0/T1 even after the promotion.
7. **Set the project tier floor — ONLY IF operator answered `P`** (v0.5.0 OBS-MET-AH closure; HWM-style sticky across future sessions). If the operator answered `S`, SKIP this block. If `P`, run:
   ```bash
   PROJ_DIRNAME=$(python -c 'import os, re; print(re.sub(r"[^A-Za-z0-9-]", "-", os.path.realpath(os.getcwd())))')  # OBS-MET-AJ
   PROJ_DIR="$HOME/.claude/projects/$PROJ_DIRNAME"
   mkdir -p "$PROJ_DIR" 2>/dev/null
   echo "<NEW_TIER>" > "$PROJ_DIR/methodology-tier-floor"
   echo "Project tier floor set to <NEW_TIER> at $PROJ_DIR/methodology-tier-floor (sticky across all future sessions of this project; project-scope answer)."
   ```
8. Disclose:
   - If `S`: `Tier promoted to T<N+1> for THIS SESSION ONLY. Project tier floor unchanged. Re-run /raise-tier and answer P next time if you want sticky behaviour, or rely on HWM auto-elevation when next-session auto-detection picks up the higher tier.`
   - If `P`: `Tier promoted to T<N+1> for this session AND project tier floor raised to T<N+1> for all future sessions of this project. CLAUDE.md tier: sentinel remains the absolute override if present.`
