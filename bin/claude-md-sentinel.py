#!/usr/bin/env python3
"""claude-md-sentinel.py — insert/update tier sentinel in project CLAUDE.md.

Usage:
    python claude-md-sentinel.py <NEW_TIER> [--project-root <path>] [--target {auto,CLAUDE.md,AGENTS.md}]

<NEW_TIER>          one of T0, T1, T2, T3, T4.
--project-root      override auto-discovery (defaults to git-root walk from cwd).
--target            sentinel file (default auto: CLAUDE.md when present at project
                    root, AGENTS.md when it is the only one of the two present;
                    with neither present CLAUDE.md is created, matching the
                    behavior from before --target existed).

Why this exists (v1.13.0):
    The project tier floor at ~/.claude/projects/<encoded-cwd>/methodology-tier-floor
    is machine-local cache; it does NOT sync across machines (closes 1b in the
    v1.13.0 fix set). For multi-machine projects (e.g. namescan-api on
    Win laptop + Manjaro minipc) the only durable, portable, git-syncable
    home for an explicit-operator tier override is the `tier: T<N>` sentinel
    in project-root CLAUDE.md, which `hooks/session-start.py:find_tier_in_claude_md`
    parses at every SessionStart with absolute precedence over floor + auto.

    The sentinel reader regex is `^\\s*tier:\\s*T([0-4])\\b` with MULTILINE +
    IGNORECASE (session-start.py line 157). This script writes the sentinel
    inside a YAML frontmatter block at the top of CLAUDE.md (B-ii operator
    choice at v1.13.0 planning); the reader matches it because the regex
    only requires a line beginning with `tier: T<N>`.

Cases handled (idempotent across all of them):
    1. CLAUDE.md absent              → create with frontmatter only + newline.
    2. CLAUDE.md has no frontmatter  → prepend `---\\ntier: T<N>\\n---\\n\\n`.
    3. Frontmatter without `tier:`   → insert `tier: T<N>` at end of block.
    4. Frontmatter with `tier:` same → no-op (idempotent).
    5. Frontmatter with `tier:` diff → replace value in block.
    6. Bare `tier: T<N>` line present (no frontmatter) → replace value in line.

Cross-platform: pure Python stdlib; pathlib + UTF-8 explicit + atomic
os.replace; no sed, no shell, no platform-specific calls. Works on Windows
laptop and Manjaro minipc identically (closes the multi-machine portability
gap that motivated 1b).

Exit codes:
    0   success (action printed to stdout: created|prepended|inserted|noop|replaced)
    1   argument validation failure (invalid tier, --project-root not a directory)
    2   usage error raised by argparse (unknown flag or choice, missing argument)
    3   NOT-RUNNABLE: the environment is wrong, e.g. CLAUDE.md cannot be written

    3 is new at v1.25.0 and dissolves a real collision. The write failure used
    to return 2, which argparse had always been returning for a usage error, so
    a caller seeing 2 could not tell "you passed a bad flag" from "your
    CLAUDE.md is locked or read-only". See methodology-content/T2.md item 2.

    1 is NOT that table's "ran, found a defect". Here it means the CALLER is
    wrong (an invalid tier, or a --project-root that is not a directory), which
    the table places at 2. It is kept at 1 under the same item's "do not
    renumber codes that already discriminate correctly" clause: 1, 2 and 3 now
    carry one meaning each, and test-claude-md-sentinel.py:184 pins 1. One
    divergence a reader should be told rather than left to discover: the
    sibling tier helper bin/anchor-rewrite.sh answers the same bad-tier
    argument with 2 (test-exit-contract.py:133). Aligning the two is a one-line
    change here plus a one-line change in that test, so it is left to the
    operator rather than made from inside this file alone.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple


def _force_utf8_streams() -> None:
    """Make stdout/stderr emit non-ASCII paths regardless of host console codec.

    On Windows the default console encoding is cp1252, which raises
    UnicodeEncodeError when a project path contains characters outside Latin-1
    (e.g. a Greek directory name). On Linux/macOS stdout is normally already
    UTF-8, so this is a harmless no-op. Guarded with getattr because some
    captured streams (e.g. test harnesses) expose no reconfigure().
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass


