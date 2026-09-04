#!/usr/bin/env python3
"""Synthetic-fixture regression test for bin/audit-patterns.sh.

Closes OBS-vcroe-s55-test-method-broken-by-v1.2.1-01. The pre-v1.2.1
working-tree probe (untracked `.s55-leak-test.txt` scanned by
`grep -rnE`) stopped working at v1.2.1 when publish-audit.sh switched
to `git grep -nE`, which skips untracked files. This test substitutes
that probe with a pattern-level unit assertion: each DENY and WARN
regex in bin/audit-patterns.sh is verified to match at least one
synthetic fixture line built at runtime by sanitizing the pattern to
its literal form. A pattern whose ERE this sanitizer cannot model is
counted as a skip, not a failure, and the pass line states the
denominator, so a partial run cannot read as full coverage. A pattern
the sanitizer DOES model but Python's `re` cannot compile is put through
`grep -E`, the engine family the gate itself scans with, rather than
skipped: skipping it would leave that arm unable to fail.

The fixture strings are constructed from the patterns sourced from
bin/audit-patterns.sh at test time; no pattern literal is hard-coded
in this file. A self-audit step additionally asserts that no sanitized
literal appears as a contiguous substring of this file's own source,
so the audit gate stays tight without adding test-audit-patterns.py to
SCAN_EXCLUDE.

Pure stdlib. No working-tree side-effects.

Usage:
    python3 test-audit-patterns.py
    python3 test-audit-patterns.py --verbose
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
PATTERNS_FILE = PLUGIN_ROOT / "bin" / "audit-patterns.sh"


def extract_patterns(array_name: str) -> list[str]:
    cmd = [
        "bash",
        "-c",
        f'set -e; cd "{PLUGIN_ROOT}"; source bin/audit-patterns.sh; '
        f'printf "%s\\n" "${{{array_name}[@]}}"',
    ]
    out = subprocess.check_output(cmd, text=True).rstrip("\n")
    return out.split("\n") if out else []


def _class_literal(cls_body: str) -> str:
    if "0-9" in cls_body:
        return "1"
    if "A-Z" in cls_body:
        return "A"
    if "a-z" in cls_body:
        return "a"
    for c in cls_body:
        if c not in "-^":
            return c
    return "x"


class UnsupportedConstruct(ValueError):
    """The sanitizer cannot build a fixture for this pattern.

    Distinct from a malformed pattern: it says the INSTRUMENT is too
    narrow, not that the subject is broken. T2's exit-code contract puts
    it as "the loud symptom points at the subject rather than at the
    instrument", and this is the arm where that happens.

    It is reached by ordinary correct overlay entries, not by exotic
    ones. bin/audit-patterns.local.sh.example line 9 tells the operator
    to write the overlay in extended-grep syntax, and alternation is core
    ERE, so a DENY entry of the form `Firm (Asset|Capital) Management`
    lands here. Measured 2026-09-04 on a synthetic overlay: it raised,
    main() counted it a failure, Tool 5 went FAIL, and the aggregate
    blocked every push from that clone until the overlay was edited. A
    fixture this test cannot build is not a leak, so it must not block.

    grep_e_matches() raises it for the one remaining undecidable case:
    a pattern that sanitizes here and that `grep -E` itself refuses, so
    neither engine can say whether the pattern matches its own fixture.
    """


class Unrunnable(RuntimeError):
    """A guard's dependency is absent, so the guard did not run.

    Distinct from the strings a guard RETURNS, which are assertion
    failures about the subject. methodology-content/T2.md:55 defines
    exit 3 as "NOT-RUNNABLE: a dependency is missing, the ENVIRONMENT is
    wrong", and its class list at line 65 names "a hook whose helper
    moved". bin/publish-audit-state.sh is the helper of two guards here,
    so its absence is that case and not a finding about the pattern set.
    Raised rather than returned so it cannot be appended to the same list
    that carries real assertion failures, which is how the two used to
    share exit 1 and send a reader hunting a leak-pattern regression.
    """


def sanitize_to_literal(pattern: str) -> str:
    """Build a literal that the ERE pattern matches.

    Handles the regex constructs used in bin/audit-patterns.sh today:
    backslash word boundaries (dropped), escaped dot (unescaped),
    escaped whitespace (single space), single-bracket character class
    with optional `+`/`*`/`?` quantifier.

    Raises UnsupportedConstruct for ERE meta this sanitizer does not
    model, and plain ValueError for a pattern that is malformed. main()
    counts the first as a skip and fails on the second.

    No message raised here carries the pattern text. These arrays include
    the gitignored operator overlay, so anything printed reaches the
    combined harness's row detail (bin/publish-audit-combined.sh:204
    takes the last line of the run) and every log that captures it.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            nxt = pattern[i + 1] if i + 1 < len(pattern) else ""
            if nxt in ("b", "B"):
                i += 2
                continue
            if nxt == ".":
                out.append(".")
                i += 2
                continue
            if nxt == "s":
                out.append(" ")
                i += 2
                continue
            out.append(nxt)
            i += 2
        elif ch == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                raise ValueError("Unterminated character class")
            out.append(_class_literal(pattern[i + 1 : j]))
            i = j + 1
            if i < len(pattern) and pattern[i] in "*+?":
                i += 1
        elif ch in ("*", "+", "?"):
            # Quantifier applied to the preceding literal char (the bracket-class
            # case is consumed above). The single copy already emitted satisfies
            # all three: `?`/`*` make it optional (one occurrence still matches)
            # and `+` requires at least one (which we have). A quantifier with no
            # preceding token is malformed. This is what lets a pattern with an
            # optional trailing literal char (e.g. a plural `s?`) sanitize.
            if not out:
                raise ValueError(f"Quantifier {ch!r} with no preceding token")
            i += 1
        elif ch in ("^", "$", "(", ")", "|", "{"):
            raise UnsupportedConstruct(
                f"unsupported ERE meta {ch!r}; extend sanitize_to_literal"
            )
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def build_fixture(literal: str) -> str:
    return f"sample-context [{literal}] trailing-context"


