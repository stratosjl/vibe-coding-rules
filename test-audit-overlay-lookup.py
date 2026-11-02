#!/usr/bin/env python3
"""The publish gate finds the operator overlay outside the public tree (v1.31.0).

The gate's private DENY patterns live in a gitignored overlay. Until v1.31.0 it
read that overlay only from `bin/audit-patterns.local.sh` inside the public
clone, so each node needed a hand-made copy, and a pattern added to the
canonical copy reached no node until someone copied it. Measured 2026-11-02:
the combined audit passed with a private name in CHANGELOG.md because no
copy carried a pattern for it.

The gate now looks in `~/.claude/vc-roe-addons/audit-overlay/` first, then in
`bin/`. A node that has `~/.claude/vc-roe-addons` but no overlay in either
place cannot run fully armed, so the gate exits 3 (NOT-RUNNABLE), which blocks
a push. A clone with neither the link nor an overlay runs the public patterns
only, as before.

Every case runs in a throwaway HOME and a throwaway git repo carrying copies
of the gate scripts. The fixture pattern is made up and names nothing real.

Pure stdlib. Usage: python test-audit-overlay-lookup.py ; exit code is the verdict.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
FIXTURE = "zqx" + "-fixture-companion"
OTHER = "zqx" + "-only-in-bin"
OVERLAY_NAME = "audit-patterns.local.sh"
BASELINE_NAME = "audit-history-baseline.local.txt"


def overlay_text(*patterns: str) -> str:
    body = "\n".join(f"  '{p}'" for p in patterns)
    return f"DENY_PATTERNS+=(\n{body}\n)\n"


def make_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "bin").mkdir(parents=True)
    for name in ("audit-patterns.sh", "publish-audit.sh", "publish-audit-state.sh"):
        shutil.copy2(REPO_ROOT / "bin" / name, repo / "bin" / name)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / "README.md").write_text("clean\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    return repo


def env_for(home: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    return env


def run(repo: Path, home: Path, script: str) -> tuple[int, str]:
    p = subprocess.run(["bash", f"bin/{script}"], cwd=repo, env=env_for(home),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout + p.stderr


def plant(repo: Path, text: str) -> None:
    (repo / "NOTES.md").write_text(text + "\n", encoding="utf-8", newline="\n")
    subprocess.run(["git", "add", "NOTES.md"], cwd=repo, check=True)


def resolved(repo: Path, home: Path) -> dict[str, str]:
    """Source the patterns file and read back the variables it publishes."""
    cmd = ('. bin/audit-patterns.sh >/dev/null 2>&1; '
           'printf "%s\\n%s\\n%s\\n" "$AUDIT_OVERLAY_STATE" "$AUDIT_OVERLAY_FILE" '
           '"$AUDIT_BASELINE_LOCAL_FILE"')
    p = subprocess.run(["bash", "-c", cmd], cwd=repo, env=env_for(home),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    state, ovl, base = (p.stdout.splitlines() + ["", "", ""])[:3]
    return {"state": state, "overlay": ovl, "baseline": base}


def addons_dir(home: Path) -> Path:
    return home / ".claude" / "vc-roe-addons" / "audit-overlay"


CASES = []


def case(fn):
    CASES.append(fn)
    return fn


@case
def third_party_clone_runs_public_only(home: Path, repo: Path) -> list[str]:
    rc, out = run(repo, home, "publish-audit.sh")
    fails = []
    if rc != 0:
        fails.append(f"no link, no overlay: expected rc 0, got {rc}")
    if "public scaffold patterns only" not in out:
        fails.append("no link, no overlay: output does not say public patterns only")
    if resolved(repo, home)["state"] != "absent":
        fails.append("no link, no overlay: AUDIT_OVERLAY_STATE should be absent")
    return fails


@case
def addons_overlay_is_read_and_blocks(home: Path, repo: Path) -> list[str]:
    d = addons_dir(home)
    d.mkdir(parents=True)
    (d / OVERLAY_NAME).write_text(overlay_text(FIXTURE), encoding="utf-8", newline=chr(10))
    fails = []
    rc, out = run(repo, home, "publish-audit.sh")
    if rc != 0:
        fails.append(f"addons overlay, clean tree: expected rc 0, got {rc}")
    if str(d.name) not in out or "vc-roe-addons" not in out:
        fails.append("addons overlay: output does not name the path it read")
    plant(repo, f"see {FIXTURE} here")
    rc, out = run(repo, home, "publish-audit.sh")
    if rc != 1:
        fails.append(f"addons overlay, planted name: expected rc 1, got {rc}")
    r = resolved(repo, home)
    if Path(r["baseline"]).parent.name != "audit-overlay" or BASELINE_NAME not in r["baseline"]:
        fails.append(f"addons overlay: baseline should resolve beside it, got {r['baseline']!r}")
    return fails


@case
def link_without_overlay_is_not_runnable(home: Path, repo: Path) -> list[str]:
    (home / ".claude" / "vc-roe-addons").mkdir(parents=True)
    fails = []
    rc, out = run(repo, home, "publish-audit.sh")
    if rc != 3:
        fails.append(f"link, no overlay: publish-audit.sh expected rc 3, got {rc}")
    if "NOT-RUNNABLE" not in out:
        fails.append("link, no overlay: publish-audit.sh did not say NOT-RUNNABLE")
    rc, out = run(repo, home, "publish-audit-state.sh")
    if rc != 3:
        fails.append(f"link, no overlay: publish-audit-state.sh expected rc 3, got {rc}")
    if "NOT-RUNNABLE" not in out or "vc-roe-addons" not in out:
        fails.append("link, no overlay: publish-audit-state.sh did not say NOT-RUNNABLE naming the addons path")
    return fails


def make_dangling_link(link: Path, root: Path) -> bool:
    """A link whose target is gone: a symlink on POSIX, a junction on Windows
    (the form the operator's Windows node uses, and creatable without admin)."""
    target = root / "gone"
    target.mkdir()
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                               capture_output=True, text=True)
            if r.returncode != 0:
                return False
        else:
            os.symlink(target, link, target_is_directory=True)
    except OSError:
        return False
    shutil.rmtree(target)
    return not link.exists()