VALID_TIER = re.compile(r"^T[0-4]$")
# Reader uses `^\s*tier:\s*T([0-4])\b` for DETECTION (session-start.py:157), which
# allows `\s*` to greedily span newlines. For WRITING we narrow to `[ \t]*` so
# replacement preserves the blank line that may precede the bare-tier line in
# legacy CLAUDE.md formats (otherwise the leading newline gets eaten on replace
# and the file's vertical spacing collapses).
SENTINEL_BARE_RE = re.compile(
    r"^([ \t]*)tier:([ \t]*)T([0-4])\b",
    re.MULTILINE | re.IGNORECASE,
)
SENTINEL_DETECT_RE = re.compile(r"^\s*tier:\s*T([0-4])\b", re.MULTILINE | re.IGNORECASE)
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)


def find_git_root(start: Path, max_levels: int = 20) -> Optional[Path]:
    cur = start.resolve()
    for _ in range(max_levels):
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent
    return None


def resolve_project_root(override: Optional[str]) -> Path:
    if override:
        p = Path(override).expanduser().resolve()
        if not p.is_dir():
            print(f"error: --project-root {p} is not a directory", file=sys.stderr)
            sys.exit(1)
        return p
    cwd = Path.cwd().resolve()
    return find_git_root(cwd) or cwd


