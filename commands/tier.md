---
description: Display the current tier (no args) or override the tier for this session (with T0..T4 arg). At v1.13.0, elevation is always sticky; both project tier floor AND `tier: T<N>` sentinel in project CLAUDE.md are written so the override survives across sessions and across machines (closes 1a + 1b). Demotion preserves D-MET-62 both-scope semantics.
argument-hint: "[T0|T1|T2|T3|T4]"
---

User invoked `/tier` with arguments: `$ARGUMENTS`

(Registered as `/vc-roe:tier`: the CLI namespaces plugin commands with the plugin id; the bare `/tier` alias resolves to this command.)

If `$ARGUMENTS` is empty:

- Recall the tier you saw in the SessionStart `additionalContext` block at the start of this chat. Print the current display line in the canonical format: `Current tier: T<N> (S<x>/C<y>), <label>. Source: <auto|claude.md|claude-config|env|slash>.`
- If the SessionStart trace is not in your context (the hook may not have fired), say so explicitly: `No methodology context loaded; the SessionStart hook may not have fired this session. Check ~/.claude/methodology-hook.log for diagnosis.`

If `$ARGUMENTS` matches one of `T0`, `T1`, `T2`, `T3`, `T4` (**case-insensitively**: `t3` and `T3` are the same request):

**Canonical token.** Every artefact this command writes (anchor `TIER=`, the tier-floor file, the CLAUDE.md sentinel) and every line it prints uses the UPPERCASE `T<N>` form, whatever case the operator typed. The shell blocks below derive `$TIER` for this; use that value, not the raw argument, in your prose too.

1. Identify the current effective tier from your context (SessionStart trace, or any prior `/tier` / `/raise-tier` / `/lower-tier` overrides during this session).
2. Compare `$ARGUMENTS` (target) to the current effective tier and compute direction:
   - **Elevation** if target > current (e.g., current T2, `$ARGUMENTS` T3 or T4).
   - **Demotion or no-change** if target ≤ current.
