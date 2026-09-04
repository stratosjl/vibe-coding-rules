#!/usr/bin/env bash
# publish-audit-combined.sh - v1.8.0 ship target.
#
# Combined-audit harness. Wraps the six pre-publication audit tools into
# a single invocation with per-tool PASS|FAIL|WARN reporting and an
# aggregated exit code. Designed for pre-push hook invocation
# (cross-machine via .githooks/pre-push + bin/install-hooks.sh) and for
# cron daily-run invocation (logs to operator's methodology-hook.log).
#
# Closes F-61-02 forward obligation (combined-audit harness) per the
# T3 item 11 deferral closure-date discipline rule (v1.7.0): closure
# trigger active until shipped before v1.6.0 soak completes or next
# push to origin/main, whichever earlier.
#
# Seven wrapped tools:
#   1. publish-audit.sh             operator-pattern, working-tree scope
#   2. publish-audit-state.sh       fresh-clone public-state, HEAD only
#                                   (--history skipped per v1.6.0
#                                   sanitisation-artefact note; gitleaks
#                                   history walk below provides the
#                                   history coverage)
#   3. gitleaks (HEAD, no-git)      credential-pattern, working-tree
#   4. gitleaks (full history)      credential-pattern, history walk
#   5. test-audit-patterns.py       audit-pattern self-audit regression
#   6. inline credential heuristics drafted at v1.8.0; complementary
#                                   to gitleaks for shapes gitleaks
#                                   regexes can miss (Anthropic, recent
#                                   GitHub PAT shapes, PEM markers, etc.)
#   7. test-version-lockstep.py     version-lockstep guard (I-22), run
#                                   WITH --self-test so its three negative
#                                   arms are proven to fire on every push
#                                   rather than only when someone remembers
#
# Result semantics:
#   PASS   - tool ran clean
#   FAIL   - tool RAN and reported a leak / regression; aggregate exits 1
#   NOTRUN - tool could NOT RUN at all (dependency absent, environment
#            wrong). Aggregate exits 3 and the push is blocked, because a
#            check that did not run is not a check that passed. This used to
#            be WARN, which incremented a counter the exit decision never
#            read, so a missing scanner rendered as "Safe to push".
#   WARN   - tool RAN and reported known-noise; aggregate still exits 0.
#            No tool currently emits WARN: the three sites that used to
#            (gitleaks absent, twice, and a missing runner file) are NOTRUN,
#            because they are tools that did not run rather than tools that
#            ran noisily. The status is kept for a genuine known-noise case.
#
# Exit codes, the contract in methodology-content/T2.md (added v1.24.0):
#   0 - every tool ran and none reported a defect; safe to push
#   1 - one or more tools reported a leak / regression; push blocked
#   2 - usage error: the CALLER is wrong (unknown argument)
#   3 - NOT-RUNNABLE: one or more tools could not run, so the audit is
#       incomplete and nothing here says the tree is clean; push blocked
#
# The line this table used to carry, "2 - harness-internal error (missing
# dependency file etc.)", described behaviour no code path produced: every
# exit in this file was 0, 1 or 2 and the only exit 2 was the
# unknown-argument branch, so a missing dependency could never reach it.
#
# Usage:
#   bash bin/publish-audit-combined.sh                 quiet, hook-mode
#   bash bin/publish-audit-combined.sh --verbose       full per-tool output
#   bash bin/publish-audit-combined.sh --version       resolved harness version
#   bash bin/publish-audit-combined.sh -h | --help

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

# I-22: HARNESS_VERSION derives from the single-source-of-truth manifest.
# A hand-maintained mirror here went stale on every content-only release
# (measured 2026-08-25: manifest 1.21.0, this constant still 1.20.2). Read with
# `sed` rather than jq or python so the banner needs no interpreter and this
# line cannot fail before the $PY probe below has run. The value is cosmetic,
# so an unreadable manifest degrades to a visible "unknown" rather than
# aborting the audit; test-version-lockstep.py ARM 2 asserts it resolves.
HARNESS_VERSION="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
  "$REPO_ROOT/.claude-plugin/plugin.json" 2>/dev/null | head -1)"