def grep_e_matches(pattern: str, fixture: str) -> bool:
    """Ask the engine the GATE uses whether PATTERN matches FIXTURE.

    bin/publish-audit.sh scans with `git grep -nE`, so ERE is the
    authority on these arrays and Python's `re` is only a convenience.
    A pattern Python cannot compile is therefore not undecidable, and
    treating it as undecidable widens the leak gate: measured 2026-09-04
    with GNU grep 3.0, a DENY entry of the shape `zsynth\\U[^a]` is
    rejected by Python, accepted by `grep -E`, and does NOT match the
    fixture this file builds for it. That is a pattern which cannot
    match what it was written to match, and it is precisely what this
    test exists to report.

    Returns True on a match and False on a clean no-match. Raises
    UnsupportedConstruct only when grep itself declines the pattern
    (rc 2, e.g. an invalid back reference), which is the one case
    neither engine can decide.

    Both the pattern and the fixture travel in the ENVIRONMENT, never on
    the command line. These arrays carry the gitignored operator overlay
    and the fixture is derived from it, so neither belongs in a process
    listing any more than in the stderr this file already keeps them out
    of. Nothing here is returned or printed either: grep's stderr quotes
    the pattern on a rejection, so it is captured and discarded.
    """
    env = dict(os.environ)
    env["VC_ROE_GREP_PATTERN"] = pattern
    env["VC_ROE_GREP_FIXTURE"] = fixture
    proc = subprocess.run(
        [
            "bash",
            "-c",
            'printf "%s\\n" "$VC_ROE_GREP_FIXTURE" '
            '| grep -E -q -e "$VC_ROE_GREP_PATTERN"',
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 1:
        return False
    raise UnsupportedConstruct(
        f"the gate's own engine could not decide it either (grep -E "
        f"rc={proc.returncode})"
    )


def check_exclusion_is_path_scoped(deny: list[str]) -> list[str]:
    """End-to-end guard for OBS-S67-03.

    SCAN_EXCLUDE is a PATH exclusion list. Until v1.20.1 both scan loops in
    bin/publish-audit.sh applied it to the whole `git grep -n` record, so a
    genuine DENY hit was discarded whenever the matched line's own TEXT
    mentioned an excluded token. A real leak survived in CHANGELOG.md for
    exactly that reason while the gate printed "Safe to push".

    This builds a throwaway git repo, plants a DENY literal on a line that
    also contains an excluded token, and asserts the gate blocks. It then
    asserts the path-scoped exclusion still works, so the fix cannot be
    "make SCAN_EXCLUDE do nothing".

    Both the DENY literal and the excluded token are derived at runtime, so
    no pattern literal enters this file's source and the self-audit above
    stays meaningful.
    """
    fails: list[str] = []
    literal = sanitize_to_literal(deny[0])
    # An excluded token, taken from the exclusion list rather than retyped.
    excluded_token = "bin/audit-patterns" + ".sh"

    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        (repo / "bin").mkdir()
        for name in ("audit-patterns.sh", "publish-audit.sh"):
            shutil.copy2(PLUGIN_ROOT / "bin" / name, repo / "bin" / name)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)

        def audit() -> tuple[int, str]:
            p = subprocess.run(
                ["bash", "bin/publish-audit.sh"],
                cwd=repo, capture_output=True, text=True,
            )
            return p.returncode, p.stdout + p.stderr

        def plant(relpath: str, body: str) -> None:
            target = repo / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body + "\n", encoding="utf-8")
            # git grep only sees tracked-or-indexed files (I-8 lesson).
            subprocess.run(["git", "add", relpath], cwd=repo, check=True)

        # 1. Control: the harness must be silent on a clean tree, otherwise
        #    every later assertion is meaningless.
        rc, out = audit()
        if rc != 0:
            fails.append(f"exclusion-guard: clean fixture repo did not pass (rc={rc})")

        # 2. The regression itself: DENY literal on a line that also carries
        #    an excluded token, in a file whose PATH is not excluded.
        plant("NOTES.md", f"see {excluded_token} for details, host is {literal}")
        rc, out = audit()
        if rc == 0:
            fails.append(
                "exclusion-guard: DENY literal was NOT caught when the same line "
                "also contained an excluded token: SCAN_EXCLUDE is matching "
                "file content, not the path field (OBS-S67-03 regression)"
            )

        # 3. The exclusion must still exclude by path, so the fix is not
        #    simply the removal of the filter.
        subprocess.run(["git", "rm", "-q", "--cached", "NOTES.md"], cwd=repo, check=True)
        (repo / "NOTES.md").unlink()
        plant("bin/audit-patterns.local.sh.example", f"host is {literal}")
        rc, out = audit()
        if rc != 0:
            fails.append(
                "exclusion-guard: a DENY literal inside an excluded PATH was "
                "flagged; SCAN_EXCLUDE no longer excludes by path"
            )

    return fails


