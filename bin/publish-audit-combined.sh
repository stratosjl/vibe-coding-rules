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
# Six wrapped tools:
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
#
# Result semantics:
#   PASS - tool ran clean
#   FAIL - tool reported a leak / regression; aggregate exits non-zero
#   WARN - tool unavailable or known-noise (e.g., gitleaks absent);
#          aggregate still exits zero
#
# Exit codes:
#   0 - all PASS (optionally with WARN); safe to push
#   1 - one or more FAIL; push blocked
#   2 - harness-internal error (missing dependency file etc.)
#
# Usage:
#   bash bin/publish-audit-combined.sh                 quiet, hook-mode
#   bash bin/publish-audit-combined.sh --verbose       full per-tool output
#   bash bin/publish-audit-combined.sh -h | --help

set -uo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

HARNESS_VERSION="1.20.1"
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
    -h|--help)
      sed -n '2,40p' "$0"
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
fails=0

record() {
  local tool="$1" status="$2" detail="$3"
  RESULTS+=("$tool|$status|$detail")
  case "$status" in
    PASS) passes=$((passes + 1)) ;;
    WARN) warns=$((warns + 1)) ;;
    FAIL) fails=$((fails + 1)) ;;
  esac
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
if [ "$VERBOSE" -eq 1 ]; then hdr "tool 1/6: publish-audit.sh"; fi
out=""
run_verbose_or_capture out bash bin/publish-audit.sh
rc=$?
deny_n=$(printf '%s\n' "$out" | grep -E '^\[publish-audit\][[:space:]]+deny hits:' | head -1 | sed -E 's/.*deny hits:[[:space:]]+([0-9]+).*/\1/' || echo '?')
warn_n=$(printf '%s\n' "$out" | grep -E '^\[publish-audit\][[:space:]]+warn hits:' | head -1 | sed -E 's/.*warn hits:[[:space:]]+([0-9]+).*/\1/' || echo '?')
if [ "$rc" -eq 0 ]; then
  record "publish-audit" PASS "deny=${deny_n:-0} warn=${warn_n:-0}"
else
  record "publish-audit" FAIL "deny=${deny_n:-?} warn=${warn_n:-?} rc=$rc"
fi

# ----- Tool 2: publish-audit-state.sh (HEAD only; no --history) -----------
if [ "$VERBOSE" -eq 1 ]; then hdr "tool 2/6: publish-audit-state.sh (HEAD only)"; fi
out=""
run_verbose_or_capture out bash bin/publish-audit-state.sh
rc=$?
if [ "$rc" -eq 0 ]; then
  record "publish-audit-state" PASS "HEAD clean (history skipped per v1.6.0 note)"
else
  record "publish-audit-state" FAIL "HEAD has DENY hits; rc=$rc"
fi

# ----- Tools 3 + 4: gitleaks (HEAD no-git, then full history) -------------
if command -v gitleaks >/dev/null 2>&1; then
  if [ "$VERBOSE" -eq 1 ]; then hdr "tool 3/6: gitleaks HEAD (no-git)"; fi
  out=""
  run_verbose_or_capture out gitleaks detect --source "$REPO_ROOT" --no-git -v
  rc=$?
  if [ "$rc" -eq 0 ]; then
    record "gitleaks-head" PASS "no leaks found"
  else
    record "gitleaks-head" FAIL "leaks reported; rc=$rc"
  fi

  if [ "$VERBOSE" -eq 1 ]; then hdr "tool 4/6: gitleaks full history walk"; fi
  out=""
  run_verbose_or_capture out gitleaks detect --source "$REPO_ROOT" -v
  rc=$?
  if [ "$rc" -eq 0 ]; then
    record "gitleaks-history" PASS "no leaks found"
  else
    record "gitleaks-history" FAIL "leaks reported; rc=$rc"
  fi
else
  if [ "$VERBOSE" -eq 1 ]; then hdr "tools 3/6 + 4/6: gitleaks SKIPPED (not installed)"; fi
  record "gitleaks-head" WARN "gitleaks not installed"
  record "gitleaks-history" WARN "gitleaks not installed"
fi

# ----- Tool 5: test-audit-patterns.py -------------------------------------
if [ "$VERBOSE" -eq 1 ]; then hdr "tool 5/6: test-audit-patterns.py"; fi
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
  record "test-audit-patterns" WARN "test-audit-patterns.py not found at repo root"
fi

# ----- Tool 6: inline credential heuristics -------------------------------
if [ "$VERBOSE" -eq 1 ]; then hdr "tool 6/6: inline credential heuristics"; fi
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
cred_hits=0
cred_detail=""
for pat in "${CRED_PATTERNS[@]}"; do
  matches=$(git grep -nE "$pat" 2>/dev/null | grep -vE "$CRED_EXCLUDE" || true)
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
if [ "$cred_hits" -eq 0 ]; then
  record "credential-heuristics" PASS "0 hits across ${#CRED_PATTERNS[@]} patterns"
else
  record "credential-heuristics" FAIL "$cred_hits hits ($cred_detail)"
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
    FAIL) color_code=31 ;;
    *)    color_code=0  ;;
  esac
  status_colored=$(color "$color_code" "$status")
  printf '  %-24s %-14s %s\n' "$tool" "$status_colored" "$detail"
done
echo ""
hdr "aggregate: ${passes} PASS, ${warns} WARN, ${fails} FAIL"

if [ "$fails" -gt 0 ]; then
  color '31' "[combined-audit] "; printf 'FAIL: %d tool(s) reported a leak / regression. Push BLOCKED.\n' "$fails"
  exit 1
fi
color '32' "[combined-audit] "; printf 'aggregate clean. Safe to push.\n'
exit 0
