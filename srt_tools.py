#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Optional SRT cue tools: timing shift, overlap repair, renumbering,
hearing-impaired tag removal, formatting tag stripping, reading-speed check.

All tools run on decoded text AFTER encoding repair. Any modifying tool
re-serializes cues in canonical SRT form (sequential numbers only if
renumbering ran or cues were dropped). Files with no parseable cues are
left untouched by the tools.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

TIMESTAMP_RE = re.compile(
    r"(\d{1,3}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*-->\s*"
    r"(\d{1,3}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})"
)

_HI_LINE_RE = re.compile(r"^\s*[\[\(].*[\]\)]\s*$")
_MUSIC_CHARS = ("\u266a", "\u266b")  # ♪ ♫
_HTML_TAG_RE = re.compile(r"<[^>\n]+>")
_ASS_TAG_RE = re.compile(r"\{[^\n}]*\}")


@dataclass
class Cue:
    index: int
    start_ms: int
    end_ms: int
    lines: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@dataclass
class ToolOptions:
    shift_ms: int = 0
    fix_overlaps: bool = False
    renumber: bool = False
    strip_hi: bool = False
    strip_html: bool = False
    check_cps: bool = True
    cps_limit: float = 20.0


@dataclass
class ToolReport:
    cues: int = 0
    shifted_ms: int = 0
    overlaps_fixed: int = 0
    renumbered: bool = False
    hi_removed: int = 0
    html_removed: int = 0
    cps_violations: List[int] = field(default_factory=list)
    modified: bool = False
    notes: List[str] = field(default_factory=list)


def parse_timestamp(value: str) -> int:
    match = re.match(r"(\d{1,3}):(\d{1,2}):(\d{1,2})[,.](\d{1,3})\s*$", value.strip())
    if not match:
        raise ValueError(f"bad timestamp: {value!r}")
    h, m, s, ms = (int(g) for g in match.groups())
    return ((h * 60 + m) * 60 + s) * 1000 + int(str(ms)[:3].ljust(3, "0")[:3])


def format_timestamp(ms: int) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str) -> List[Cue]:
    """Tolerant SRT parser. Blocks without a valid timestamp line are skipped."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    cues: List[Cue] = []
    for block in re.split(r"\n\s*\n", normalized):
        lines = [ln for ln in block.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if not lines:
            continue
        first, rest = lines[0], lines[1:]
        number: Optional[int] = None
        stamp_line: Optional[str] = None
        if first.strip().isdigit() and rest:
            number = int(first.strip())
            stamp_line = rest[0]
            body = rest[1:]
        elif TIMESTAMP_RE.search(first):
            stamp_line = first
            body = rest
        else:
            continue
        match = TIMESTAMP_RE.search(stamp_line or "")
        if not match:
            continue
        try:
            start = parse_timestamp(f"{match.group(1)}:{match.group(2)}:"
                                    f"{match.group(3)},{match.group(4)}")
            end = parse_timestamp(f"{match.group(5)}:{match.group(6)}:"
                                  f"{match.group(7)},{match.group(8)}")
        except ValueError:
            continue
        cues.append(Cue(index=number if number is not None else len(cues) + 1,
                        start_ms=start, end_ms=end, lines=body))
    return cues


def format_srt(cues: List[Cue]) -> str:
    blocks = []
    for number, cue in enumerate(cues, 1):
        blocks.append(f"{number}\n{format_timestamp(cue.start_ms)} --> "
                      f"{format_timestamp(cue.end_ms)}\n" + "\n".join(cue.lines))
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def shift_cues(cues: List[Cue], ms: int) -> int:
    if not ms:
        return 0
    for cue in cues:
        duration = max(0, cue.end_ms - cue.start_ms)
        cue.start_ms = max(0, cue.start_ms + ms)
        cue.end_ms = cue.start_ms + duration
    return len(cues)


def fix_overlaps(cues: List[Cue]) -> int:
    """Clamp each cue end to (next start - 1ms) where they overlap, in file order."""
    fixed = 0
    for current, nxt in zip(cues, cues[1:]):
        if current.end_ms > nxt.start_ms:
            current.end_ms = max(current.start_ms, nxt.start_ms - 1)
            fixed += 1
    return fixed


def renumber_cues(cues: List[Cue]) -> None:
    for number, cue in enumerate(cues, 1):
        cue.index = number


def _strip_hi_from_lines(lines: List[str]) -> Tuple[List[str], int]:
    kept: List[str] = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if _HI_LINE_RE.match(line):
            removed += 1
            continue
        if stripped and all(ch in _MUSIC_CHARS or ch.isspace() for ch in stripped):
            removed += 1
            continue
        if stripped.startswith(_MUSIC_CHARS) and stripped.endswith(_MUSIC_CHARS) and len(stripped) >= 2:
            removed += 1
            continue
        kept.append(line)
    return kept, removed


def _strip_html_from_lines(lines: List[str]) -> Tuple[List[str], int]:
    kept: List[str] = []
    removed = 0
    for line in lines:
        new_line, n1 = _HTML_TAG_RE.subn("", line)
        new_line, n2 = _ASS_TAG_RE.subn("", new_line)
        removed += n1 + n2
        kept.append(new_line)
    return kept, removed


def cps_of(cue: Cue) -> float:
    """Characters per second: visible chars (tags stripped) over duration."""
    visible = _HTML_TAG_RE.sub("", _ASS_TAG_RE.sub("", cue.text)).replace("\n", " ")
    duration_s = max(0.001, (cue.end_ms - cue.start_ms) / 1000.0)
    return len(visible) / duration_s


def apply_tools(text: str, options: ToolOptions) -> Tuple[str, ToolReport]:
    report = ToolReport()
    cues = parse_srt(text)
    report.cues = len(cues)
    if not cues:
        if options.shift_ms or options.fix_overlaps or options.renumber \
                or options.strip_hi or options.strip_html:
            report.notes.append("tools-skipped-no-cues")
        if options.check_cps:
            report.notes.append("cps-skipped-no-cues")
        return text, report

    if options.shift_ms:
        shift_cues(cues, options.shift_ms)
        report.shifted_ms = options.shift_ms
        report.modified = True

    if options.fix_overlaps:
        report.overlaps_fixed = fix_overlaps(cues)
        if report.overlaps_fixed:
            report.modified = True

    hi_total = 0
    html_total = 0
    if options.strip_hi or options.strip_html:
        kept_cues: List[Cue] = []
        for cue in cues:
            lines = cue.lines
            if options.strip_hi:
                lines, n = _strip_hi_from_lines(lines)
                hi_total += n
            if options.strip_html:
                lines, n = _strip_html_from_lines(lines)
                html_total += n
            # Drop cues left with no visible text (but keep pure whitespace layout).
            if not "".join(lines).strip():
                continue
            cue.lines = lines
            kept_cues.append(cue)
        if len(kept_cues) != len(cues):
            cues = kept_cues
            renumber_cues(cues)
            report.renumbered = True
        report.hi_removed = hi_total
        report.html_removed = html_total
        if hi_total or html_total or report.renumbered:
            report.modified = True

    if options.renumber and not report.renumbered:
        renumber_cues(cues)
        report.renumbered = True
        report.modified = True

    if options.check_cps:
        report.cps_violations = [cue.index for cue in cues
                                 if cps_of(cue) > options.cps_limit]

    if report.modified:
        return format_srt(cues), report
    return text, report
