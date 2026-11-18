#!/usr/bin/env python3
"""The floor leaves nothing in the temp directory (VCR-I-49).

Runs every other floor suite (each `test-*.py` at the repository root, and
`codex/test_continuation.py`) as its own process with TMPDIR, TEMP and TMP set
to a fresh, empty directory, and fails when a suite leaves anything in it: a
`mkdtemp` folder it never removed, or a session anchor a hook wrote for a test
session id. Up to v1.35.6, five suites did (1, 16, 8, 1 and 2 entries).

A suite's exit code is read under the T2 exit-code contract (v1.36.1, VCR-I-52):
0 passed, 3 NOT-RUNNABLE (a case could not run on this platform), anything else
failed. A NOT-RUNNABLE suite is reported as such, never as a failure, and its
residue is still checked. Exit: 1 on any failure, else 3 if any suite was not
runnable, else 0; `--allow-skips` turns that 3 into 0.

Controls, run first:
  - a planted script that leaves one file is reported (the check can fail);
  - a planted script that leaves nothing is not (the check can pass);
  - planted scripts that exit 0, 3 and 1 read as passed, not runnable and
    failed, through the same function the suite loop uses;
  - a read-only file is removed by this guard's own cleanup (v1.36.1, VCR-I-53:
    on Windows git writes object files read-only, and
    `shutil.rmtree(ignore_errors=True)` silently leaves them);
  - this guard's own exit code (v1.36.2, VCR-I-56): the guard is run as a
    child with `--suites-dir` over planted suites exiting 0; 0 and 3; 0 and 3
    with `--allow-skips`; 3 and 1; and none, and must exit 0, 3, 0, 1 and 1,
    leaving nothing in the temp directory.
`--suites-dir DIR` runs only the `test-*.py` files in DIR, skips the controls
and the floor's suite count, requires at least one suite and says it is not
the floor. It exists for that control; the floor is the run without it. The
control marks its child with VCROE_RESIDUE_GUARD_CHILD, and a marked run that
reaches the controls exits 1 instead of starting children of its own.
At the end the guard checks that its own scratch root is gone.

What it cannot see, stated so nobody relies on it: a path a suite builds
without the temp-directory variables (a literal "/tmp/..."), and anything a
suite writes outside the temp directory (its own HOME handling is checked by
the suites themselves). Run on Linux and on Windows.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SELF = Path(__file__).resolve()

PASS = 0
FAIL = 0
FAILURES: list[str] = []
NOTRUN: list[str] = []


def check(ok: bool, label: str) -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK  ] {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  [FAIL] {label}")


def suite_verdict(rc: int) -> str:
    """A suite's exit code under the T2 exit-code contract: 'pass', 'notrun' or 'fail'."""
    if rc == 0:
        return "pass"
    if rc == 3:
        return "notrun"
    return "fail"


def force_rmtree(path: Path) -> None:
    """Remove `path`, read-only files included. Clearing the read-only bit first
    works on every Python version, unlike rmtree's onerror/onexc split."""
    if not path.exists():
        return
    for dirpath, dirnames, filenames in os.walk(path):
        for name in dirnames + filenames:
            p = os.path.join(dirpath, name)
            try:
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if name in dirnames else 0))
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


CHILD_ENV = "VCROE_RESIDUE_GUARD_CHILD"


def residue(cmd: list[str], scratch: Path, extra_env: "dict[str, str] | None" = None) -> tuple[int, list[str]]:
    """Run `cmd` with an empty temp directory; return its exit code and what it left."""
    tmp = Path(tempfile.mkdtemp(prefix="run-", dir=scratch))
    env = os.environ.copy()
    env.update({"TMPDIR": str(tmp), "TEMP": str(tmp), "TMP": str(tmp)})
    env.update(extra_env or {})
    r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    left = sorted(p.name for p in tmp.iterdir())
    return r.returncode, left


def run_suites(suites: list[Path], scratch: Path, base: Path) -> None:
    """Run each suite under `residue` and record its verdict and its leftovers."""
    for suite in suites:
        name = suite.relative_to(base)
        rc, left = residue([sys.executable, str(suite)], scratch)
        shown = left[:5] + ([f"... {len(left) - 5} more"] if len(left) > 5 else [])
        verdict = suite_verdict(rc)
        if verdict == "notrun":
            NOTRUN.append(f"{name}: exit 3")
            print(f"  [NRUN] {name}: exit 3 (NOT-RUNNABLE on this platform; not a failure)")
        else:
            check(verdict == "pass", f"{name}: exit {rc}")
        check(left == [], f"{name}: {len(left)} entr{'y' if len(left) == 1 else 'ies'} "
                          f"left in the temp directory {shown}")


