#!/usr/bin/env python3
"""Release-time version-lockstep guard (I-22).

`.claude-plugin/plugin.json` is single-source-of-truth for the plugin version.
CONTRIBUTING.md calls the mirrors the "9-constant lockstep". Every content-only
release re-broke it: the manifest was bumped, the mirrors were not, and nothing
failed. Measured 2026-08-25 at v1.21.0 — the manifest read `1.21.0` while all
EIGHT mirrors still read `1.20.2`, in the working tree AND in the installed
plugin cache, so every hook log entry stamped the wrong version.

This runner closes that gap from three directions:

  ARM 1  static-carrier scan. No hand-maintained version LITERAL may exist in
         `hooks/**/*.py` or `bin/*.sh`. Seven of the nine constants are now
         derived at load; a literal reappearing is a regression, and if one is
         reintroduced deliberately it must equal the manifest.

  ARM 2  runtime resolution. Each hook is executed and its resolved
         `ROUTINE_VERSION` read back. It must equal the manifest and must NOT
         be the `unknown` fail-soft sentinel. This is the positive assertion
         per `guards-and-negative-tests.md` item 12: a silently-broken resolver
         would leave every carrier "not drifted" while stamping nothing useful,
         which ARM 1 alone cannot see.

  ARM 3  manifest mirrors. `kimi.plugin.json` is a second static manifest
         consumed by Kimi Code and CANNOT derive from the first. It stays
         hand-maintained and is therefore the one carrier that can still drift.

Self-proving. `--self-test` builds three synthetic trees in a temp dir, each
carrying exactly one injected defect, and asserts the corresponding arm reports
it. A guard whose negative test has never been watched go red is not evidence
(`guards-and-negative-tests.md` item 3), and a NEW guard can silently disable
the arms beside it (item 10), so all three arms are proven independently.

Cross-platform: pure stdlib, `sys.executable` (never the broken `python3`
Microsoft Store alias on the Windows node).

Usage:
    python test-version-lockstep.py               # guard the real tree
    python test-version-lockstep.py --self-test   # prove all three arms fire
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

# The five Claude hooks plus the Kimi adapter. Each is executed in ARM 2 and
# must expose a resolved version constant under the name given here.
RUNTIME_CARRIERS = [
    (Path("hooks") / "session-start.py", "ROUTINE_VERSION"),
    (Path("hooks") / "user-prompt-submit.py", "ROUTINE_VERSION"),
    (Path("hooks") / "post-tool-use.py", "ROUTINE_VERSION"),
    (Path("hooks") / "stop.py", "ROUTINE_VERSION"),
    (Path("hooks") / "session-end.py", "ROUTINE_VERSION"),
    (Path("hooks") / "kimi" / "_adapter.py", "KIMI_ADAPTER_VERSION"),
]

# Static manifests that mirror the source-of-truth manifest by hand.
MANIFEST_MIRRORS = [Path("kimi.plugin.json")]

# A hand-maintained version LITERAL. Matches an assignment to a quoted
# dotted-numeric string only, so `ROUTINE_VERSION = _resolve_version()` and
# the `unknown` sentinel inside the resolver body do not trip it. The optional
# `: str` annotation is deliberate — a detector that only knew the bare form
# would miss `ROUTINE_VERSION: str = "1.2.3"`, and a pattern detector's blind
# spots are exactly what has to be asserted (`guards-and-negative-tests.md`
# item 7). `_regex_blind_spot_probe()` proves both forms are caught.
PY_LITERAL_RE = re.compile(
    r"^\s*(ROUTINE_VERSION|KIMI_ADAPTER_VERSION)"
    r"(?:\s*:\s*[A-Za-z_][\w\[\], .]*)?"
    r"\s*=\s*[\"'](\d+\.\d+[^\"']*)[\"']"
)
SH_LITERAL_RE = re.compile(
    r"^\s*(HARNESS_VERSION)\s*=\s*[\"']?(\d+\.\d+[^\"'\s]*)[\"']?"
)

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  FAIL {label}")


def manifest_version(root: Path) -> str:
    """The single source of truth. Empty string if unreadable."""
    try:
        with open(root / ".claude-plugin" / "plugin.json", encoding="utf-8") as f:
            return str(json.load(f).get("version", "")).strip()
    except Exception:
        return ""


def resolve_runtime_version(root: Path, rel: Path, const: str) -> str:
    """Execute a hook and read back the version constant it actually resolved.

    Runs in a subprocess against a throwaway HOME so a hook that touches its
    log path cannot write into the operator's tree. Returns the constant, or a
    `<error: ...>` marker the caller reports verbatim.
    """
    src = root / rel
    prog = (
        "import importlib.util,sys\n"
        "spec=importlib.util.spec_from_file_location('vc_roe_probe',sys.argv[1])\n"
        "mod=importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "sys.stderr.write(str(getattr(mod,sys.argv[2],'<absent>')))\n"
    )
    tmp_home = tempfile.mkdtemp(prefix="vc-roe-lockstep-home-")
    try:
        env = dict(os.environ)
        env["HOME"] = tmp_home
        env["USERPROFILE"] = tmp_home  # v1.14.1 Windows divergence
        env.pop("CLAUDE_PLUGIN_ROOT", None)  # force __file__-relative resolution
        proc = subprocess.run(
            [sys.executable, "-c", prog, str(src), const],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(root),
            env=env,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or "").strip().splitlines()
            return f"<error: rc={proc.returncode} {tail[-1] if tail else ''}>"
        # The constant is written to stderr so a hook that emits its own
        # stdout envelope at import cannot contaminate the reading.
        return (proc.stderr or "").strip()
    except Exception as exc:  # pragma: no cover - defensive
        return f"<error: {exc}>"
    finally:
        shutil.rmtree(tmp_home, ignore_errors=True)


def harness_version(root: Path) -> str:
    """Ask the shell harness what version it resolved, via its --version flag."""
    script = root / "bin" / "publish-audit-combined.sh"
    if not script.is_file():
        return "<error: harness absent>"
    try:
        proc = subprocess.run(
            ["bash", str(script), "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(root),
        )
        if proc.returncode != 0:
            return f"<error: rc={proc.returncode}>"
        return (proc.stdout or "").strip()
    except Exception as exc:
        return f"<error: {exc}>"


def scan_static_literals(
    root: Path,
) -> tuple[list[tuple[str, int, str, str]], int]:
    """Every hand-maintained version literal still present, plus the denominator.

    Returns (hits, files_read). The count is not decoration: a scan that opened
    zero files reports zero literals and looks identical to a clean tree, which
    is the vacuous pass `report-the-denominator.md` exists to prevent.
    """
    hits: list[tuple[str, int, str, str]] = []
    files_read = 0
    targets: list[tuple[Path, re.Pattern[str]]] = []
    hooks_dir = root / "hooks"
    if hooks_dir.is_dir():
        for p in sorted(hooks_dir.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            targets.append((p, PY_LITERAL_RE))
    bin_dir = root / "bin"
    if bin_dir.is_dir():
        for p in sorted(bin_dir.glob("*.sh")):
            targets.append((p, SH_LITERAL_RE))
    for path, pattern in targets:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        files_read += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            m = pattern.match(line)
            if m:
                hits.append(
                    (str(path.relative_to(root)), lineno, m.group(1), m.group(2))
                )
    return hits, files_read


def check_lockstep(root: Path) -> tuple[list[str], dict[str, int]]:
    """Run all three arms against `root`.

    Returns (problems, counts). `counts` is the denominator every arm examined,
    so a clean verdict can state what it looked at instead of asserting a bare
    negative — `report-the-denominator.md`.
    """
    problems: list[str] = []
    counts = {"arm1_files": 0, "arm1_literals": 0, "arm2_carriers": 0, "arm3_mirrors": 0}

    expected = manifest_version(root)
    if not expected:
        return (
            ["manifest .claude-plugin/plugin.json unreadable or carries no version"],
            counts,
        )

    # ARM 1 - no hand-maintained literal may survive; if one does, it must match.
    literals, counts["arm1_files"] = scan_static_literals(root)
    counts["arm1_literals"] = len(literals)
    for rel, lineno, name, value in literals:
        if value != expected:
            problems.append(
                f"ARM1 drift: {rel}:{lineno} {name}={value!r} != manifest {expected!r}"
            )
        else:
            problems.append(
                f"ARM1 literal: {rel}:{lineno} {name} is a hand-maintained literal; "
                f"derive it from the manifest instead"
            )

    # ARM 2 - what each hook actually resolves at load.
    for rel, const in RUNTIME_CARRIERS:
        if not (root / rel).is_file():
            problems.append(f"ARM2 missing: {rel} not found")
            continue
        counts["arm2_carriers"] += 1
        got = resolve_runtime_version(root, rel, const)
        if got != expected:
            problems.append(
                f"ARM2 drift: {rel} resolved {const}={got!r} != manifest {expected!r}"
            )
    counts["arm2_carriers"] += 1
    got = harness_version(root)
    if got != expected:
        problems.append(
            f"ARM2 drift: bin/publish-audit-combined.sh --version -> {got!r} "
            f"!= manifest {expected!r}"
        )

    # ARM 3 - static manifests that cannot derive.
    for rel in MANIFEST_MIRRORS:
        path = root / rel
        if not path.is_file():
            problems.append(f"ARM3 missing: {rel} not found")
            continue
        counts["arm3_mirrors"] += 1
        try:
            with open(path, encoding="utf-8") as f:
                got = str(json.load(f).get("version", "")).strip()
        except Exception as exc:
            problems.append(f"ARM3 unreadable: {rel} ({exc})")
            continue
        if got != expected:
            problems.append(
                f"ARM3 drift: {rel} version={got!r} != manifest {expected!r}"
            )

    return problems, counts


# --------------------------------------------------------------------------
# Negative tests. Each builds a tree carrying exactly ONE injected defect and
# asserts the matching arm reports it. Proving the arms independently is what
# stops a future guard from silently disabling one of them.
# --------------------------------------------------------------------------

def _clone_tree(dest: Path) -> Path:
    """Copy the parts of the repo the guard reads. Returns the new root."""
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_ROOT / "hooks",
        dest / "hooks",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    shutil.copytree(REPO_ROOT / "bin", dest / "bin")
    (dest / ".claude-plugin").mkdir(exist_ok=True)
    shutil.copy2(
        REPO_ROOT / ".claude-plugin" / "plugin.json",
        dest / ".claude-plugin" / "plugin.json",
    )
    shutil.copy2(REPO_ROOT / "kimi.plugin.json", dest / "kimi.plugin.json")
    return dest


def _set_manifest(root: Path, version: str) -> None:
    path = root / ".claude-plugin" / "plugin.json"
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    data["version"] = version
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def self_test() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vc-roe-lockstep-selftest-"))
    try:
        # Control: an untouched clone must be CLEAN. Without this the three
        # reds below prove nothing, since a permanently-red guard also goes red.
        print("Control: untouched clone")
        root = _clone_tree(tmp / "control")
        problems, _ = check_lockstep(root)
        check(problems == [], f"control clone clean (got {problems})")

        # Negative 1 (ARM 3): bump the manifest alone. This is the operator's
        # stated proof condition. The derived carriers follow the manifest, so
        # the ONLY thing that can drift is the hand-maintained Kimi manifest.
        print("Negative 1: manifest bumped alone -> ARM3 must fire")
        root = _clone_tree(tmp / "neg1")
        _set_manifest(root, "9.99.99")
        problems, _ = check_lockstep(root)
        check(
            any(p.startswith("ARM3 drift") for p in problems),
            f"ARM3 reports kimi.plugin.json drift (got {problems})",
        )
        check(
            not any(p.startswith("ARM2 drift") for p in problems),
            "derived carriers followed the manifest (no ARM2 drift)",
        )

        # Negative 2 (ARM 2): break the resolver's source of truth. The hooks
        # fall soft to the sentinel, which every static scan would call clean.
        print("Negative 2: manifest deleted from a hook's bundle -> ARM2 must fire")
        root = _clone_tree(tmp / "neg2")
        real = root / ".claude-plugin" / "plugin.json"
        shutil.copy2(real, root / "plugin-manifest-backup.json")
        # Keep a readable manifest for the checker itself, but hide it from the
        # hooks by moving the directory the resolver walks to.
        (root / ".claude-plugin").rename(root / ".claude-plugin-hidden")
        (root / ".claude-plugin").mkdir()
        shutil.copy2(
            root / ".claude-plugin-hidden" / "plugin.json",
            root / ".claude-plugin" / "plugin.json.decoy",
        )
        problems, _ = check_lockstep(root)
        check(
            problems and problems[0].startswith("manifest"),
            f"unreadable manifest is reported, not silently passed (got {problems})",
        )
        # Now the sharper case: manifest readable by the checker, resolver blind.
        root = _clone_tree(tmp / "neg2b")
        (root / "hooks" / "session-start.py").write_text(
            (root / "hooks" / "session-start.py")
            .read_text(encoding="utf-8")
            .replace('".claude-plugin"', '".claude-plugin-nonexistent"', 1),
            encoding="utf-8",
        )
        problems, _ = check_lockstep(root)
        check(
            any("ARM2 drift" in p and "session-start.py" in p for p in problems),
            f"ARM2 catches a hook whose resolver cannot find the manifest "
            f"(got {problems})",
        )

        # Negative 3 (ARM 1): reintroduce a hand-maintained literal.
        print("Negative 3: literal constant reintroduced -> ARM1 must fire")
        root = _clone_tree(tmp / "neg3")
        target = root / "hooks" / "stop.py"
        text = target.read_text(encoding="utf-8")
        text = text.replace(
            "\nROUTINE_VERSION = ", '\nROUTINE_VERSION = "9.9.9"  # injected\n_UNUSED = ', 1
        )
        target.write_text(text, encoding="utf-8")
        problems, _ = check_lockstep(root)
        check(
            any(p.startswith("ARM1") and "stop.py" in p for p in problems),
            f"ARM1 catches a reintroduced literal (got {problems})",
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _regex_blind_spot_probe() -> None:
    """Assert ARM 1's detector on the shapes it must and must not match.

    Item 7 of `guards-and-negative-tests.md`: before acting on a detector's
    output, check the cases it cannot see, and encode that check into the
    detector. The must-NOT list is the load-bearing half — a detector that
    matched the derived form would make the correct code look like a defect.
    """
    must_match = [
        'ROUTINE_VERSION = "1.20.2"',
        "ROUTINE_VERSION='1.20.2'",
        'ROUTINE_VERSION="1.20.2"',
        '  ROUTINE_VERSION = "1.20.2"  # indented',
        'ROUTINE_VERSION: str = "1.20.2"',
        'KIMI_ADAPTER_VERSION = "1.20.2"',
    ]
    must_not_match = [
        "ROUTINE_VERSION = _resolve_plugin_version()",
        '        return "unknown"',
        '    return str(json.load(f).get("version", "")).strip() or "unknown"',
        "# ROUTINE_VERSION was 1.20.2 before I-22",
    ]
    check(
        all(PY_LITERAL_RE.match(s) for s in must_match),
        f"ARM1 detector matches all {len(must_match)} literal shapes",
    )
    check(
        not any(PY_LITERAL_RE.match(s) for s in must_not_match),
        f"ARM1 detector matches none of the {len(must_not_match)} derived/comment shapes",
    )
    sh_match = ['HARNESS_VERSION="1.20.2"', "HARNESS_VERSION=1.20.2"]
    sh_no_match = ['HARNESS_VERSION="$(sed -n ...)"', '[ -n "$HARNESS_VERSION" ] || true']
    check(
        all(SH_LITERAL_RE.match(s) for s in sh_match)
        and not any(SH_LITERAL_RE.match(s) for s in sh_no_match),
        "ARM1 shell detector matches literals only, not the derived form",
    )


def main() -> int:
    do_self_test = "--self-test" in sys.argv[1:]

    print(f"version-lockstep guard | repo root: {REPO_ROOT}")
    expected = manifest_version(REPO_ROOT)
    print(f"manifest .claude-plugin/plugin.json version: {expected!r}")
    print()

    print("Detector coverage (the shapes ARM 1 must and must not match)")
    _regex_blind_spot_probe()
    print()

    print("Guarding the real tree")
    problems, counts = check_lockstep(REPO_ROOT)
    # State the denominator in the same breath as the verdict. A guard that
    # examined nothing reports the same clean result as a guard that examined
    # everything (`report-the-denominator.md`, `guards-and-negative-tests.md`
    # item 4), so the counts below are part of the assertion, not decoration.
    print(
        f"  examined: ARM1 {counts['arm1_files']} source files "
        f"({counts['arm1_literals']} literal(s) found) | "
        f"ARM2 {counts['arm2_carriers']} runtime carriers | "
        f"ARM3 {counts['arm3_mirrors']} manifest mirror(s)"
    )
    check(counts["arm1_files"] > 0, "ARM1 actually read source files (non-vacuous)")
    check(
        counts["arm2_carriers"] == len(RUNTIME_CARRIERS) + 1,
        f"ARM2 probed every carrier ({len(RUNTIME_CARRIERS)} hooks + harness)",
    )
    check(
        counts["arm3_mirrors"] == len(MANIFEST_MIRRORS),
        f"ARM3 probed every manifest mirror ({len(MANIFEST_MIRRORS)})",
    )
    check(problems == [], "all version carriers agree with the manifest")
    for p in problems:
        print(f"       -> {p}")

    if do_self_test:
        print()
        print("Self-test (proving each arm can go red)")
        self_test()

    print()
    print(f"{PASS} pass / {FAIL} fail")
    if FAIL:
        print("FAILURES:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
