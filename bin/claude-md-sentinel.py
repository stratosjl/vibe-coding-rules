#!/usr/bin/env python3
"""claude-md-sentinel.py — insert/update tier sentinel in project CLAUDE.md.

Usage:
    python claude-md-sentinel.py <NEW_TIER> [--project-root <path>]

<NEW_TIER>          one of T0, T1, T2, T3, T4.
--project-root      override auto-discovery (defaults to git-root walk from cwd).

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
    1   argument validation failure
    2   IO failure (write/atomic-replace failed)
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


def atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
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


def write_sentinel(project_root: Path, new_tier: str) -> str:
    claude_md = project_root / "CLAUDE.md"
    if not claude_md.is_file():
        content = f"---\ntier: {new_tier}\n---\n"
        atomic_write(claude_md, content)
        return "created"

    text = claude_md.read_text(encoding="utf-8")
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
        atomic_write(claude_md, new_text)
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
        atomic_write(claude_md, new_text)
        return "replaced"

    prefix = f"---\ntier: {new_tier}\n---\n\n"
    new_text = prefix + text
    atomic_write(claude_md, new_text)
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
    args = parser.parse_args(argv[1:])

    new_tier = args.tier.strip().upper()
    if not VALID_TIER.match(new_tier):
        print(
            f"error: invalid tier {args.tier!r}; expected one of T0, T1, T2, T3, T4",
            file=sys.stderr,
        )
        return 1

    project_root = resolve_project_root(args.project_root)
    try:
        action = write_sentinel(project_root, new_tier)
    except OSError as e:
        print(f"error: CLAUDE.md write failed at {project_root}: {e}", file=sys.stderr)
        return 2

    claude_md = project_root / "CLAUDE.md"
    print(f"claude-md-sentinel: {action} tier={new_tier} at {claude_md}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