def extract_scalar(var_name: str) -> str:
    cmd = [
        "bash",
        "-c",
        f'set -e; cd "{PLUGIN_ROOT}"; source bin/audit-patterns.sh; '
        f'printf "%s" "${{{var_name}}}"',
    ]
    return subprocess.check_output(cmd, text=True)


def check_history_walk_pins_current_scanner() -> list[str]:
    """End-to-end guard for OBS-S67-04.

    `bin/publish-audit-state.sh --history` checked out each commit and then
    ran THAT COMMIT'S scanner. Old scanners in this repo's history carry a
    bracketed placeholder in their DENY array, left behind by an early leak
    scrub. A pattern written as [SOME-NAME] is not a literal: as an ERE it is
    a character class matching any one of those letters, so those commits
    scanned themselves with a near-universal matcher and reported floods of
    hits on ordinary prose. The walk's verdict was noise in both directions.

    Fixture: an origin repo on `main` with two commits. The older one ships a
    scanner whose DENY array holds a bracketed placeholder; HEAD ships the
    current scanner. Neither tree contains a real leak, so a trustworthy walk
    reports clean. Pre-fix, the old commit self-matches and the walk reports
    a historical leak.

    The placeholder is assembled at runtime so no bracketed token enters this
    file's source, where the real gate would later scan it.
    """
    fails: list[str] = []
    state_script = PLUGIN_ROOT / "bin" / "publish-audit-state.sh"
    if not state_script.is_file():
        raise Unrunnable("history-walk-guard: bin/publish-audit-state.sh not found")

    author = extract_scalar("PUBLIC_AUTHOR_EMAIL").strip()
    if not author:
        raise Unrunnable(
            "history-walk-guard: PUBLIC_AUTHOR_EMAIL is empty; the scaffold "
            "bin/audit-patterns.sh sets it, so the tree is damaged"
        )

    placeholder = "[" + "REDACTED" + "-" + "NAME" + "]"
    # Innocuous prose. It contains letters that the placeholder-as-character
    # class matches, which is the whole point: nothing here is a real leak.
    prose = "PROJECT NOTES\n\nOrdinary README content, no operator identifiers.\n"

    stale_patterns = (
        "#!/usr/bin/env bash\n"
        f"PUBLIC_AUTHOR_EMAIL='{author}'\n"
        f"DENY_PATTERNS=( '{placeholder}' )\n"
        "WARN_PATTERNS=( 'zzz-never-matches-anything-zzz' )\n"
        "SCAN_EXCLUDE='(__pycache__|(^|/)\\.git/|bin/publish-audit\\.sh"
        "|bin/publish-audit-state\\.sh|bin/audit-patterns\\.sh)'\n"
    )

    def git(repo: Path, *args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True,
                       capture_output=True, text=True)

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        origin = root / "origin"
        origin.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=origin, check=True)
        git(origin, "config", "user.email", author)
        git(origin, "config", "user.name", "audit-fixture")
        git(origin, "config", "commit.gpgsign", "false")
        (origin / "bin").mkdir()

        # Commit 1: the stale scanner, with the placeholder DENY entry.
        shutil.copy2(PLUGIN_ROOT / "bin" / "publish-audit.sh",
                     origin / "bin" / "publish-audit.sh")
        (origin / "bin" / "audit-patterns.sh").write_text(
            stale_patterns, encoding="utf-8")
        (origin / "README.md").write_text(prose, encoding="utf-8")
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", "old commit with stale scanner")

        # Commit 2 (HEAD): the current scanner.
        shutil.copy2(PLUGIN_ROOT / "bin" / "audit-patterns.sh",
                     origin / "bin" / "audit-patterns.sh")
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", "current scanner")

        work = root / "work"
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
        git(work, "config", "user.email", author)
        git(work, "config", "user.name", "audit-fixture")

        proc = subprocess.run(
            ["bash", str(state_script), "--history"],
            cwd=work, capture_output=True, text=True, timeout=300,
        )
        out = proc.stdout + proc.stderr
        if proc.returncode != 0 or "history walk clean" not in out:
            fails.append(
                "history-walk-guard: the walk reported a historical leak on a "
                "fixture whose only 'hit' is an old commit's own placeholder "
                "DENY pattern being read as a character class. The walk must "
                "run the CURRENT scanner against each commit's tree "
                "(OBS-S67-04 regression). "
                f"rc={proc.returncode} tail={out[-400:]!r}"
            )

    return fails


