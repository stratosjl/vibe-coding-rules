#!/usr/bin/env python3
"""Tests for the split SessionStart delivery (since v1.27.0).

Claude Code saves a hook output over about 10,000 characters to a file and puts
only a 2 KB preview into the model's context, per hook. hooks.json therefore
runs session-start.py once with --split (everything except the slice) and once
per slice part with --slice-part K. These tests run the hook as real processes
and check:

  1. every public slice fits in SLICE_PARTS_MAX parts, each part's whole
     additionalContext under SLICE_PART_OUTPUT_CEILING characters and bytes;
  2. the parts, headers removed, rejoin to the slice byte for byte;
  3. the --split block carries no slice text, names the part count and the
     slice path, and stays under the ceiling;
  4. the default (no argument) output still carries the whole slice, which the
     Kimi adapter and older hooks.json files rely on;
  5. part hooks write nothing (no tier floor, claim or anchor), with the main
     entry run the same way as the positive control;
  6. hooks.json wires exactly one --split entry and parts 1..SLICE_PARTS_MAX,
     and no entry without a flag (which would deliver the slice twice);
  7. split_slice keeps every chunk within budget and rejoins exactly on
     synthetic input, including an over-long line and text with no blank line;
  8. a slice needing more parts than are wired makes the --split block say so.

A negative control proves the size check can fail: a 12,000-character string
must be rejected by the same predicate.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent
SESSION_START = PLUGIN_ROOT / "hooks" / "session-start.py"
CONTENT_DIR = PLUGIN_ROOT / "methodology-content"
PUBLIC_TIERS = ["T0", "T1", "T2", "T3", "T4"]

PASS = 0
FAIL = 0
FAILURES: list[str] = []


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS: {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  FAIL: {label}")


def load_module():
    spec = importlib.util.spec_from_file_location("vcroe_session_start", SESSION_START)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = load_module()


def fits(text: str) -> bool:
    ceiling = M.SLICE_PART_OUTPUT_CEILING
    return len(text) < ceiling and len(text.encode("utf-8")) < ceiling


def run(home: Path, cwd: Path, args: list[str], tier: str | None = None,
        session_id: str = "test-split-session") -> str | None:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USERPROFILE"] = str(home)
    env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
    env.pop("CLAUDE_TIER", None)
    if tier:
        env["CLAUDE_TIER"] = tier
    proc = subprocess.run(
        [sys.executable, str(SESSION_START), *args],
        input=json.dumps({"session_id": session_id, "cwd": str(cwd)}),
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=30,
    )
    if proc.returncode != 0:
        return f"__RC_{proc.returncode}__"
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]


def strip_header(part: str) -> str:
    head, _, body = part.partition("\n\n")
    assert head.startswith("## Methodology slice "), head[:80]
    return body


def anchor_for(session_id: str) -> Path:
    return M.ANCHOR_DIR / f"{M.ANCHOR_PREFIX}{session_id}"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="vcroe-split-test-"))
    home = tmp / "home"
    (home / ".claude").mkdir(parents=True)
    cwd = tmp / "proj"
    cwd.mkdir()

    print("Negative control: the size predicate rejects an oversized output")
    check(not fits("x" * 12000), "12,000 characters is rejected")
    check(fits("x" * 9000), "9,000 characters is accepted")

    print("1-4. Every public tier, as real processes")
    for tier in PUBLIC_TIERS:
        src = (CONTENT_DIR / f"{tier}.md").read_text(encoding="utf-8")
        parts = [run(home, cwd, ["--slice-part", str(k)], tier)
                 for k in range(1, M.SLICE_PARTS_MAX + 2)]
        got = [p for p in parts if p is not None]
        check(parts[M.SLICE_PARTS_MAX] is None,
              f"{tier}: part {M.SLICE_PARTS_MAX + 1} prints nothing")
        check(1 <= len(got) <= M.SLICE_PARTS_MAX,
              f"{tier}: {len(got)} part(s), within {M.SLICE_PARTS_MAX}")
        check(all(fits(p) for p in got),
              f"{tier}: each part under {M.SLICE_PART_OUTPUT_CEILING} "
              f"(sizes {[len(p) for p in got]})")
        check("".join(strip_header(p) for p in got) == src,
              f"{tier}: parts rejoin to the slice exactly")
        check(all(f"part {k} of {len(got)}" in p.split("\n", 1)[0]
                  for k, p in enumerate(got, 1)),
              f"{tier}: each part is labelled k of {len(got)}")
        split = run(home, cwd, ["--split"], tier)
        check(split is not None and fits(split), f"{tier}: --split block under the ceiling")
        check(split is not None and src[:300] not in split and src[-300:] not in split,
              f"{tier}: --split block carries no slice text")
        check(split is not None and f"{len(got)} further SessionStart" in split
              and str(CONTENT_DIR / f"{tier}.md") in split,
              f"{tier}: --split block names the part count and the slice path")
        legacy = run(home, cwd, [], tier)
        check(legacy is not None and src in legacy,
              f"{tier}: default output still carries the whole slice")

    print("5. Part hooks write nothing; the main entry is the positive control")
    home2 = tmp / "home2"
    (home2 / ".claude").mkdir(parents=True)
    cwd2 = tmp / "proj2"
    cwd2.mkdir()
    sid = f"test-split-readonly-{os.getpid()}"
    for k in range(1, M.SLICE_PARTS_MAX + 1):
        run(home2, cwd2, ["--slice-part", str(k)], session_id=sid)
    projects = home2 / ".claude" / "projects"
    written = sorted(str(p.relative_to(home2)) for p in projects.rglob("*")) if projects.exists() else []
    check(written == [], f"parts wrote nothing under ~/.claude/projects ({written})")
    check(not anchor_for(sid).exists(), "parts wrote no session anchor")
    run(home2, cwd2, ["--split"], session_id=sid)
    floor = list(projects.rglob(M.TIER_FLOOR_FILENAME)) if projects.exists() else []
    check(bool(floor), "control: the --split entry does write the tier floor")
    check(anchor_for(sid).exists(), "control: the --split entry does write the anchor")
    try:
        anchor_for(sid).unlink()
    except OSError:
        pass

    print("5b. On an early failure only the --split entry reports it")
    def raw_run(args: list[str], stdin: str) -> str:
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["USERPROFILE"] = str(home)
        env["CLAUDE_PLUGIN_ROOT"] = str(PLUGIN_ROOT)
        p = subprocess.run([sys.executable, str(SESSION_START), *args], input=stdin,
                           capture_output=True, text=True, encoding="utf-8", env=env, timeout=30)
        return p.stdout
    check(raw_run(["--split"], "{not json").strip() != "",
          "control: --split reports a stdin failure")
    check(all(raw_run(["--slice-part", str(k)], "{not json").strip() == ""
              for k in range(1, M.SLICE_PARTS_MAX + 1)),
          "part entries print nothing on a stdin failure (no duplicate banners)")

    print("6. hooks.json wiring")
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    cmds = [h["command"] for entry in hooks["hooks"]["SessionStart"] for h in entry["hooks"]]
    base = 'python "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.py"'
    expected = [f"{base} --split"] + [f"{base} --slice-part {k}"
                                      for k in range(1, M.SLICE_PARTS_MAX + 1)]
    check(cmds == expected, f"SessionStart commands are --split + parts 1..{M.SLICE_PARTS_MAX}")
    check(base not in [c.strip() for c in cmds], "no flagless entry that would inline the slice")

    print("7. split_slice on synthetic input")
    budget = 500
    samples = {
        "paragraphs": "\n\n".join(f"para {i} " + "w" * 120 for i in range(30)),
        "no blank lines": "".join(f"line {i} " + "v" * 90 + "\n" for i in range(40)),
        "one over-long line": "start\n" + "L" * 1700 + "\nend\n",
        "multibyte": "".join("Ελληνικό κείμενο δοκιμής. " * 6 + "\n" for _ in range(20)),
        "empty": "",
    }
    for name, text in samples.items():
        chunks = M.split_slice(text, budget)
        check(all(len(c.encode("utf-8")) <= budget for c in chunks),
              f"{name}: every chunk within {budget} bytes")
        check("".join(chunks) == text, f"{name}: chunks rejoin exactly")
    paras = M.split_slice(samples["paragraphs"], budget)
    check(all(c.endswith("\n\n") for c in paras[:-1]),
          "paragraphs: cuts fall on blank lines when they can")

    print("8. A slice needing more parts than are wired is announced")
    big = "\n\n".join("q" * 4000 for _ in range(10))
    n = len(M.split_slice(big))
    check(n > M.SLICE_PARTS_MAX, f"synthetic slice needs {n} parts")
    import io
    from contextlib import redirect_stdout
    original = M.load_slice
    old_home = os.environ.get("HOME")
    sys_stdin = sys.stdin
    setattr(M, "load_slice", lambda _tier: big)
    try:
        os.environ["HOME"] = str(home)
        sys.stdin = io.StringIO(json.dumps({"session_id": "test-split-over", "cwd": str(cwd)}))
        os.environ["CLAUDE_TIER"] = "T3"
        buf = io.StringIO()
        with redirect_stdout(buf):
            M.main(["--split"])
        ctx = json.loads(buf.getvalue())["hookSpecificOutput"]["additionalContext"]
        check(f"only {M.SLICE_PARTS_MAX} are injected" in ctx and "Read the whole slice" in ctx,
              "--split block tells the model to read the whole slice")
    finally:
        setattr(M, "load_slice", original)
        sys.stdin = sys_stdin
        os.environ.pop("CLAUDE_TIER", None)
        if old_home is not None:
            os.environ["HOME"] = old_home
        try:
            anchor_for("test-split-over").unlink()
        except OSError:
            pass

    print(f"\n{PASS} pass / {FAIL} fail")
    for f in FAILURES:
        print(f"  - {f}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