[ -n "$HARNESS_VERSION" ] || HARNESS_VERSION="unknown"
VERBOSE=0

# Resolve a Python interpreter that actually runs. On Windows, `python3`
# resolves via `command -v` to the Microsoft Store app-execution alias stub,
# which prints nothing and fails on exec, so a plain existence check is unsafe.
# Probe each candidate by running --version and requiring real "Python X" output.
# Linux/macOS keep `python3` (listed first); Windows falls through to `python`.
PY=""
for _cand in python3 python; do
  if command -v "$_cand" >/dev/null 2>&1 && "$_cand" --version 2>&1 | grep -qi '^python [0-9]'; then
    PY="$_cand"; break
  fi
done
[ -n "$PY" ] || PY=python3  # last resort; surfaces a clear error downstream
while [ "$#" -gt 0 ]; do
  case "$1" in
    -v|--verbose)
      VERBOSE=1
      shift
      ;;
    --version)
      # I-22: lets test-version-lockstep.py read back the resolved value
      # without running the six-tool audit.
      printf '%s\n' "$HARNESS_VERSION"
      exit 0
      ;;
    -h|--help)
      # Print the whole comment header, however long it grows. The former
      # hard-coded `sed -n '2,40p'` silently truncated the Usage block the
      # first time the header gained a line (I-22 tool-7 entry).
      awk 'NR>1 { if (/^#/) print; else exit }' "$0"
      exit 0
      ;;
    *)
      printf '[combined-audit] FATAL: unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

color() {
  if [ -t 1 ] && [ "${TERM:-}" != "dumb" ]; then printf '\033[%sm%s\033[0m' "$1" "$2"
  else printf '%s' "$2"; fi
}
hdr() { color '36' "[combined-audit] "; printf '%s\n' "$*"; }

declare -a RESULTS=()
passes=0
warns=0
notruns=0
fails=0

record() {
  local tool="$1" status="$2" detail="$3"
  RESULTS+=("$tool|$status|$detail")
  case "$status" in
    PASS) passes=$((passes + 1)) ;;
    WARN) warns=$((warns + 1)) ;;
    NOTRUN) notruns=$((notruns + 1)) ;;
    FAIL) fails=$((fails + 1)) ;;
  esac
}

record_missing() {
  # ONE policy for "this check's own runner file is absent at repo root".
  # Tool 5 used to record WARN for it (aggregate exit 0, push allowed) and
  # tool 7 FAIL (push blocked), sixty lines apart, for the identical
  # condition; the silently forgiven one was the audit-pattern self-audit,
  # the check that proves every DENY and WARN regex still matches its own
  # fixture. Neither verdict is right: an absent runner is a check that did
  # not run. Routing both through one helper is what stops them diverging
  # again.
  record "$1" NOTRUN "$2 not found at repo root; check did not run"
}

run_verbose_or_capture() {
  # Helper: run "$@" capturing combined stdout+stderr to var $out_var,
  # echo the captured output if VERBOSE, return the command's rc.
  local out_var="$1"; shift
  local _out _rc
  _out=$("$@" 2>&1)
  _rc=$?
  printf -v "$out_var" '%s' "$_out"
  if [ "$VERBOSE" -eq 1 ]; then
    printf '%s\n' "$_out"
  fi
  return "$_rc"
}

hdr "combined-audit harness v${HARNESS_VERSION} starting"