def check_history_baseline_is_enumerated(deny: list[str]) -> list[str]:
    """Negative tests for the v1.20.2 accepted-history baseline (OBS-S67-04).

    The baseline exists so a walk that correctly reports knowingly-accepted
    residue does not go permanently red. That makes it a forgiveness
    mechanism, and a forgiveness mechanism nobody has watched refuse is
    indistinguishable from a switch that turns the check off. So all three
    are asserted here: it forgives the listed pair, it refuses a pair it does
    not list, and it refuses even a listed pair once the commit is newer than
    baseline-sha.

    Fixture history on `main`: A clean, B carrying a DENY literal, C clean.
    """
    fails: list[str] = []
    state_script = PLUGIN_ROOT / "bin" / "publish-audit-state.sh"
    if not state_script.is_file():
        raise Unrunnable("history-baseline-guard: bin/publish-audit-state.sh not found")

    author = extract_scalar("PUBLIC_AUTHOR_EMAIL").strip()
    pattern = deny[0]
    literal = sanitize_to_literal(pattern)

    def git(repo: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        origin = root / "origin"
        origin.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=origin, check=True)
        git(origin, "config", "user.email", author)
        git(origin, "config", "user.name", "audit-fixture")
        git(origin, "config", "commit.gpgsign", "false")
        (origin / "bin").mkdir()
        for name in ("audit-patterns.sh", "publish-audit.sh"):
            shutil.copy2(PLUGIN_ROOT / "bin" / name, origin / "bin" / name)

        (origin / "README.md").write_text("clean\n", encoding="utf-8")
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", "A: clean")
        sha_a = git(origin, "rev-parse", "HEAD")

        (origin / "README.md").write_text(f"host is {literal}\n", encoding="utf-8")
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", "B: carries the literal")
        sha_b = git(origin, "rev-parse", "HEAD")

        (origin / "README.md").write_text("clean again\n", encoding="utf-8")
        git(origin, "add", "-A")
        git(origin, "commit", "-q", "-m", "C: scrubbed at HEAD")

        work = root / "work"
        subprocess.run(["git", "clone", "-q", str(origin), str(work)], check=True)
        git(work, "config", "user.email", author)
        git(work, "config", "user.name", "audit-fixture")

        baseline = work / "bin" / "audit-history-baseline.txt"

        def walk() -> tuple[int, str]:
            p = subprocess.run(["bash", str(state_script), "--history"],
                               cwd=work, capture_output=True, text=True,
                               timeout=300)
            return p.returncode, p.stdout + p.stderr

        def write_baseline(sha: str, path_col: str) -> None:
            baseline.write_text(
                f"# baseline-sha: {sha}\n{path_col}\t{pattern}\n", encoding="utf-8")

        # 1. Listed pair, commit within the baseline window: forgiven.
        write_baseline(sha_b, "README.md")
        rc, out = walk()
        if rc != 0 or "history walk clean" not in out:
            fails.append(
                "history-baseline-guard: a listed (path, pattern) pair inside "
                f"the baseline window was still reported as a leak. rc={rc} "
                f"tail={out[-300:]!r}")

        # 2. Same finding, pair NOT listed: must still fail.
        write_baseline(sha_b, "SOMETHING-ELSE.md")
        rc, out = walk()
        if rc == 0 or "historical leak" not in out:
            fails.append(
                "history-baseline-guard: a DENY hit whose (path, pattern) pair "
                "is NOT in the baseline was forgiven. The baseline is behaving "
                "as an off switch rather than as an enumeration.")

        # 3. Listed pair, but the commit is NEWER than baseline-sha: must fail,
        #    otherwise the baseline would forgive a fresh reintroduction.
        write_baseline(sha_a, "README.md")
        rc, out = walk()
        if rc == 0 or "historical leak" not in out:
            fails.append(
                "history-baseline-guard: a listed pair was forgiven in a commit "
                f"that is NOT an ancestor of baseline-sha ({sha_a[:12]}); the "
                f"finding is in {sha_b[:12]}. A new commit could reintroduce an "
                "accepted identifier and pass.")

    return fails