def detect_newline(raw_text: str) -> str:
    """Return the terminator to write back: CRLF when it dominates, else LF.

    Call this on the RAW decoded text, before any universal-newline
    translation, so the counts are of what is actually on disk. Without it the
    read side collapses CRLF to LF, atomic_write's fixed "\\n" never restores
    it, and the first MUTATING run rewrites every line of an operator's CRLF
    CLAUDE.md: git then reports the whole file as changed instead of the four
    frontmatter lines that were actually added.
    """
    crlf = raw_text.count("\r\n")
    lf = raw_text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def atomic_write(target: Path, content: str, newline: str = "\n") -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def update_frontmatter_block(block_body: str, new_tier: str) -> Tuple[str, bool]:
    """Return (new_block_body, changed). Inserts or replaces `tier: T<N>` line.

    Uses `[ \\t]*` rather than `\\s*` for the leading-whitespace class so a
    replacement does not eat blank lines between the tier line and adjacent
    YAML keys. The block_body passed in is the YAML content between the `---`
    markers, exclusive.
    """
    tier_line_re = re.compile(
        r"^([ \t]*)tier:([ \t]*)T[0-4]\b.*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = tier_line_re.search(block_body)
    if m:
        existing_tier = re.search(r"T([0-4])\b", m.group(0), re.IGNORECASE)
        existing_label = "T" + existing_tier.group(1) if existing_tier else None
        if existing_label and existing_label.upper() == new_tier.upper():
            return block_body, False
        new_block = tier_line_re.sub(
            lambda mm: f"{mm.group(1)}tier:{mm.group(2) or ' '}{new_tier}",
            block_body,
            count=1,
        )
        return new_block, True
    if block_body and not block_body.endswith("\n"):
        block_body = block_body + "\n"
    return block_body + f"tier: {new_tier}", True


def write_sentinel(project_root: Path, new_tier: str, target: str = "CLAUDE.md") -> str:
    claude_md = project_root / target
    if not claude_md.is_file():
        content = f"---\ntier: {new_tier}\n---\n"
        atomic_write(claude_md, content)
        return "created"

    # read_bytes().decode rather than read_text(): read_text applies
    # universal-newline translation, and its newline="" opt-out is 3.13+ while
    # this script declares no minimum version. Translate to "\n" here so every
    # regex below still works on LF text, and carry the file's own terminator
    # through to atomic_write so it is written back the way it was found.
    raw = claude_md.read_bytes().decode("utf-8")
    newline = detect_newline(raw)
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    fm_match = FRONTMATTER_RE.match(text)

    if fm_match:
        block_body = fm_match.group(1)
        rest = text[fm_match.end():]
        new_block_body, changed = update_frontmatter_block(block_body, new_tier)
        if not changed:
            return "noop"
        new_text = f"---\n{new_block_body}\n---\n{rest}" if rest.startswith("\n") or not rest else f"---\n{new_block_body}\n---\n\n{rest}"
        if new_text == text:
            return "noop"
        atomic_write(claude_md, new_text, newline=newline)
        # Distinguish insert vs replace by whether the old block had a tier line.
        had_tier = re.search(r"^\s*tier:\s*T[0-4]\b", block_body, re.MULTILINE | re.IGNORECASE) is not None
        return "replaced" if had_tier else "inserted"

    bare_match = SENTINEL_BARE_RE.search(text)
    if bare_match:
        existing_tier = "T" + bare_match.group(3)
        if existing_tier.upper() == new_tier.upper():
            return "noop"
        new_text = SENTINEL_BARE_RE.sub(
            lambda mm: f"{mm.group(1)}tier:{mm.group(2) or ' '}{new_tier}",
            text,
            count=1,
        )
        if new_text == text:
            return "noop"
        atomic_write(claude_md, new_text, newline=newline)
        return "replaced"

    prefix = f"---\ntier: {new_tier}\n---\n\n"
    new_text = prefix + text
    atomic_write(claude_md, new_text, newline=newline)
    return "prepended"


def main(argv: list[str]) -> int:
    _force_utf8_streams()
    parser = argparse.ArgumentParser(
        description="Insert/update tier sentinel in project-root CLAUDE.md."
    )
    parser.add_argument("tier", help="Target tier (T0..T4)")
    parser.add_argument(
        "--project-root",
        default=None,
        help="Override project-root auto-discovery (defaults to git-root walk from cwd).",
    )
    parser.add_argument(
        "--target",
        choices=["auto", "CLAUDE.md", "AGENTS.md"],
        default="auto",
        help="Sentinel file. auto: CLAUDE.md if present; AGENTS.md only when it is the sole sentinel file; otherwise CLAUDE.md.",
    )
    args = parser.parse_args(argv[1:])

    new_tier = args.tier.strip().upper()
    if not VALID_TIER.match(new_tier):
        print(
            f"error: invalid tier {args.tier!r}; expected one of T0, T1, T2, T3, T4",
            file=sys.stderr,
        )
        return 1

    project_root = resolve_project_root(args.project_root)
    target = args.target
    if target == "auto":
        # CLAUDE.md keeps priority; AGENTS.md is picked only when it is the
        # project's sentinel file. With neither present, keep the pre-existing
        # behavior of creating CLAUDE.md so existing no-flag callers see zero
        # behavior change.
        if (project_root / "AGENTS.md").exists() and not (project_root / "CLAUDE.md").exists():
            target = "AGENTS.md"
        else:
            target = "CLAUDE.md"
    try:
        action = write_sentinel(project_root, new_tier, target)
    except OSError as e:
        # 3 rather than 2, because an unwritable, locked or read-only sentinel
        # file is the ENVIRONMENT being wrong, while 2 is what argparse already
        # emits when the CALLER is wrong. Measured 2026-09-04: with a directory
        # sitting on the CLAUDE.md path this returned 2, byte-identical to the
        # code from `--target BOGUS`. The six tier commands propagate this code
        # verbatim (`exit "$rc"`) and already exit 3 for their own missing-helper
        # case, so one command was reporting two environment failures as 3 and 2.
        print(
            f"error: NOT-RUNNABLE: {target} write failed at {project_root}: {e}",
            file=sys.stderr,
        )
        return 3

    claude_md = project_root / target
    print(f"claude-md-sentinel: {action} tier={new_tier} at {claude_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