3. **v1.13.0: no elevation-scope question.** Elevation via explicit operator action is always project-sticky: both the project tier floor AND the `tier: T<N>` sentinel in project-root CLAUDE.md are written. This closes OBS-vcroe-elevation-not-sticky-01 (1a) and the multi-machine portability gap OBS-vcroe-floor-machine-local-01 (1b). If the operator wants a one-session-only tier change, use `/lower-tier` (without `--project`) AFTER the elevation, or simply do not invoke `/tier` for non-sticky decisions. Demotion or no-change paths via `/tier` continue to apply D-MET-62 both-scope semantics (explicit setting writes both anchor and floor; sentinel is also updated for consistency).
4. Acknowledge the override in your context: `Tier set to $ARGUMENTS for this session AND for the project (sticky via floor + CLAUDE.md sentinel). Direction: <elevation|demotion|no-change>.` From this point on, follow the methodology rules for the requested tier. **From your VERY NEXT reply onward, prepend the first-line rule from the loaded slice (e.g., `Detected tier: $ARGUMENTS (S<x>/C<y>), <label>. Override with /vc-roe:tier <T0..T4> if wrong.`) to every assistant reply for the remainder of the session** (v1.13.0 closes 2; the first-line MUST rule was originally authored for SessionStart-time loading and did not previously apply on mid-session slice loads).
5. **Load the methodology slice for the new tier into the active context (closes #1, v1.11.0).** The anchor-rewrite call in step 6 only updates what the heartbeat hook reads; the assistant's methodology slice was rendered at SessionStart and does not auto-refresh on a `/tier` override. Without this step the assistant keeps operating against the SessionStart-time slice (silent T-N gap, operator-invisible). Resolve the slice path via Bash (reuse the same plugin discovery pattern as step 6), then invoke the `Read` tool on the resolved path so the slice content becomes a `tool_result` in the active context:
   ```bash
   TIER=$(printf '%s' "$ARGUMENTS" | tr '[:lower:]' '[:upper:]')  # canonical token: /tier t3 and /tier T3 are one request
   VC_ROE_SEARCH="$HOME/.claude/plugins"
   VC_ROE_KNOWN=""
   # --- vc-roe resolver v1.24.0 BEGIN (byte-identical in every command file;
   # --- test-command-lockstep.py fails the build if a copy drifts). Resolves the
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
   # --- vc-roe resolver v1.24.0 END
   SLICE="$VC_ROE_ROOT/methodology-content/$TIER.md"
   if [ ! -r "$SLICE" ]; then
     echo "NOT-RUNNABLE: the $TIER methodology slice is missing at $SLICE."
     echo "NOT-RUNNABLE: NO rules were loaded. Do not continue as though $TIER were in force."
     exit 3
   fi
   echo "Methodology slice for $TIER resolved at: $SLICE"
   echo ""
   echo "--- methodology slice $TIER begin ---"
   cat "$SLICE"
   echo "--- methodology slice $TIER end ---"
   ```
   Then use the `Read` tool on the `$SLICE` path printed by the Bash block. The Bash block also emits the slice content inline between `--- methodology slice $ARGUMENTS begin ---` and `--- methodology slice $ARGUMENTS end ---` markers (v1.12.0 belt-and-braces; closes Issue #1 point 3) so the operator sees the slice rendered in the chat output without expanding the Read tool result. From this point on, the loaded slice is the authoritative rule set for the remainder of the session. **If the Bash block exits 3 with a NOT-RUNNABLE banner, STOP.** Nothing was loaded, so the tier change is not in force however the acknowledgement reads. Report the banner to the operator verbatim and do not proceed on general familiarity with the tier: a half-remembered slice is indistinguishable, in the transcript, from one that loaded.
6. **Rewrite the anchor TIER via helper script (always; all directions apply this).** The v0.1.7 UserPromptSubmit hook reads `TIER=` from `/tmp/claude-methodology-anchor-<session_id>` and short-circuits to no-op at T0/T1; without rewriting that line, an override from auto-T0 to T2+ leaves heartbeat enforcement silent. The helper carries v0.3.0 ups-marker Layer 1 + v0.2.0 transcript-derived Layer 2 + size tie-break Layer 3:
   ```bash
   TIER=$(printf '%s' "$ARGUMENTS" | tr '[:lower:]' '[:upper:]')  # canonical token: /tier t3 and /tier T3 are one request
   VC_ROE_SEARCH="$HOME/.claude/plugins"
   VC_ROE_KNOWN=""
   # --- vc-roe resolver v1.24.0 BEGIN (byte-identical in every command file;
   # --- test-command-lockstep.py fails the build if a copy drifts). Resolves the
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
   # --- vc-roe resolver v1.24.0 END
   bash "$VC_ROE_ROOT/bin/anchor-rewrite.sh" "$TIER"; rc=$?
   case "$rc" in
     0) echo "anchor: TIER set to $TIER." ;;
     3) echo "NOT-RUNNABLE: no session anchor resolved. Heartbeat enforcement is NOT in force at any tier. Report this to the operator." ;;
     2) echo "anchor-rewrite.sh REFUSED tier '$TIER' (rc=2, usage error). That is a REFUSAL, not a resolution failure: the script was found and ran, and the heartbeat TIER is UNCHANGED. Do not report this as a missing plugin." ;;
     *) echo "anchor-rewrite.sh exited $rc (unexpected)." ;;
   esac
   exit "$rc"
   ```
7. **Set the project tier floor (always; v1.13.0 sticky-on-explicit).** Any `/tier T<N>` invocation now writes the floor (closes 1a). Run:
   ```bash
   TIER=$(printf '%s' "$ARGUMENTS" | tr '[:lower:]' '[:upper:]')  # canonical token: /tier t3 and /tier T3 are one request
   PROJ_DIRNAME=$(python -c 'import os, re; print(re.sub(r"[^A-Za-z0-9-]", "-", os.path.realpath(os.getcwd())))')  # OBS-MET-AJ
   PROJ_DIR="$HOME/.claude/projects/$PROJ_DIRNAME"
   mkdir -p "$PROJ_DIR" 2>/dev/null
   echo "$TIER" > "$PROJ_DIR/methodology-tier-floor"  # MUST be uppercase: read_tier_floor() is case-sensitive
   echo "Project tier floor set to $TIER at $PROJ_DIR/methodology-tier-floor (sticky across all future sessions of this project)."
   ```
8. **Write the `tier: T<N>` sentinel to project-root CLAUDE.md (always; v1.13.0 cross-machine portability).** Floor file is `~/.claude/projects/`-local cache; it does not git-sync. CLAUDE.md is git-committed, so the sentinel makes the override portable across all machines the project syncs to (closes 1b). Cross-platform helper at `bin/claude-md-sentinel.py` handles all six edit cases (insert into existing frontmatter, prepend new frontmatter, replace bare legacy `tier:` line, idempotent no-op, etc.):
   ```bash
   TIER=$(printf '%s' "$ARGUMENTS" | tr '[:lower:]' '[:upper:]')  # canonical token: /tier t3 and /tier T3 are one request
   VC_ROE_SEARCH="$HOME/.claude/plugins"
   VC_ROE_KNOWN=""
   # --- vc-roe resolver v1.24.0 BEGIN (byte-identical in every command file;
   # --- test-command-lockstep.py fails the build if a copy drifts). Resolves the
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
   # --- vc-roe resolver v1.24.0 END
   H="$VC_ROE_ROOT/bin/claude-md-sentinel.py"
   if [ ! -r "$H" ]; then
     echo "NOT-RUNNABLE: claude-md-sentinel.py is missing at $H."
     echo "NOT-RUNNABLE: the CLAUDE.md sentinel was NOT written, so the tier is not portable to other machines."
     exit 3
   fi
   python "$H" "$TIER"; rc=$?
   [ "$rc" = 0 ] || echo "claude-md-sentinel.py exited $rc; the sentinel was NOT written."
   exit "$rc"
   ```
9. Disclose: `Tier set to $ARGUMENTS for this session, project tier floor set to $ARGUMENTS, AND tier: $ARGUMENTS sentinel written to project-root CLAUDE.md (sticky across all future sessions of this project AND portable across all machines the repo syncs to). Direction: <elevation|demotion|no-change>. Commit the CLAUDE.md change to git to propagate the override to other machines.`

If `$ARGUMENTS` is anything else:

- Print: `Invalid tier: "$ARGUMENTS". Expected one of T0, T1, T2, T3, T4.`