# ----- Tool 1: publish-audit.sh -------------------------------------------
if [ "$VERBOSE" -eq 1 ]; then hdr "tool 1/7: publish-audit.sh"; fi
out=""
run_verbose_or_capture out bash bin/publish-audit.sh
rc=$?
deny_n=$(printf '%s\n' "$out" | grep -E '^\[publish-audit\][[:space:]]+deny hits:' | head -1 | sed -E 's/.*deny hits:[[:space:]]+([0-9]+).*/\1/' || echo '?')
warn_n=$(printf '%s\n' "$out" | grep -E '^\[publish-audit\][[:space:]]+warn hits:' | head -1 | sed -E 's/.*warn hits:[[:space:]]+([0-9]+).*/\1/' || echo '?')
if [ "$rc" -eq 0 ]; then
  record "publish-audit" PASS "deny=${deny_n:-0} warn=${warn_n:-0}"
elif [ "$rc" -eq 3 ]; then
  # bin/publish-audit.sh gained the NOT-RUNNABLE exit 3 in v1.25.0 (not inside a
  # git work tree, or `git grep` itself could not run). Without this arm that
  # lands here as FAIL, which is the exact defect this release exists to close:
  # a scan that never happened rendered as a scan that found something. NOTRUN
  # still blocks the push, via the aggregate's exit 3, so nothing is loosened.
  summary=$(printf '%s\n' "$out" | tail -1)
  record "publish-audit" NOTRUN "rc=3; ${summary:-no output}"
else
  record "publish-audit" FAIL "deny=${deny_n:-?} warn=${warn_n:-?} rc=$rc"
fi

# ----- Tool 2: publish-audit-state.sh (HEAD only; no --history) -----------
if [ "$VERBOSE" -eq 1 ]; then hdr "tool 2/7: publish-audit-state.sh (HEAD only)"; fi
out=""
run_verbose_or_capture out bash bin/publish-audit-state.sh
rc=$?
if [ "$rc" -eq 0 ]; then
  record "publish-audit-state" PASS "HEAD clean (history skipped per v1.6.0 note)"
elif [ "$rc" -eq 3 ]; then
  # The harness half of a two-file change, and BOTH halves are now in. This
  # comment used to say bin/publish-audit-state.sh "does NOT emit 3 today",
  # which was true when the arm was written and false by the time the release
  # shipped: that script gained exit 3 for a failed `git clone` in v1.25.0.
  # Measured on a clone whose origin does not resolve: it exits 3 with
  # "NOT-RUNNABLE: git clone ... exited 128", and this arm renders it NOTRUN
  # rather than as a finding about HEAD.
  summary=$(printf '%s\n' "$out" | tail -1)
  record "publish-audit-state" NOTRUN "rc=3; ${summary:-no output}"
else
  # The detail used to assert "HEAD has DENY hits" for every non-zero rc, but
  # publish-audit-state.sh does not check its `git clone` (it runs under
  # `set -uo pipefail`, with no -e) and reports an unreachable remote through
  # the same exit 1 as a real leak. Measured 2026-09-04 on this repo, whose
  # origin does not resolve: rc=1, "cannot self-audit", rendered here as
  # "HEAD has DENY hits". Reporting that tool's own last line keeps the two
  # causes distinguishable. The row stays FAIL either way, so the gate still
  # fails closed when the published state could not be audited at all.
  summary=$(printf '%s\n' "$out" | tail -1)
  record "publish-audit-state" FAIL "rc=$rc; ${summary:-no output}"
fi

# ----- Tools 3 + 4: gitleaks (HEAD no-git, then full history) -------------
if command -v gitleaks >/dev/null 2>&1; then
  if [ "$VERBOSE" -eq 1 ]; then hdr "tool 3/7: gitleaks HEAD (no-git)"; fi
  out=""
  run_verbose_or_capture out gitleaks detect --source "$REPO_ROOT" --no-git -v
  rc=$?
  if [ "$rc" -eq 0 ]; then
    record "gitleaks-head" PASS "no leaks found"
  else
    record "gitleaks-head" FAIL "leaks reported; rc=$rc"
  fi

  if [ "$VERBOSE" -eq 1 ]; then hdr "tool 4/7: gitleaks full history walk"; fi
  out=""
  run_verbose_or_capture out gitleaks detect --source "$REPO_ROOT" -v
  rc=$?
  if [ "$rc" -eq 0 ]; then
    record "gitleaks-history" PASS "no leaks found"
  else
    record "gitleaks-history" FAIL "leaks reported; rc=$rc"
  fi
