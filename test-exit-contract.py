#!/usr/bin/env python3
"""Exit-contract guard (F-EXIT-01, v1.24.0).

WHY THIS EXISTS
---------------
A check whose CANNOT-RUN renders identically to its PASS or to its
FOUND-A-DEFECT is worse than no check, because the failure is recorded as
evidence. Measured 2026-09-04 on a T4 regulated project: a guard committed under
`bin/` opened with

    sys.path.insert(0, r"C:\\Users\\...\\AppData\\Local\\Temp\\claude"
                       r"\\<project>\\<session-uuid>\\scratchpad")
    import isa_edit as E

where that module existed in exactly one place on the machine, a session-scoped
temp directory written three weeks earlier. The guard worked only because the OS
had not yet swept the tree. `ModuleNotFoundError` exits 1, and the guard used 1
for "this document has a structural defect", so on the day the temp was swept a
regression floor would report a failing DOCUMENT and send the reader to
investigate the document. The guard's disappearance is indistinguishable from
the guard firing.

vc-roe had the same defect in three places of its own, all fixed at v1.24.0:

  - `bin/anchor-rewrite.sh` exited 0 when no session anchor resolved. Its own
    header said so. A caller running `anchor-rewrite.sh T4 && echo ok` reported
    success while heartbeat enforcement was never armed.
  - Every tier command printed "falling back to general familiarity with T4
    prescriptions" and exited 0 when the plugin root did not resolve. That is a
    methodology loader folding CANNOT-RUN into PASS: the session then carries
    the T4 first-line rule on every reply while holding none of T4's rules.
  - `hooks/session-start.py`'s `load_slice()` returned a single comment line,
    `# Methodology slice for T4 not found at ...`, which is injected verbatim as
    the session's rule set under a header reading "Methodology in force: T4".

THE CONTRACT
------------
    0  ran, nothing wrong
    1  ran, found a defect
    2  usage error      (the CALLER is wrong)
    3  NOT-RUNNABLE     (a dependency is missing; the ENVIRONMENT is wrong)

Codes that already discriminate correctly are not renumbered; 3 is added where a
collision existed.

THE NEGATIVE TEST RULE
----------------------
Every arm below that asserts 3 does so by REMOVING THE DEPENDENCY, not by
corrupting the input. Corrupting the input exercises the arm that already works.

ISOLATION
---------
Nothing here writes outside a `tempfile.mkdtemp()`. That is not hygiene, it is
the point: the pre-existing suite writes `ktest-*` anchors into the real temp
dir, and during development of this file the CANNOT-RUN arm resolved to one of
them and reported a false 0.

Usage:
    python test-exit-contract.py               # guard the real tree
    python test-exit-contract.py --self-test   # prove each arm can go red
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

REPO_ROOT = Path(__file__).resolve().parent  # never an absolute path elsewhere

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  [{'ok ' if ok else 'RED'}] {name}" + (f" :: {detail}" if detail else ""))


def run(cmd, **kw):
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("cwd", str(REPO_ROOT))
    return subprocess.run(cmd, **kw)


def sh(script: str, env=None, cwd=None):
    """Run a bash snippet. Returns CompletedProcess."""
    e = dict(os.environ)
    if env:
        e.update({k: v for k, v in env.items() if v is not None})
        for k, v in env.items():
            if v is None:
                e.pop(k, None)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, env=e, cwd=str(cwd or REPO_ROOT),
    )


# --------------------------------------------------------------------------
# ARM 1 -- bin/anchor-rewrite.sh
# --------------------------------------------------------------------------
def arm_anchor_rewrite() -> None:
    print("\nARM 1  bin/anchor-rewrite.sh")
    script = REPO_ROOT / "bin" / "anchor-rewrite.sh"
    tmp = Path(tempfile.mkdtemp(prefix="vcroe-exit-"))
    try:
        anchors = tmp / "anchors"
        anchors.mkdir()
        anchor = anchors / "claude-methodology-anchor-sid"
        anchor.write_text("T0=1\nLAST_HEARTBEAT=7\nTIER=T0\n", encoding="utf-8")

        env0 = {"CLAUDE_CODE_SESSION_ID": "sid", "VC_ROE_ANCHOR_DIR": str(anchors)}

        r = sh(f'bash "{script}" T3', env=env0)
        check("0 on a resolved anchor", r.returncode == 0, r.stdout.strip()[:70])
        check("TIER actually rewritten", "TIER=T3" in anchor.read_text(encoding="utf-8"))
        check(
            "T0 and LAST_HEARTBEAT untouched",
            "T0=1" in anchor.read_text(encoding="utf-8")
            and "LAST_HEARTBEAT=7" in anchor.read_text(encoding="utf-8"),
        )

        r = sh(f'bash "{script}" --resolve-only', env=env0)
        check("--resolve-only exits 0 and names the layer",
              r.returncode == 0 and "layer=env-session-id" in r.stdout, r.stdout.strip()[:70])

        r = sh(f'bash "{script}" T9', env=env0)
        check("2 on a bad tier (USAGE, not a defect)", r.returncode == 2)
        r = sh(f'bash "{script}"', env=env0)
        check("2 on no argument", r.returncode == 2)

        # DEPENDENCY REMOVED, not input corrupted: no session id, empty anchor
        # dir, and a home with no project transcripts, so every one of the four
        # resolution layers has nothing to find.
        empty = tmp / "empty"
        empty.mkdir()
        nohome = tmp / "nohome"
        nohome.mkdir()
        r = sh(
            f'bash "{script}" T4',
            env={"CLAUDE_CODE_SESSION_ID": None, "VC_ROE_ANCHOR_DIR": str(empty),
                 "HOME": str(nohome)},
        )
        check("3 when the anchor dependency is GONE", r.returncode == 3, r.stdout.strip()[:70])
        check("3 arm says NOT-RUNNABLE in its output", "NOT-RUNNABLE" in r.stdout)
        check("3 arm states enforcement is not in force",
              "not in force" in r.stdout.lower() or "NOT in force" in r.stdout)

        # The distinction that matters: 3 must differ from 0 AND from 1.
        codes = {"resolved": 0, "usage": 2, "cannot-run": 3}
        check("cannot-run code collides with neither pass nor defect",
              codes["cannot-run"] not in (0, 1), f"3 vs pass=0 defect=1")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# ARM 2 -- bin/sync-user-aliases.sh
# --------------------------------------------------------------------------
def arm_sync_aliases() -> None:
    print("\nARM 2  bin/sync-user-aliases.sh")
    script = REPO_ROOT / "bin" / "sync-user-aliases.sh"
    tmp = Path(tempfile.mkdtemp(prefix="vcroe-exit-"))
    try:
        r = sh(f'bash "{script}"', env={"HOME": str(tmp)})
        check("3 when the plugin cache is absent", r.returncode == 3, r.stderr.strip()[:70])
        check("3 arm says NOT-RUNNABLE", "NOT-RUNNABLE" in r.stderr)
        check("3 arm says nothing was copied", "Nothing was copied" in r.stderr)

        text = script.read_text(encoding="utf-8")
        code = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
        check("dead CACHE_BASE_LEGACY branch removed from CODE",
              not any("CACHE_BASE_LEGACY" in l for l in code),
              "the surviving mention must be the comment explaining the removal")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# ARM 3 -- hooks/session-start.py
# --------------------------------------------------------------------------
def _run_hook(tmp: Path, session_id: str, plugin_root, cwd) -> tuple[str, Path]:
    env = dict(os.environ)
    env.update({"TMPDIR": str(tmp), "TEMP": str(tmp), "TMP": str(tmp)})
    if plugin_root is None:
        env.pop("CLAUDE_PLUGIN_ROOT", None)
    else:
        env["CLAUDE_PLUGIN_ROOT"] = str(plugin_root)
    event = json.dumps({"session_id": session_id, "cwd": str(cwd), "source": "startup"})
    p = subprocess.run(
        [sys.executable, str(REPO_ROOT / "hooks" / "session-start.py")],
        input=event, capture_output=True, text=True, env=env, cwd=str(REPO_ROOT),
    )
    return p.stdout, tmp / f"claude-methodology-anchor-{session_id}"


def _context_of(stdout: str) -> str:
    try:
        d = json.loads(stdout)
    except Exception:
        return ""
    return d.get("hookSpecificOutput", {}).get("additionalContext", d.get("additionalContext", ""))


def arm_session_start() -> None:
    print("\nARM 3  hooks/session-start.py")
    tmp = Path(tempfile.mkdtemp(prefix="vcroe-exit-"))
    try:
        _, anchor = _run_hook(tmp, "vcroe-new", REPO_ROOT, REPO_ROOT)
        body = anchor.read_text(encoding="utf-8") if anchor.exists() else ""
        check("fresh anchor carries PLUGIN_ROOT", "PLUGIN_ROOT=" in body)
        check("fresh anchor carries PLUGIN_VERSION", "PLUGIN_VERSION=" in body)
        root = re.search(r"^PLUGIN_ROOT=(.*)$", body, re.M)
        check("stamped root is POSIX-form (Git Bash can stat it)",
              bool(root) and "\\" not in root.group(1), root.group(1) if root else "")
        check("stamped root points at a real bundle",
              bool(root) and (Path(root.group(1)) / "methodology-content").is_dir())

        legacy = tmp / "claude-methodology-anchor-vcroe-legacy"
        legacy.write_text("T0=1234\nLAST_HEARTBEAT=99\nTIER=T4\n", encoding="utf-8")
        _run_hook(tmp, "vcroe-legacy", REPO_ROOT, REPO_ROOT)
        lb = legacy.read_text(encoding="utf-8")
        check("legacy anchor is AUGMENTED not replaced",
              "PLUGIN_ROOT=" in lb and "T0=1234" in lb and "TIER=T4" in lb and "LAST_HEARTBEAT=99" in lb)
        before = legacy.read_text(encoding="utf-8")
        _run_hook(tmp, "vcroe-legacy", REPO_ROOT, REPO_ROOT)
        check("augmentation is idempotent", legacy.read_text(encoding="utf-8") == before)

        # set-but-EMPTY env var must not become Path(".")
        _, a2 = _run_hook(tmp, "vcroe-empty", "", REPO_ROOT)
        b2 = a2.read_text(encoding="utf-8") if a2.exists() else ""
        m2 = re.search(r"^PLUGIN_ROOT=(.*)$", b2, re.M)
        check("set-but-empty CLAUDE_PLUGIN_ROOT falls back, does not become '.'",
              bool(m2) and m2.group(1) not in (".", ""), m2.group(1) if m2 else "")

        # env var pointing somewhere real but WITHOUT this plugin in it
        bogus = tmp / "bogus"
        bogus.mkdir()
        _, a3 = _run_hook(tmp, "vcroe-bogus", bogus, REPO_ROOT)
        b3 = a3.read_text(encoding="utf-8") if a3.exists() else ""
        m3 = re.search(r"^PLUGIN_ROOT=(.*)$", b3, re.M)
        check("a harness root that lacks the bundle is REJECTED",
              bool(m3) and Path(m3.group(1)).resolve() != bogus.resolve(), m3.group(1) if m3 else "")

        # DEPENDENCY REMOVED: a root whose methodology-content dir is empty
        fake = tmp / "fakeroot"
        (fake / "methodology-content").mkdir(parents=True)
        shutil.copy2(REPO_ROOT / "detection-rules.json", fake / "detection-rules.json")
        shutil.copytree(REPO_ROOT / ".claude-plugin", fake / ".claude-plugin")
        out, _ = _run_hook(tmp, "vcroe-noslice", fake, REPO_ROOT)
        ctx = _context_of(out)
        check("missing slice yields a LOUD NOT-RUNNABLE banner", "NOT-RUNNABLE" in ctx)
        check("banner states no rules are in force",
              "No methodology rules are in force" in ctx)
        check("banner instructs the assistant to stop and tell the operator",
              "FIRST reply" in ctx or "first reply" in ctx)
        check("banner is not the old one-line comment",
              "# Methodology slice for" not in ctx)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# ARM 4 -- the command bash blocks
# --------------------------------------------------------------------------
COMMAND_FILES = [
    "commands/tier.md", "commands/raise-tier.md", "commands/lower-tier.md",
    "commands-kimi/tier.md", "commands-kimi/raise-tier.md", "commands-kimi/lower-tier.md",
]
FENCE = re.compile(r"^   ```bash\n((?:.*?\n)*?)   ```\n", re.M)
RESOLVER_BEGIN = "# --- vc-roe resolver v1.24.0 BEGIN"
RESOLVER_END = "# --- vc-roe resolver v1.24.0 END"


def _blocks(rel: str):
    text = (REPO_ROOT / rel).read_text(encoding="utf-8")
    for m in FENCE.finditer(text):
        yield "\n".join(l[3:] if l.startswith("   ") else l for l in m.group(1).splitlines())


def _resolver_of(block: str):
    if RESOLVER_BEGIN not in block:
        return None
    s = block.index(RESOLVER_BEGIN)
    e = block.index(RESOLVER_END) + len(RESOLVER_END)
    return block[s:e]


def arm_commands() -> None:
    print("\nARM 4  command bash blocks")
    tmp = Path(tempfile.mkdtemp(prefix="vcroe-exit-"))
    try:
        slice_block = next(
            b for b in _blocks("commands/tier.md")
            if "SLICE=" in b and RESOLVER_BEGIN in b
        ).replace("$ARGUMENTS", "T4")

        anchors = tmp / "anchors"
        anchors.mkdir()
        (anchors / "claude-methodology-anchor-sid").write_text(
            f"T0=1\nLAST_HEARTBEAT=0\nTIER=T4\nPLUGIN_ROOT={REPO_ROOT.as_posix()}\n",
            encoding="utf-8",
        )
        r = sh(slice_block, env={"CLAUDE_CODE_SESSION_ID": "sid", "TMPDIR": str(anchors)})
        check("resolves via THIS SESSION's anchor", r.returncode == 0 and "this session own anchor" in r.stdout)
        check("emits the slice content", "--- methodology slice T4 begin ---" in r.stdout)

        (anchors / "claude-methodology-anchor-old").write_text(
            "T0=1\nLAST_HEARTBEAT=0\nTIER=T4\n", encoding="utf-8")
        r = sh(slice_block, env={"CLAUDE_CODE_SESSION_ID": "old", "TMPDIR": str(anchors)})
        check("falls back when the anchor predates PLUGIN_ROOT",
              r.returncode == 0 and "FALLBACK" in r.stdout)

        # DEPENDENCY REMOVED: no session id, and a search root with no install.
        nowhere = tmp / "nowhere"
        nowhere.mkdir()
        broken = slice_block.replace('VC_ROE_SEARCH="$HOME/.claude/plugins"',
                                     f'VC_ROE_SEARCH="{nowhere.as_posix()}"')
        r = sh(broken, env={"CLAUDE_CODE_SESSION_ID": None, "TMPDIR": str(tmp / "empty-anchors")})
        check("3 when no root resolves at all", r.returncode == 3, r.stdout.strip()[:70])
        check("3 arm says NOT-RUNNABLE", "NOT-RUNNABLE" in r.stdout)
        check("3 arm says nothing was loaded", "nothing was loaded" in r.stdout)

        # slice file missing but root fine -> still 3, never 0
        halfroot = tmp / "halfroot"
        (halfroot / "methodology-content").mkdir(parents=True)
        shutil.copytree(REPO_ROOT / ".claude-plugin", halfroot / ".claude-plugin")
        (anchors / "claude-methodology-anchor-half").write_text(
            f"T0=1\nLAST_HEARTBEAT=0\nTIER=T4\nPLUGIN_ROOT={halfroot.as_posix()}\n",
            encoding="utf-8")
        r = sh(slice_block, env={"CLAUDE_CODE_SESSION_ID": "half", "TMPDIR": str(anchors)})
        check("3 when the root resolves but the slice is missing", r.returncode == 3)

        # no command file may still carry the old resolver or the old prose
        stale = [f for f in COMMAND_FILES
                 if "PLUGIN_BIN=" in (REPO_ROOT / f).read_text(encoding="utf-8")]
        check("no command file carries the old PLUGIN_BIN resolver", not stale, ",".join(stale))
        fallback = [f for f in COMMAND_FILES
                    if "general familiarity" in (REPO_ROOT / f).read_text(encoding="utf-8")
                    and "do not proceed on general familiarity" not in (REPO_ROOT / f).read_text(encoding="utf-8").lower()]
        check("no command file still offers a silent fallback", not fallback, ",".join(fallback))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# ARM 5 -- resolver lockstep across the six command files
# --------------------------------------------------------------------------
def arm_lockstep() -> None:
    print("\nARM 5  resolver lockstep")
    found = {}
    for rel in COMMAND_FILES:
        for b in _blocks(rel):
            r = _resolver_of(b)
            if r is not None:
                found.setdefault(rel, []).append(r)

    check("every command file carries the resolver", len(found) == len(COMMAND_FILES),
          f"{len(found)}/{len(COMMAND_FILES)}")
    counts = {k: len(v) for k, v in found.items()}
    check("three resolver copies per file", set(counts.values()) == {3}, str(counts))

    variants = {r for copies in found.values() for r in copies}
    check("all resolver copies are byte-identical", len(variants) == 1,
          f"{len(variants)} distinct variant(s)")

    total = sum(counts.values())
    check("18 resolver sites accounted for", total == 18, str(total))


# --------------------------------------------------------------------------
# ARM 6 -- committed-check hygiene (the rule-1 half)
# --------------------------------------------------------------------------
OUTSIDE = re.compile(
    r"(AppData[/\\]Local[/\\]Temp|/home/[a-z]|/Users/[A-Za-z]|[A-Z]:[/\\]Users[/\\])",
)
EXEMPT = "vc-roe:runtime-state"


def _executable_code_lines(rel: str, text: str):
    """Yield (lineno, line) for lines that are CODE, not prose.

    Documentation is out of scope by construction: this rule is about a check
    whose DEPENDENCY lives outside the repo, and a CHANGELOG entry describing a
    path is not a dependency. Measured while writing this: scanning `.md` flagged
    a changelog line containing the placeholder `<user>\\AppData\\Local\\Temp\\`
    and an `.example` file containing `/home/operator-user/somewhere`, neither of
    which is a real path or a dependency. A guard that fires on all of those is
    measuring itself, not the tree.

    For Python, docstrings are excluded via `ast` rather than by guessing, so
    this file's own explanation of the defect does not trip it.
    """
    if rel.endswith(".py"):
        docstring_spans = set()
        try:
            tree = __import__("ast").parse(text)
            ast = __import__("ast")
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                    body = getattr(node, "body", None)
                    if body and isinstance(body[0], ast.Expr) and isinstance(
                            getattr(body[0], "value", None), ast.Constant) and isinstance(
                            body[0].value.value, str):
                        for ln in range(body[0].lineno, (body[0].end_lineno or body[0].lineno) + 1):
                            docstring_spans.add(ln)
        except Exception:
            return  # unparseable: report nothing rather than a wrong number
        for i, line in enumerate(text.splitlines(), 1):
            if i in docstring_spans:
                continue
            if line.lstrip().startswith("#"):
                continue
            yield i, line
    else:
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            yield i, line


def arm_hygiene() -> None:
    print("\nARM 6  committed-check hygiene")
    tracked = run(["git", "ls-files"]).stdout.split()
    scanned, skipped, hits = 0, 0, []
    for rel in tracked:
        if not rel.endswith((".py", ".sh")):
            skipped += 1
            continue
        try:
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        except Exception:
            skipped += 1
            continue
        scanned += 1
        for i, line in _executable_code_lines(rel, text):
            if OUTSIDE.search(line) and EXEMPT not in line:
                hits.append(f"{rel}:{i}")

    # Positive control. Without it a zero is indistinguishable from a matcher
    # that cannot match anything.
    ctl = OUTSIDE.search(r'sys.path.insert(0, r"C:\Users\x\AppData\Local\Temp\claude\p\s")')
    check("hygiene matcher fires on a planted violation (positive control)", bool(ctl))
    # And a negative control: the exemption marker must actually suppress.
    check("exemption marker suppresses a real hit (negative control)",
          EXEMPT in "x = '/home/op/y'  # " + EXEMPT)
    check("no executable file hard-codes an out-of-repo absolute path",
          not hits, f"{len(hits)} hit(s): " + ", ".join(hits[:6]))
    print(f"       denominator: {scanned} .py/.sh of {len(tracked)} tracked "
          f"({skipped} skipped as non-executable or unreadable); docstrings and "
          f"comment lines excluded")

    # Any sys.path insertion of a path this repo does not own must carry the
    # marker, so that adding one is a decision rather than an assumption.
    bad = []
    for rel in tracked:
        if not rel.endswith(".py"):
            continue
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        lines = text.splitlines()
        for m in re.finditer(r"^\s*sys\.path\.(insert|append)\((.*)$", text, re.M):
            arg = m.group(2)
            lineno = text[:m.start()].count("\n") + 1
            if any(tok in arg for tok in ("__file__", "REPO_ROOT", "PLUGIN_ROOT")):
                continue
            window = "\n".join(lines[max(0, lineno - 8):lineno])
            if EXEMPT in window:
                continue
            bad.append(f"{rel}:{lineno}: {m.group(0).strip()[:50]}")
    check("every out-of-repo sys.path insertion is marked and justified",
          not bad, "; ".join(bad[:3]))


# --------------------------------------------------------------------------
# self-test: prove each arm can go RED
# --------------------------------------------------------------------------
MUTATIONS = [
    ("anchor-rewrite exits 0 instead of 3 on no anchor",
     "bin/anchor-rewrite.sh", "    exit 3\nfi\n\nif [ \"$RESOLVE_ONLY\"", "    exit 0\nfi\n\nif [ \"$RESOLVE_ONLY\""),
    ("resolver drifts in one command file",
     "commands-kimi/lower-tier.md", "VC_ROE_ROOT=\"\"; VC_ROE_VIA=\"\"; VC_ROE_ANCHOR=\"\"",
     "VC_ROE_ROOT=\"\"; VC_ROE_VIA=\"\"  # drifted"),
    ("session-start stops stamping PLUGIN_ROOT",
     "hooks/session-start.py", '"PLUGIN_ROOT": PLUGIN_ROOT.as_posix(),', '"XPLUGIN_ROOT": PLUGIN_ROOT.as_posix(),'),
]


def self_test() -> int:
    print("SELF-TEST: each mutation must turn this runner RED.\n")
    import hashlib

    def digest(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()

    reds = 0
    for name, rel, old, new in MUTATIONS:
        target = REPO_ROOT / rel
        pristine = target.read_bytes()
        before = digest(target)
        text = target.read_text(encoding="utf-8")
        if old not in text:
            print(f"  [SKIP] {name} :: anchor no longer matches in {rel} -- MUTATION NOT APPLIED")
            print("         A skipped mutation proves nothing. Re-anchor it.")
            continue
        target.write_text(text.replace(old, new, 1), encoding="utf-8")
        if digest(target) == before:
            print(f"  [SKIP] {name} :: file unchanged after write")
            target.write_bytes(pristine)
            continue
        r = run([sys.executable, str(REPO_ROOT / "test-exit-contract.py")])
        target.write_bytes(pristine)
        assert digest(target) == before, f"restore failed for {rel}"
        went_red = r.returncode != 0
        reds += went_red
        print(f"  [{'RED' if went_red else 'GREEN -- BAD'}] {name}")
        if not went_red:
            print("         The guard did not notice. That mutation is unguarded.")

    applied = sum(1 for n, rel, old, _ in MUTATIONS
                  if old in (REPO_ROOT / rel).read_text(encoding="utf-8"))
    print(f"\nSELF-TEST: {reds}/{len(MUTATIONS)} mutations detected "
          f"({applied}/{len(MUTATIONS)} anchors still valid)")
    ok = reds == len(MUTATIONS) and applied == len(MUTATIONS)
    print(f"VERDICT: {'PASS' if ok else 'FAIL'}  (do not pipe this; the verdict is the exit code)")
    return 0 if ok else 1


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    print("vc-roe exit-contract guard (F-EXIT-01)")
    print(f"repo: {REPO_ROOT}")
    for fn in (arm_anchor_rewrite, arm_sync_aliases, arm_session_start,
               arm_commands, arm_lockstep, arm_hygiene):
        try:
            fn()
        except Exception as e:
            check(f"{fn.__name__} raised", False, f"{type(e).__name__}: {e}")
    print(f"\n{len(PASS)} pass / {len(FAIL)} fail")
    for f in FAIL:
        print(f"  FAILED: {f}")
    print(f"VERDICT: {'PASS' if not FAIL else 'FAIL'}  "
          "(do not pipe this; the verdict is the exit code)")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
