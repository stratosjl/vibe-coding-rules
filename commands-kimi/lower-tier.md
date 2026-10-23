---
description: Demote the effective tier by one step (T4->T3, T3->T2, T2->T1, T1->T0). Default is session-scope only (preserves v0.4.0 default); use --project to also lower the project tier floor AND `tier: T<N>` sentinel in CLAUDE.md for all future sessions and across machines (v1.13.0 symmetry with `/tier` and `/raise-tier`). Asks for a one-line reason.
argument-hint: "[--project]"
---

User invoked `/lower-tier` with arguments: `$ARGUMENTS`

(Registered as `/vc-roe:lower-tier`: the CLI namespaces plugin commands with the plugin id; the bare `/lower-tier` alias resolves to this command.)

Action:

1. Identify the current effective tier from your context (SessionStart trace, or any prior `/tier` / `/raise-tier` / `/lower-tier` overrides during this session).
2. Compute the next-lower tier: `T4->T3`, `T3->T2`, `T2->T1`, `T1->T0`. If currently `T0`, refuse: `Already at T0; there is no lower tier.`
3. Determine scope from `$ARGUMENTS`. If the literal string `--project` appears in `$ARGUMENTS`, this is a project-floor demotion (writes the floor marker, sticky across all future sessions). Otherwise this is a session-scope-only demotion (rewrites the per-session anchor only; floor unchanged). The session-scope-only default originates in v0.4.0 OBS-MET-AG closure (a sibling session running `/lower-tier` no longer silently lowers the project floor for all future sessions; previous behaviour caused observed cross-session floor-drift at sessions 15→16 and 18→19) and is preserved at v0.5.0 (v4.1) — HWM auto-elevation is elevation-only by definition, so demotion-via-flag is the only path to demote the floor.
4. Ask the operator: `What is the one-line reason for demoting to T<N-1>?` Pause for the answer.
5. Once the operator answers, log the demotion in your context: `Tier demoted from T<N> to T<N-1>. Reason: <reason>.` From this point on, apply the methodology rules for the new tier for the rest of the session.
6. **Load the methodology slice for the new tier into the active context (closes #1, v1.11.0).** The anchor-rewrite call in step 7 only updates what the heartbeat hook reads; the assistant's methodology slice was rendered at SessionStart and does not auto-refresh on a `/lower-tier` invocation. Without this step the assistant keeps applying the prior tier's ceremony (e.g. T3 four-pass close after a T3->T2 demotion), which is no longer required. Substitute the computed new tier for `<NEW_TIER>` in the block below; resolve the slice path via Bash; then invoke the `Read` tool on the resolved path so the slice content becomes a `tool_result` in the active context:
   ```bash
   VC_ROE_SEARCH="$HOME/.kimi-code/plugins"
   VC_ROE_KNOWN="$HOME/.kimi-code/plugins/managed/vc-roe"
   # --- vc-roe resolver v1.27.0 BEGIN (byte-identical in every command file;
   # --- test-exit-contract.py ARM 5 fails the build if a copy drifts). Resolves the
   # --- root THIS SESSION loaded, not the highest version present on disk.
   VC_ROE_ROOT=""; VC_ROE_VIA=""; VC_ROE_ANCHOR=""
   if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
     for d in /tmp "${TMPDIR:-}" "${TEMP:-}" "${TMP:-}"; do
       [ -n "$d" ] || continue
       a="$d/claude-methodology-anchor-$CLAUDE_CODE_SESSION_ID"
       [ -f "$a" ] || continue
       VC_ROE_ROOT=$(sed -n 's/^PLUGIN_ROOT=//p' "$a" | head -1)
       if [ -n "$VC_ROE_ROOT" ]; then VC_ROE_ANCHOR="$a"; VC_ROE_VIA="this session own anchor"; break; fi
     done
   fi
   if [ -z "$VC_ROE_ROOT" ] || [ ! -d "$VC_ROE_ROOT/methodology-content" ]; then
     if [ -n "$VC_ROE_KNOWN" ] && [ -d "$VC_ROE_KNOWN/methodology-content" ]; then
       VC_ROE_ROOT="$VC_ROE_KNOWN"; VC_ROE_VIA="known managed install path"
     fi
   fi
   if [ -z "$VC_ROE_ROOT" ] || [ ! -d "$VC_ROE_ROOT/methodology-content" ]; then
     b=$(find "$VC_ROE_SEARCH" -name anchor-rewrite.sh -path '*vc-roe*' -type f 2>/dev/null | sort -V | tail -1)
     if [ -n "$b" ]; then
       VC_ROE_ROOT=$(dirname "$(dirname "$b")")
       VC_ROE_VIA="FALLBACK highest version on disk, which may NOT be the one this session loaded"
     fi
   fi
   if [ -z "$VC_ROE_ROOT" ] || [ ! -d "$VC_ROE_ROOT/methodology-content" ]; then
     echo "NOT-RUNNABLE: the vc-roe plugin root did not resolve."
     echo "NOT-RUNNABLE: no PLUGIN_ROOT in this session anchor, and no install found under $VC_ROE_SEARCH."
     echo "NOT-RUNNABLE: nothing was loaded and nothing was written."
     exit 3
   fi
   echo "vc-roe root: $VC_ROE_ROOT"
   echo "resolved via: $VC_ROE_VIA"
   grep -o '"version"[^,]*' "$VC_ROE_ROOT/.claude-plugin/plugin.json" 2>/dev/null | head -1
   # --- vc-roe resolver v1.27.0 END
   SLICE="$VC_ROE_ROOT/methodology-content/<NEW_TIER>.md"
   if [ ! -r "$SLICE" ]; then
     echo "NOT-RUNNABLE: the <NEW_TIER> methodology slice is missing at $SLICE."
     echo "NOT-RUNNABLE: NO rules were loaded. Do not continue as though <NEW_TIER> were in force."
     exit 3
   fi
   echo "Methodology slice for <NEW_TIER> resolved at: $SLICE"
   echo ""
   echo "--- methodology slice <NEW_TIER> begin ---"
   cat "$SLICE"
   echo "--- methodology slice <NEW_TIER> end ---"
   ```
   Then use the `Read` tool on the `$SLICE` path printed by the Bash block. The Bash block also emits the slice content inline between `--- methodology slice <NEW_TIER> begin ---` and `--- methodology slice <NEW_TIER> end ---` markers (v1.12.0 belt-and-braces; closes Issue #1 point 3) so the operator sees the slice rendered in the chat output without expanding the Read tool result. From this point on, the loaded slice is the authoritative rule set for the remainder of the session. **If the Bash block exits 3 with a NOT-RUNNABLE banner, STOP.** Nothing was loaded, so the demotion is not in force however the acknowledgement reads. Report the banner verbatim and do not proceed on general familiarity with the tier.
7. **Rewrite the anchor TIER via helper script (always; this is the session-scope effect, applies for both default and `--project` invocations).** Use the Bash tool to run the block below, substituting the computed new tier (e.g. `T1` if demoting from T2) for the literal `<NEW_TIER>` token. The helper script `bin/anchor-rewrite.sh` carries the v0.3.0 ups-marker Layer 1 + v0.2.0 transcript-derived Layer 2 + size tie-break Layer 3:
   ```bash
   VC_ROE_SEARCH="$HOME/.kimi-code/plugins"
   VC_ROE_KNOWN="$HOME/.kimi-code/plugins/managed/vc-roe"
   # --- vc-roe resolver v1.27.0 BEGIN (byte-identical in every command file;
   # --- test-exit-contract.py ARM 5 fails the build if a copy drifts). Resolves the
   # --- root THIS SESSION loaded, not the highest version present on disk.
   VC_ROE_ROOT=""; VC_ROE_VIA=""; VC_ROE_ANCHOR=""
   if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
     for d in /tmp "${TMPDIR:-}" "${TEMP:-}" "${TMP:-}"; do
       [ -n "$d" ] || continue
       a="$d/claude-methodology-anchor-$CLAUDE_CODE_SESSION_ID"
       [ -f "$a" ] || continue
       VC_ROE_ROOT=$(sed -n 's/^PLUGIN_ROOT=//p' "$a" | head -1)
       if [ -n "$VC_ROE_ROOT" ]; then VC_ROE_ANCHOR="$a"; VC_ROE_VIA="this session own anchor"; break; fi
     done
   fi
   if [ -z "$VC_ROE_ROOT" ] || [ ! -d "$VC_ROE_ROOT/methodology-content" ]; then
     if [ -n "$VC_ROE_KNOWN" ] && [ -d "$VC_ROE_KNOWN/methodology-content" ]; then
       VC_ROE_ROOT="$VC_ROE_KNOWN"; VC_ROE_VIA="known managed install path"
     fi
   fi
   if [ -z "$VC_ROE_ROOT" ] || [ ! -d "$VC_ROE_ROOT/methodology-content" ]; then
     b=$(find "$VC_ROE_SEARCH" -name anchor-rewrite.sh -path '*vc-roe*' -type f 2>/dev/null | sort -V | tail -1)
     if [ -n "$b" ]; then
       VC_ROE_ROOT=$(dirname "$(dirname "$b")")
       VC_ROE_VIA="FALLBACK highest version on disk, which may NOT be the one this session loaded"
     fi
   fi
   if [ -z "$VC_ROE_ROOT" ] || [ ! -d "$VC_ROE_ROOT/methodology-content" ]; then
     echo "NOT-RUNNABLE: the vc-roe plugin root did not resolve."
     echo "NOT-RUNNABLE: no PLUGIN_ROOT in this session anchor, and no install found under $VC_ROE_SEARCH."
     echo "NOT-RUNNABLE: nothing was loaded and nothing was written."
     exit 3
   fi
   echo "vc-roe root: $VC_ROE_ROOT"
   echo "resolved via: $VC_ROE_VIA"
   grep -o '"version"[^,]*' "$VC_ROE_ROOT/.claude-plugin/plugin.json" 2>/dev/null | head -1
   # --- vc-roe resolver v1.27.0 END
   bash "$VC_ROE_ROOT/bin/anchor-rewrite.sh" "<NEW_TIER>"; rc=$?
   case "$rc" in
     0) echo "anchor: TIER set to <NEW_TIER>." ;;
     3) echo "NOT-RUNNABLE: no session anchor resolved. Heartbeat enforcement is NOT in force at any tier. Report this to the operator." ;;
     2) echo "USAGE ERROR: anchor-rewrite.sh rejected the argument. That is a bug in this command, not in the environment." ;;
     *) echo "anchor-rewrite.sh exited $rc (unexpected)." ;;
   esac
   exit "$rc"
   ```
   Without this rewrite, the UserPromptSubmit hook keeps reading the original anchor TIER and may continue emitting clock tags for a tier the operator just demoted out of.
8. **Set the project tier floor ONLY IF `$ARGUMENTS` contains `--project`** (v0.4.0 OBS-MET-AG closure; default is session-scope only). If `--project` is present, run the Bash tool block below substituting the new tier for `<NEW_TIER>`:
   ```bash
   PROJ_DIRNAME=$(python -c 'import os, re; print(re.sub(r"[^A-Za-z0-9-]", "-", os.path.realpath(os.getcwd())))')  # OBS-MET-AJ
   PROJ_DIR="$HOME/.claude/projects/$PROJ_DIRNAME"
   mkdir -p "$PROJ_DIR" 2>/dev/null
   echo "<NEW_TIER>" > "$PROJ_DIR/methodology-tier-floor"
   echo "Project tier floor lowered to <NEW_TIER> at $PROJ_DIR/methodology-tier-floor (--project flag provided; sticky across all future sessions of this project)."
   ```
   If `--project` is NOT present, SKIP this block. Do not write the floor marker. The demotion takes effect for THIS session only via the anchor rewrite in step 6.
9. **Write the `tier: T<N>` sentinel to project-root CLAUDE.md or AGENTS.md ONLY IF `$ARGUMENTS` contains `--project`** (v1.13.0 symmetry with `/tier` and `/raise-tier`; closes 1b for the demotion side). If `--project` is NOT present, SKIP this block; the session-only demotion does not touch the sentinel and next session will resume at the higher tier per the sentinel/floor. If `--project` is present, the demotion must also lower the CLAUDE.md sentinel; otherwise the sentinel (which has absolute priority over floor and auto-detect per `find_tier_in_claude_md`) would re-elevate the tier at next SessionStart, silently negating the operator-requested demotion. Substitute the new tier for `<NEW_TIER>`:
   ```bash
   VC_ROE_SEARCH="$HOME/.kimi-code/plugins"
   VC_ROE_KNOWN="$HOME/.kimi-code/plugins/managed/vc-roe"
   # --- vc-roe resolver v1.27.0 BEGIN (byte-identical in every command file;
   # --- test-exit-contract.py ARM 5 fails the build if a copy drifts). Resolves the
   # --- root THIS SESSION loaded, not the highest version present on disk.
   VC_ROE_ROOT=""; VC_ROE_VIA=""; VC_ROE_ANCHOR=""
   if [ -n "${CLAUDE_CODE_SESSION_ID:-}" ]; then
     for d in /tmp "${TMPDIR:-}" "${TEMP:-}" "${TMP:-}"; do
       [ -n "$d" ] || continue
       a="$d/claude-methodology-anchor-$CLAUDE_CODE_SESSION_ID"
       [ -f "$a" ] || continue
       VC_ROE_ROOT=$(sed -n 's/^PLUGIN_ROOT=//p' "$a" | head -1)
       if [ -n "$VC_ROE_ROOT" ]; then VC_ROE_ANCHOR="$a"; VC_ROE_VIA="this session own anchor"; break; fi
     done
   fi
   if [ -z "$VC_ROE_ROOT" ] || [ ! -d "$VC_ROE_ROOT/methodology-content" ]; then
     if [ -n "$VC_ROE_KNOWN" ] && [ -d "$VC_ROE_KNOWN/methodology-content" ]; then
       VC_ROE_ROOT="$VC_ROE_KNOWN"; VC_ROE_VIA="known managed install path"
     fi
   fi
   if [ -z "$VC_ROE_ROOT" ] || [ ! -d "$VC_ROE_ROOT/methodology-content" ]; then
     b=$(find "$VC_ROE_SEARCH" -name anchor-rewrite.sh -path '*vc-roe*' -type f 2>/dev/null | sort -V | tail -1)
     if [ -n "$b" ]; then
       VC_ROE_ROOT=$(dirname "$(dirname "$b")")
       VC_ROE_VIA="FALLBACK highest version on disk, which may NOT be the one this session loaded"
     fi
   fi
   if [ -z "$VC_ROE_ROOT" ] || [ ! -d "$VC_ROE_ROOT/methodology-content" ]; then
     echo "NOT-RUNNABLE: the vc-roe plugin root did not resolve."
     echo "NOT-RUNNABLE: no PLUGIN_ROOT in this session anchor, and no install found under $VC_ROE_SEARCH."
     echo "NOT-RUNNABLE: nothing was loaded and nothing was written."
     exit 3
   fi
   echo "vc-roe root: $VC_ROE_ROOT"
   echo "resolved via: $VC_ROE_VIA"
   grep -o '"version"[^,]*' "$VC_ROE_ROOT/.claude-plugin/plugin.json" 2>/dev/null | head -1
   # --- vc-roe resolver v1.27.0 END
   H="$VC_ROE_ROOT/bin/claude-md-sentinel.py"
   if [ ! -r "$H" ]; then
     echo "NOT-RUNNABLE: claude-md-sentinel.py is missing at $H."
     echo "NOT-RUNNABLE: the CLAUDE.md sentinel was NOT written, so the tier is not portable to other machines."
     exit 3
   fi
   python "$H" "<NEW_TIER>" --target auto; rc=$?
   [ "$rc" = 0 ] || echo "claude-md-sentinel.py exited $rc; the sentinel was NOT written."
   exit "$rc"
   ```
10. Also instruct the assistant: **From your VERY NEXT reply onward, prepend the first-line rule from the loaded slice (e.g., `Detected tier: <NEW_TIER> (S<x>/C<y>), <label>. Override with /vc-roe:tier <T0..T4> if wrong.`) to every assistant reply for the remainder of the session** (v1.13.0 closes 2; first-line MUST rule applies on mid-session slice load too, not only at SessionStart).
11. Disclose to the operator:
    - Without `--project`: `Tier demoted to T<N-1> for THIS SESSION ONLY (v0.4.0 default). Project tier floor unchanged; CLAUDE.md sentinel unchanged. Use /lower-tier --project if you want the demotion to stick across all future sessions and across machines. Re-raise via /tier or /raise-tier if the session-scope demotion was an accident.`
    - With `--project`: `Tier demoted to T<N-1> for this session, project tier floor lowered to T<N-1>, AND tier: T<N-1> sentinel updated in project-root CLAUDE.md or AGENTS.md (--project flag provided; sticky across all future sessions of this project AND portable across all machines the repo syncs to). Commit the CLAUDE.md change to git to propagate the demotion to other machines. Re-raise via /tier or /raise-tier if the demotion was temporary.`