else
  if [ "$VERBOSE" -eq 1 ]; then hdr "tools 3/7 + 4/7: gitleaks SKIPPED (not installed)"; fi
  # NOTRUN, not WARN. These two are the only tools that walk git HISTORY, so
  # without them a credential committed and then removed from the working
  # tree in a later commit is invisible to all seven: tools 1 and 6 are
  # git grep scans of tracked content with no rev, tool 2 audits the PUBLIC
  # remote HEAD rather than the local commits being pushed, and tools 5 and 7
  # scan no repository at all. WARN incremented a counter the exit decision
  # never read, so the gate answered "aggregate clean. Safe to push."
  record "gitleaks-head" NOTRUN "gitleaks not installed; HEAD credential scan did not run"
  record "gitleaks-history" NOTRUN "gitleaks not installed; history walk did not run"
fi

# ----- Tool 5: test-audit-patterns.py -------------------------------------
if [ "$VERBOSE" -eq 1 ]; then hdr "tool 5/7: test-audit-patterns.py"; fi
if [ -r "$REPO_ROOT/test-audit-patterns.py" ]; then
  out=""
  run_verbose_or_capture out "$PY" test-audit-patterns.py
  rc=$?
  summary=$(printf '%s\n' "$out" | tail -1)
  if [ "$rc" -eq 0 ]; then
    record "test-audit-patterns" PASS "$summary"
  else
    record "test-audit-patterns" FAIL "rc=$rc; $summary"
  fi
else
  record_missing "test-audit-patterns" "test-audit-patterns.py"
fi

# ----- Tool 6: inline credential heuristics -------------------------------
if [ "$VERBOSE" -eq 1 ]; then hdr "tool 6/7: inline credential heuristics"; fi
# Patterns are regex SHAPES only (no literal credentials embedded). The
# harness file is added to CRED_EXCLUDE as defence-in-depth even though
# regex shapes do not self-match by inspection.
CRED_PATTERNS=(
  'AKIA[0-9A-Z]{16}'
  'ghp_[A-Za-z0-9]{36}'
  'gho_[A-Za-z0-9]{36}'
  'ghu_[A-Za-z0-9]{36}'
  'ghs_[A-Za-z0-9]{36}'
  'ghr_[A-Za-z0-9]{36}'
  'sk-ant-[A-Za-z0-9_-]{30,}'
  'sk-proj-[A-Za-z0-9_-]{30,}'
  'xox[abprs]-[A-Za-z0-9-]{10,}'
  '-----BEGIN [A-Z ]*PRIVATE KEY-----'
)
CRED_EXCLUDE='(bin/publish-audit-combined\.sh|\.git/|__pycache__)'