def main(argv: list[str]) -> int:
    verbose = "--verbose" in argv or "-v" in argv

    try:
        deny = extract_patterns("DENY_PATTERNS")
        warn = extract_patterns("WARN_PATTERNS")
    except subprocess.CalledProcessError as exc:
        print(f"test-audit-patterns: FAIL, could not source audit-patterns.sh: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        # bash is a DEPENDENCY, not the subject. CalledProcessError covers only
        # a bash that ran and exited non-zero; a bash that cannot be SPAWNED
        # raises OSError, and it arrives in three shapes, all measured
        # 2026-09-04: FileNotFoundError when nothing on PATH carries the name,
        # PermissionError when a directory does, and plain OSError when a
        # non-executable file does. All three mean the check did not run.
        # This is not hypothetical on the Windows node: CHANGELOG.md:694 tells
        # the operator to run this file directly as part of a regression sweep,
        # and bash is not on that node's PowerShell PATH, so the documented
        # command died on an uncaught traceback whose last line named no file
        # at all. CalledProcessError is a SubprocessError and disjoint from
        # OSError, so the handler above still owns "bash ran and the script is
        # broken" and that stays exit 1.
        print(
            f"test-audit-patterns: NOT-RUNNABLE, could not run bash to source "
            f"bin/audit-patterns.sh, so no pattern was examined: {exc}",
            file=sys.stderr,
        )
        return 3

    if not deny:
        print("test-audit-patterns: FAIL, DENY_PATTERNS array empty", file=sys.stderr)
        return 1

    own_source = Path(__file__).read_text(encoding="utf-8")
    fails: list[str] = []
    skipped: list[str] = []
    verified = 0
    verified_deny: list[str] = []
    total = len(deny) + len(warn)

    # Every message below names a pattern by kind and 1-based index, never by
    # its text. These arrays carry the gitignored operator overlay, so the old
    # form printed the operator's private DENY entries verbatim to stderr,
    # which the combined harness captures and quotes in its FAIL row detail
    # (bin/publish-audit-combined.sh:204 takes the last line of the run).
    # Kind plus index locates the entry in a file the operator owns, which is
    # all a reader of that row needs.
    for kind, patterns in (("DENY", deny), ("WARN", warn)):
        for idx, pat in enumerate(patterns, start=1):
            try:
                lit = sanitize_to_literal(pat)
            except UnsupportedConstruct as exc:
                skipped.append(f"{kind} pattern #{idx}: {exc}")
                continue
            except ValueError as exc:
                fails.append(f"{kind} pattern #{idx}: malformed pattern: {exc}")
                continue
            fixture = build_fixture(lit)
            try:
                m = re.search(pat, fixture)
            except re.error as exc:
                # The gate runs these through `grep -E`, not through Python, so
                # a construct Python rejects can be perfectly good ERE: a
                # Windows path root is a plain literal to grep and an
                # incomplete escape to Python. That makes Python's refusal a
                # reason to ask the OTHER engine, not a reason to skip. Skipping
                # it would leave this arm unable to fail, and the failure it
                # would stop reporting is real: measured 2026-09-04 with GNU
                # grep 3.0, `zsynth\U[^a]` is rejected by Python, accepted by
                # grep -E, and does not match its own fixture. The re module's
                # message quotes at most a metacharacter fragment and never the
                # pattern; grep_e_matches keeps both off the command line.
                try:
                    m = grep_e_matches(pat, fixture)
                except UnsupportedConstruct as gexc:
                    skipped.append(
                        f"{kind} pattern #{idx}: not parseable by Python re "
                        f"({exc}), and {gexc}"
                    )
                    continue
            if not m:
                fails.append(f"{kind} pattern #{idx}: did not match its own fixture")
                continue
            if lit in own_source:
                fails.append(
                    f"{kind} pattern #{idx}: its sanitized literal appears in this "
                    f"test's own source: audit gate hole risk; refactor to keep the "
                    f"literal out of source."
                )
                continue
            verified += 1
            if kind == "DENY":
                verified_deny.append(pat)
            if verbose:
                # The one place a pattern is printed. --verbose is the operator
                # asking to see their own patterns on their own terminal, and
                # the combined harness never passes the flag
                # (bin/publish-audit-combined.sh:198).
                print(f"  OK  {kind:4} {pat!r}  fixture-literal={lit!r}")

    # TWO of the three structural guards build their fixture from deny[0]:
    # check_exclusion_is_path_scoped and check_history_baseline_is_enumerated.
    # check_history_walk_pins_current_scanner takes no argument and assembles
    # its own placeholder, so it runs whatever the pattern set looks like.
    #
    # Hand the first two only the DENY entries this run actually verified.
    # `verified == 0` was the wrong precondition for that: it neither implies
    # nor is implied by "deny[0] can be sanitized". Measured 2026-09-04 with an
    # overlay whose first DENY entry is an alternation, every other entry
    # verifying: verified was 10, so the run fell through, and
    # sanitize_to_literal(deny[0]) then raised UNCAUGHT inside the guard, rc=1,
    # with no verdict line for the combined harness to quote. Selecting a
    # verified entry guards the real precondition instead of a proxy for it.
    notrunnable: list[str] = []
    if verified_deny:
        guard_plan = (
            (check_exclusion_is_path_scoped, (verified_deny,)),
            (check_history_walk_pins_current_scanner, ()),
            (check_history_baseline_is_enumerated, (verified_deny,)),
        )
    else:
        # No fixture is available, so the two that need one cannot run. The
        # third still can, and it is kept rather than dropped: this path used
        # to return before any guard ran, which silently removed coverage the
        # pattern set has no bearing on.
        notrunnable.append(
            "no DENY pattern was fixture-verified, so the exclusion-scope and "
            "history-baseline guards had no fixture to build and did not run"
        )
        guard_plan = ((check_history_walk_pins_current_scanner, ()),)

    for guard, args in guard_plan:
        try:
            fails.extend(guard(*args))
        except Unrunnable as exc:
            notrunnable.append(str(exc))

    # Skips and cannot-runs print on EVERY exit path below, before the verdict.
    # A verdict with an unstated denominator reads as full coverage, and that
    # is as wrong on a FAIL (which checks did not run?) as it is on a pass.
    for line in skipped:
        print(f"  SKIP  {line}", file=sys.stderr)
    for line in notrunnable:
        print(f"  NOT-RUN  {line}", file=sys.stderr)

    if fails:
        print("test-audit-patterns: FAIL", file=sys.stderr)
        for line in fails:
            print(f"  {line}", file=sys.stderr)
        return 1

    # Nothing failed, but something did not run, so a zero here would be a
    # vacuous pass. Reported after fails, because a defect that WAS found
    # outranks a check that could not look.
    if notrunnable:
        print(
            f"test-audit-patterns: NOT-RUNNABLE, {verified} of {total} patterns "
            f"fixture-verified, {len(skipped)} skipped, and {len(notrunnable)} "
            f"check(s) could not run",
            file=sys.stderr,
        )
        return 3

    print(
        f"test-audit-patterns: OK ({verified} of {total} patterns fixture-verified "
        f"({len(deny)} DENY + {len(warn)} WARN present), {len(skipped)} skipped, "
        f"self-audit clean, exclusion is path-scoped, history walk pins the "
        f"current scanner)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