def finish(allow_skips: bool) -> int:
    """Print the summary and return the exit code: 1 on any failure, else 3 if a
    suite was not runnable (0 with --allow-skips), else 0."""
    print(f"\n{PASS} pass / {FAIL} fail / {len(NOTRUN)} not runnable")
    for f in FAILURES:
        print(f"  - {f}")
    for n in NOTRUN:
        print(f"  - NOT-RUNNABLE {n}")
    if FAIL:
        return 1
    if NOTRUN and not allow_skips:
        print("VERDICT: NOT-RUNNABLE (a suite could not run every case here; pass --allow-skips to accept)")
        return 3
    return 0


def child(suites_dir: Path, allow_skips: bool) -> int:
    """--suites-dir: run only the planted suites in `suites_dir` (VCR-I-56). No controls."""
    print(f"Child run over {suites_dir}: planted suites only; this is not the floor")
    suites = sorted(suites_dir.glob("test-*.py"))
    scratch = Path(tempfile.mkdtemp(prefix="vcroe-floor-residue-child-"))
    try:
        check(len(suites) >= 1, f"{len(suites)} planted suite(s) found (at least 1 expected)")
        run_suites(suites, scratch, suites_dir)
    finally:
        force_rmtree(scratch)
    check(not scratch.exists(), "this child run's scratch root is removed")
    return finish(allow_skips)


def main(argv: list[str]) -> int:
    allow_skips = "--allow-skips" in argv
    if "--suites-dir" in argv:
        i = argv.index("--suites-dir")
        if i + 1 >= len(argv):
            print("--suites-dir needs a directory")
            return 1
        return child(Path(argv[i + 1]), allow_skips)
    if os.environ.get(CHILD_ENV):
        # A child run reached the controls, which would start children of its own
        # without bound. Stop here; the parent's control reads this exit as wrong.
        print(f"{CHILD_ENV} is set but --suites-dir was not handled: refusing to run the controls")
        return 1
    scratch = Path(tempfile.mkdtemp(prefix="vcroe-floor-residue-"))
    try:
        print("Controls")
        leak = scratch / "leak.py"
        leak.write_text("import tempfile\ntempfile.mkdtemp(prefix='planted-')\n", encoding="utf-8")
        clean = scratch / "clean.py"
        clean.write_text("import tempfile\nwith tempfile.TemporaryDirectory():\n    pass\n", encoding="utf-8")
        rc, left = residue([sys.executable, str(leak)], scratch)
        check(rc == 0 and len(left) == 1 and left[0].startswith("planted-"),
              f"a planted leftover is reported: {left}")
        rc, left = residue([sys.executable, str(clean)], scratch)
        check(rc == 0 and left == [], f"a script that cleans up is not reported: {left}")
        for code, want in ((0, "pass"), (3, "notrun"), (1, "fail")):
            planted = scratch / f"exit{code}.py"
            planted.write_text(f"import sys\nsys.exit({code})\n", encoding="utf-8")
            rc, _left = residue([sys.executable, str(planted)], scratch)
            check(suite_verdict(rc) == want, f"a planted script exiting {code} reads as {want}: "
                                             f"rc={rc} verdict={suite_verdict(rc)}")
        ro = scratch / "readonly-tree"
        (ro / "objects").mkdir(parents=True)
        ro_file = ro / "objects" / "blob"
        ro_file.write_text("x", encoding="utf-8")
        os.chmod(ro_file, stat.S_IREAD)
        force_rmtree(ro)
        check(not ro.exists(), "this guard's cleanup removes a tree holding a read-only file")
        # VCR-I-56: the guard's own exit code, through main's real return path.
        for plants, extra, want in (((0,), [], 0), ((0, 3), [], 3), ((0, 3), ["--allow-skips"], 0),
                                    ((3, 1), [], 1), ((), [], 1)):
            d = scratch / ("suites-" + "-".join(map(str, plants)) + ("-allow" if extra else "") + "-x")
            d.mkdir()
            for code in plants:
                (d / f"test-exit{code}.py").write_text(f"import sys\nsys.exit({code})\n", encoding="utf-8")
            rc, left = residue([sys.executable, str(SELF), "--suites-dir", str(d), *extra], scratch,
                               {CHILD_ENV: "1"})
            shown = "suites exiting " + (", ".join(map(str, plants)) or "none")
            check(rc == want and left == [],
                  f"the guard over planted {shown}{' with --allow-skips' if extra else ''} exits {want}: "
                  f"rc={rc}, {len(left)} left in the temp directory")

        suites = sorted(p for p in ROOT.glob("test-*.py") if p.resolve() != SELF)
        suites.append(ROOT / "codex" / "test_continuation.py")
        print(f"Suites: {len(suites)}")
        check(len(suites) >= 17, f"{len(suites)} suites found (at least 17 expected)")
        run_suites(suites, scratch, ROOT)
    finally:
        force_rmtree(scratch)
    check(not scratch.exists(), "this guard's own scratch root is removed")
    return finish(allow_skips)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