# v1.20.1 (OBS-S67-03) fixed exactly this construct in bin/publish-audit.sh and
# the fix was never carried to the identical one here. CRED_EXCLUDE is a PATH
# list, so it must be applied to the path field alone. Piping the whole
# `git grep -n` record (path:lineno:content) through `grep -vE` matched on file
# CONTENT as well, so a credential sitting on a line whose own text mentions
# `.git/`, `__pycache__` or this script's name was dropped before it was
# counted. Measured 2026-09-04 in a throwaway repo: with the only AKIA-shaped
# token on a line reading "The hook lives in .git/hooks", the old form recorded
# PASS "0 hits" while the form below reports it. The blind spot in this tree
# at b50dc06 is 98 tracked lines across 14 of 63 files, CHANGELOG.md among them.
# Splitting on the first colon is safe because `git grep -n` always emits the
# path first and repo paths here contain no colon.
#
# cred_scan also PRESERVES git grep's own rc instead of swallowing it. git
# grep exits 0 when it matched, 1 when it ran and matched nothing, and >1
# when it could not run at all. The call site used to read
#   matches=$(git grep -nE "$pat" 2>/dev/null | grep -vE "$CRED_EXCLUDE" || true)
# where 2>/dev/null discarded the diagnostic and "|| true" collapsed all three
# rc classes into one empty match set, so this tool recorded PASS "0 hits"
# over a repository it had never read. Measured 2026-09-04 in a throwaway
# tree: with .git renamed away and an AKIA-shaped token still on disk, git
# grep exits 128 and the old form recorded PASS.
cred_scan() {
  local _raw _rc line path
  # `-e` is load-bearing, not decoration: without it git grep parses a
  # pattern that begins with a dash as an OPTION. Measured 2026-09-04 in
  # this repo, the PEM marker pattern in CRED_PATTERNS above returned rc=129
  # with "error: unknown option" and was therefore never searched, while the old
  # `|| true` swallowed the rc and the row still read "0 hits across 10
  # patterns". 9 of 10 patterns ran and the denominator said 10. Control:
  # the same -e form over a pattern that is present returns rc=0 and 333
  # matches, so -e does not suppress ordinary matching.
  _raw=$(git grep -nE -e "$1" 2>/dev/null)
  _rc=$?
  [ "$_rc" -le 1 ] || return "$_rc"
  [ -n "$_raw" ] || return 0
  while IFS= read -r line; do
    path=${line%%:*}
    printf '%s' "$path" | grep -qE "$CRED_EXCLUDE" || printf '%s\n' "$line"
  done <<< "$_raw"
}

cred_hits=0
cred_detail=""
cred_scan_rc=0
cred_fail_pat=""
for pat in "${CRED_PATTERNS[@]}"; do
  matches=$(cred_scan "$pat")
  cred_scan_rc=$?
  [ "$cred_scan_rc" -le 1 ] || { cred_fail_pat="$pat"; break; }
  if [ -n "$matches" ]; then
    hit_n=$(printf '%s\n' "$matches" | wc -l)
    cred_hits=$((cred_hits + hit_n))
    cred_detail="${cred_detail}${pat}=${hit_n} "
    if [ "$VERBOSE" -eq 1 ]; then
      printf '[combined-audit hit] credential-heuristic pattern %s:\n' "$pat"
      printf '%s\n' "$matches" | sed 's/^/    /'
    fi
  fi
done
# State a denominator a reader can check. "0 hits across 10 patterns" counts
# PATTERNS, never files or commits, so it reads as a measured negative over a
# corpus while saying nothing about how much was searched. git grep with no
# rev searches tracked files in the working tree, so that is the count to
# report. Untracked files stay deliberately out of scope: the two documented
# callers are the pre-push hook and a cron daily run, and both concern what
# is about to be published.
tracked_n=$(git ls-files 2>/dev/null | wc -l | tr -d '[:space:]')
[ -n "$tracked_n" ] || tracked_n='?'
if [ "$cred_scan_rc" -gt 1 ]; then
  # cred_scan runs inside $( ), which is a subshell, so it cannot hand the
  # diagnostic back to this scope. Re-read it here, on the already-failing
  # path only: 2>&1 points stderr at the substitution pipe and the following
  # >/dev/null sends stdout away, so this captures stderr alone.
  cred_why=$(git grep -nE -e "${cred_fail_pat}" 2>&1 >/dev/null | head -1)
  record "credential-heuristics" NOTRUN "git grep exited $cred_scan_rc, nothing was scanned: ${cred_why:-no diagnostic}"
elif [ "$cred_hits" -eq 0 ]; then
  record "credential-heuristics" PASS "0 hits: ${#CRED_PATTERNS[@]} patterns over ${tracked_n} tracked file(s)"
