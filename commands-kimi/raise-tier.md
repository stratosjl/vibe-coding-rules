---
description: Promote the effective tier by one step (T0->T1, T1->T2, T2->T3, T3->T4). At v1.13.0, promotion is always sticky; floor file AND `tier: T<N>` sentinel in project-root CLAUDE.md are both written (closes 1a + 1b). Asks for a one-line reason.
argument-hint: ""
---

User invoked `/raise-tier`.

Action:

1. Identify the current effective tier from your context (SessionStart trace, or any prior `/tier` / `/raise-tier` / `/lower-tier` overrides during this session).
2. Compute the next-higher tier: `T0->T1`, `T1->T2`, `T2->T3`, `T3->T4`. If currently `T4`, refuse: `Already at T4; there is no T5. If the project has grown beyond T4, split it into multiple T4 projects with higher-level governance.`
3. Ask the operator: `What is the one-line reason for promoting to T<N+1>?` Pause for the answer.
4. **v1.13.0: no elevation-scope question.** Promotion via explicit operator action is always project-sticky: both the project tier floor AND the `tier: T<N>` sentinel in project-root CLAUDE.md or AGENTS.md are written (closes 1a + 1b). The prior v0.5.0 S/P question is removed because the S option (session-only) is semantically incoherent with `/raise-tier`; if the operator wanted a session-only change they would use a different mechanism. For a temporary downshift after this promotion, use `/lower-tier` (without `--project`).
5. Once the operator has provided the reason, log the promotion in your context: `Tier promoted from T<N> to T<N+1>. Reason: <reason>. Scope: project (sticky via floor + CLAUDE.md sentinel).` From this point on, apply the methodology rules for the new tier for the rest of the session. **From your VERY NEXT reply onward, prepend the first-line rule from the loaded slice (e.g., `Detected tier: T<N+1> (S<x>/C<y>), <label>. Override with /vc-roe:tier <T0..T4> if wrong.`) to every assistant reply for the remainder of the session** (v1.13.0 closes 2; the first-line MUST rule was originally authored for SessionStart-time loading and did not previously apply on mid-session slice loads).
6. **Load the methodology slice for the new tier into the active context (closes #1, v1.11.0).** The anchor-rewrite call in step 7 only updates what the heartbeat hook reads; the assistant's methodology slice was rendered at SessionStart and does not auto-refresh on a `/raise-tier` invocation. Without this step the assistant keeps operating against the SessionStart-time slice (silent T-N gap, operator-invisible). Substitute the computed new tier for `<NEW_TIER>` in the block below; resolve the slice path via Bash; then invoke the `Read` tool on the resolved path so the slice content becomes a `tool_result` in the active context:
   ```bash
   PLUGIN_BIN="$HOME/.kimi-code/plugins/managed/vc-roe/bin/anchor-rewrite.sh"
   [ -x "$PLUGIN_BIN" ] || PLUGIN_BIN=$(find "$HOME/.kimi-code/plugins" -name anchor-rewrite.sh -path '*vc-roe*' -type f 2>/dev/null | sort -V | tail -1)
   if [ -x "$PLUGIN_BIN" ]; then
     PLUGIN_ROOT=$(dirname "$(dirname "$PLUGIN_BIN")")
     SLICE="$PLUGIN_ROOT/methodology-content/<NEW_TIER>.md"
     if [ -r "$SLICE" ]; then
       echo "Methodology slice for <NEW_TIER> resolved at: $SLICE"
       echo ""
       echo "--- methodology slice <NEW_TIER> begin ---"
       cat "$SLICE"
       echo "--- methodology slice <NEW_TIER> end ---"
     else
       echo "Methodology slice file for <NEW_TIER> not found at $PLUGIN_ROOT/methodology-content/; falling back to general familiarity with <NEW_TIER> prescriptions."
     fi
   else
     echo "Plugin root not resolvable; falling back to general familiarity with <NEW_TIER> prescriptions."
   fi
   ```
   Then use the `Read` tool on the `$SLICE` path printed by the Bash block. The Bash block also emits the slice content inline between `--- methodology slice <NEW_TIER> begin ---` and `--- methodology slice <NEW_TIER> end ---` markers (v1.12.0 belt-and-braces; closes Issue #1 point 3) so the operator sees the slice rendered in the chat output without expanding the Read tool result. From this point on, the loaded slice is the authoritative rule set for the remainder of the session. If the Bash block printed a "fallback" message instead of the slice content, surface it to the operator inline (the promotion acknowledgement remains valid; only the slice-text load failed).
7. **Rewrite the anchor TIER via helper script (always; v1.13.0 always-sticky path).** Use the Bash tool to run the block below, substituting the computed new tier (e.g. `T2` if promoting from T1) for the literal `<NEW_TIER>` token. The helper script `bin/anchor-rewrite.sh` carries the v0.3.0 ups-marker Layer 1 + v0.2.0 transcript-derived Layer 2 + size tie-break Layer 3:
   ```bash
   PLUGIN_BIN="$HOME/.kimi-code/plugins/managed/vc-roe/bin/anchor-rewrite.sh"
   [ -x "$PLUGIN_BIN" ] || PLUGIN_BIN=$(find "$HOME/.kimi-code/plugins" -name anchor-rewrite.sh -path '*vc-roe*' -type f 2>/dev/null | sort -V | tail -1)
   [ -x "$PLUGIN_BIN" ] && bash "$PLUGIN_BIN" "<NEW_TIER>" || echo "anchor-rewrite.sh not resolvable in user-scope plugin install; manual heartbeat discipline applies."
   ```
   Without this rewrite, the UserPromptSubmit hook keeps reading the original anchor TIER and short-circuits at T0/T1 even after the promotion.
8. **Set the project tier floor (always; v1.13.0 sticky-on-explicit).** Substituting the new tier for `<NEW_TIER>`:
   ```bash
   PROJ_DIRNAME=$(python -c 'import os, re; print(re.sub(r"[^A-Za-z0-9-]", "-", os.path.realpath(os.getcwd())))')  # OBS-MET-AJ
   PROJ_DIR="$HOME/.claude/projects/$PROJ_DIRNAME"
   mkdir -p "$PROJ_DIR" 2>/dev/null
   echo "<NEW_TIER>" > "$PROJ_DIR/methodology-tier-floor"
   echo "Project tier floor set to <NEW_TIER> at $PROJ_DIR/methodology-tier-floor (sticky across all future sessions of this project)."
   ```
9. **Write the `tier: T<N>` sentinel to project-root CLAUDE.md or AGENTS.md (always; v1.13.0 cross-machine portability).** Floor file is `~/.claude/projects/`-local cache; CLAUDE.md is git-committed, making the override portable across all machines the project syncs to (closes 1b). Substitute the new tier for `<NEW_TIER>`:
   ```bash
   PLUGIN_BIN="$HOME/.kimi-code/plugins/managed/vc-roe/bin/anchor-rewrite.sh"
   [ -x "$PLUGIN_BIN" ] || PLUGIN_BIN=$(find "$HOME/.kimi-code/plugins" -name anchor-rewrite.sh -path '*vc-roe*' -type f 2>/dev/null | sort -V | tail -1)
   if [ -x "$PLUGIN_BIN" ]; then
     PLUGIN_ROOT=$(dirname "$(dirname "$PLUGIN_BIN")")
     SENTINEL_HELPER="$PLUGIN_ROOT/bin/claude-md-sentinel.py"
     if [ -r "$SENTINEL_HELPER" ]; then
       python "$SENTINEL_HELPER" "<NEW_TIER>" --target auto
     else
       echo "claude-md-sentinel.py not found at $SENTINEL_HELPER; CLAUDE.md sentinel NOT updated. Floor file written; in-session tier set; cross-machine portability not guaranteed."
     fi
   else
     echo "Plugin root not resolvable; CLAUDE.md sentinel NOT updated. Floor file written; in-session tier set; cross-machine portability not guaranteed."
   fi
   ```
10. Disclose: `Tier promoted to T<N+1> for this session, project tier floor set to T<N+1>, AND tier: T<N+1> sentinel written to project-root CLAUDE.md/AGENTS.md (sticky across all future sessions of this project AND portable across all machines the repo syncs to). Commit the CLAUDE.md change to git to propagate the promotion to other machines.`