@case
def dangling_link_is_still_an_operator_node(home: Path, repo: Path) -> list[str]:
    if not make_dangling_link(home / ".claude" / "vc-roe-addons", home.parent):
        return ["dangling link: could not create one here, so this case did not run"]
    r = resolved(repo, home)
    if r["state"] != "missing":
        return [f"dangling link, no overlay: state should be missing, got {r['state']!r} "
                "(a deleted private clone must not turn into a public-only pass)"]
    rc, _ = run(repo, home, "publish-audit.sh")
    return [] if rc == 3 else [f"dangling link, no overlay: expected rc 3, got {rc}"]


@case
def bin_copy_is_the_fallback(home: Path, repo: Path) -> list[str]:
    (home / ".claude" / "vc-roe-addons").mkdir(parents=True)
    (repo / "bin" / OVERLAY_NAME).write_text(overlay_text(FIXTURE), encoding="utf-8", newline=chr(10))
    plant(repo, f"see {FIXTURE} here")
    rc, out = run(repo, home, "publish-audit.sh")
    return [] if rc == 1 else [f"link, overlay only in bin/: expected rc 1, got {rc}"]


@case
def addons_wins_over_bin(home: Path, repo: Path) -> list[str]:
    d = addons_dir(home)
    d.mkdir(parents=True)
    (d / OVERLAY_NAME).write_text(overlay_text(FIXTURE), encoding="utf-8", newline=chr(10))
    (repo / "bin" / OVERLAY_NAME).write_text(overlay_text(OTHER), encoding="utf-8", newline=chr(10))
    fails = []
    plant(repo, f"see {OTHER} here")
    rc, _ = run(repo, home, "publish-audit.sh")
    if rc != 0:
        fails.append(f"both present: a pattern only in bin/ was applied (rc {rc}); addons should win")
    plant(repo, f"see {FIXTURE} here")
    rc, _ = run(repo, home, "publish-audit.sh")
    if rc != 1:
        fails.append(f"both present: the addons pattern was not applied (rc {rc})")
    return fails


def main() -> int:
    passed = failed = 0
    for fn in CASES:
        with tempfile.TemporaryDirectory(prefix="vc-roe-overlay-lookup-") as d:
            root = Path(d)
            home = root / "home"
            home.mkdir()
            repo = make_repo(root)
            fails = fn(home, repo)
        print(("FAIL " if fails else "PASS ") + fn.__name__)
        for f in fails:
            print("  " + f)
        failed += bool(fails)
        passed += not fails
    print(f"{passed} pass / {failed} fail")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