else
  record "credential-heuristics" FAIL "$cred_hits hits ($cred_detail) over ${tracked_n} tracked file(s)"
fi

# ----- Tool 7: test-version-lockstep.py (I-22) ----------------------------
# Blocks a release whose version carriers have drifted from
# .claude-plugin/plugin.json. --self-test re-proves all three arms can go red
# on every invocation (~0.5s); per guards-and-negative-tests.md item 3 a guard
# whose negative test is never watched fail is not evidence.
if [ "$VERBOSE" -eq 1 ]; then hdr "tool 7/7: test-version-lockstep.py --self-test"; fi
if [ -r "$REPO_ROOT/test-version-lockstep.py" ]; then
  out=""
  run_verbose_or_capture out "$PY" test-version-lockstep.py --self-test
  rc=$?
  summary=$(printf '%s\n' "$out" | grep -E '^[0-9]+ pass / [0-9]+ fail$' | tail -1)
  [ -n "$summary" ] || summary="no summary line"
  if [ "$rc" -eq 0 ]; then
    record "test-version-lockstep" PASS "$summary"
  else
    drift=$(printf '%s\n' "$out" | grep -E '^ +-> ' | head -3 | tr -d '\n' | sed 's/  */ /g')
    record "test-version-lockstep" FAIL "rc=$rc; $summary;$drift"
  fi
else
  record_missing "test-version-lockstep" "test-version-lockstep.py"
fi

# ----- Aggregate report ---------------------------------------------------
echo ""
hdr "combined-audit summary"
printf '  %-24s %-5s %s\n' "tool" "result" "detail"
printf '  %-24s %-5s %s\n' "------------------------" "-----" "------------------------------------"
for r in "${RESULTS[@]}"; do
  IFS='|' read -r tool status detail <<< "$r"
  case "$status" in
    PASS) color_code=32 ;;
    WARN) color_code=33 ;;
    NOTRUN) color_code=35 ;;
    FAIL) color_code=31 ;;
    *)    color_code=0  ;;
  esac
  status_colored=$(color "$color_code" "$status")
  printf '  %-24s %-14s %s\n' "$tool" "$status_colored" "$detail"
done
echo ""
hdr "aggregate: ${passes} PASS, ${warns} WARN, ${notruns} NOTRUN, ${fails} FAIL"

if [ "$fails" -gt 0 ]; then
  color '31' "[combined-audit] "; printf 'FAIL: %d tool(s) reported a leak / regression. Push BLOCKED.\n' "$fails"
  exit 1
fi
# A tool that DID NOT RUN used to be recorded WARN and fall through to the
# all-clear line below. Measured 2026-09-04 by driving this block with the
# counters the gitleaks-absent path produces: 5 PASS, 2 WARN, 0 FAIL printed
# "aggregate clean. Safe to push." at rc=0, with both history-walking
# credential scanners retired and nothing saying so.
#
# Changing the WORDING alone did not fix it, because wording is not the
# channel the gate reads: .githooks/pre-push captures the harness exit code
# and branches on that and nothing else, so cannot-run stayed byte-identical
# to clean at the only surface a push consumes. Exit 3 is this plugin's
# NOT-RUNNABLE code (methodology-content/T2.md, "a check's CANNOT-RUN must
# never render as its PASS or its FOUND-A-DEFECT"), and the hook already
# blocks on every non-zero. The denominator is read from the row array
# rather than a literal 7, so it cannot drift if a tool is added.
if [ "$notruns" -gt 0 ]; then
  color '33' "[combined-audit] "; printf 'INCOMPLETE: %d of %d tool(s) DID NOT RUN. Nothing here says the tree is clean. Push BLOCKED.\n' "$notruns" "${#RESULTS[@]}"
  exit 3
fi
color '32' "[combined-audit] "; printf 'aggregate clean. Safe to push.\n'
exit 0
