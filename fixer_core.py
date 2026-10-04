#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Universal subtitle text / encoding repair engine.

What it handles, for any language:
  1. Files stored as raw single-byte codepages (Windows-125x, ISO-8859-x,
     KOI8-R, ...) but opened as UTF-8 -> decoded with the right codec.
  2. Double-encoded UTF-8 ("mojibake": CafÃ© -> Café, ÐŸÑ€Ð¸Ð²ÐµÑ‚ -> Привет,
     ä¸­æ–‡ -> 中文, Ù…Ø±Ø­Ø¨Ø§ -> مرحبا). This repair is purely byte-level,
     so it works for every language, including CJK, Arabic, Cyrillic....
  3. Text that was mis-decoded and then RE-SAVED (so the raw bytes are lost),
     e.g. Turkish Windows-1254 read as Windows-1252 and saved as UTF-8:
     Yardým -> Yardım, Ýmdat -> İmdat.
     Fixed with configurable (wrong -> right) codec reinterpretation pairs.
  4. Fully user-defined literal/regex find->replace rules applied last.

Only the Python standard library is used.
"""

from __future__ import annotations

import codecs
import csv
import html as _html
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple

from srt_tools import ToolOptions, ToolReport, apply_tools


# ---------------------------------------------------------------------------
# Mojibake sniffing: Latin-1 decoded UTF-8 byte sequences.
# A 2/3/4-byte UTF-8 sequence, once decoded as Latin-1/cp1252, looks like:
#   leader in C2-DF + 1 continuation (80-BF), or
#   leader in E0-EF + 2 continuations, or leader in F0-F4 + 3 continuations.
# Requiring real continuation bytes keeps false positives very rare
# ("à la", "Äpfel", "Åre" etc. do NOT match).
# ---------------------------------------------------------------------------
_MOJIBAKE_RE = re.compile(
    r"[\u00C2-\u00DF][\u0080-\u00BF]"
    r"|[\u00E0-\u00EF][\u0080-\u00BF]{2}"
    r"|[\u00F0-\u00F4][\u0080-\u00BF]{3}"
)

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Scripts common enough in subtitles to be produced by automatic repair.
# Everything else letter-like (e.g. Syriac ܜ from the byte pair of "Üç")
# vetoes the repair: it is far more likely a byte collision than real text.
# Punctuation/symbols/numbers/marks are never gated (—, €, ♪, 😀 are fine).
_COMMON_SCRIPTS = frozenset({
    "LATIN", "CYRILLIC", "GREEK", "ARABIC", "HEBREW", "ARMENIAN",
    "GEORGIAN", "CJK", "HIRAGANA", "KATAKANA", "HANGUL", "THAI",
    "THAANA", "DEVANAGARI", "BENGALI", "TAMIL", "TELUGU", "KANNADA",
    "MALAYALAM", "GUJARATI", "GURMUKHI", "SINHALA", "MYANMAR", "KHMER",
    "LAO", "ETHIOPIC",
})


def _letters_all_common(text: str) -> bool:
    for ch in text:
        if ord(ch) > 127 and unicodedata.category(ch)[0] == "L":
            name = unicodedata.name(ch, "")
            if not name or name.split(" ", 1)[0] not in _COMMON_SCRIPTS:
                return False
    return True

_ALLOWED_PUNCT = set(" \t\n\r.,;:!?-_—–-\"'«»“”‘’‚…()[]{}<>/*+=%@#$&|~^`\\")


def _count_mojibake_patterns(text: str) -> int:
    return len(_MOJIBAKE_RE.findall(text))


def _has_new_controls(candidate: str, original: str) -> bool:
    """True if candidate introduces control chars that were not there before."""
    orig_controls = set(_CONTROL_RE.findall(original))
    for ch in _CONTROL_RE.findall(candidate):
        if ch not in orig_controls:
            return True
    return False


def _encode_runs(line: str, encoding: str) -> List[Tuple[str, bool]]:
    """Split a line into maximal runs that are/aren't encodable with `encoding`."""
    runs: List[Tuple[str, bool]] = []
    buf: List[str] = []
    buf_ok: Optional[bool] = None
    for ch in line:
        try:
            ch.encode(encoding)
            ok = True
        except (UnicodeEncodeError, ValueError):
            ok = False
        if buf_ok is None:
            buf_ok = ok
        if ok != buf_ok:
            runs.append(("".join(buf), buf_ok))
            buf = []
            buf_ok = ok
        buf.append(ch)
    if buf:
        runs.append(("".join(buf), buf_ok if buf_ok is not None else True))
    return runs


def _decode_run_strict(raw: bytes, encoding: str) -> Optional[str]:
    try:
        return raw.decode(encoding)
    except (UnicodeDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 1) Universal UTF-8 mojibake repair (works for every language)
# ---------------------------------------------------------------------------
def repair_utf8_mojibake(text: str, max_passes: int = 3) -> Tuple[str, int, int]:
    """Repair double-encoded UTF-8 runs.

    Returns (new_text, fixed_lines, fixed_runs).
    """
    total_runs = 0
    fixed_line_idx = set()
    for _ in range(max_passes):
        pass_runs = 0
        out_lines: List[str] = []
        for idx, line in enumerate(text.split("\n")):
            new_line_parts: List[str] = []
            # Split runs with latin1 (1:1 for all U+0000-U+00FF, never splits
            # on C1 controls); undecodable chars (>U+00FF, i.e. already
            # correct text like ğ/Привет/中文) pass through untouched.
            for chunk, encodable in _encode_runs(line, "latin1"):
                if encodable and _MOJIBAKE_RE.search(chunk):
                    fixed = _try_repair_run(chunk)
                    if fixed is not None and fixed != chunk:
                        chunk = fixed
                        pass_runs += 1
                        fixed_line_idx.add(idx)
                new_line_parts.append(chunk)
            out_lines.append("".join(new_line_parts))
        text = "\n".join(out_lines)
        total_runs += pass_runs
        if pass_runs == 0:
            break
    return text, len(fixed_line_idx), total_runs


def _try_repair_run(chunk: str) -> Optional[str]:
    before = _count_mojibake_patterns(chunk)
    if before == 0:
        return None
    for encoding in ("cp1252", "latin1"):
        try:
            raw = chunk.encode(encoding)
        except (UnicodeEncodeError, ValueError):
            continue
        candidate = _decode_run_strict(raw, "utf-8")
        if candidate is None:
            continue
        if _count_mojibake_patterns(candidate) >= before:
            continue
        if _has_new_controls(candidate, chunk):
            continue
        if not _letters_all_common(candidate):
            continue
        return candidate
    return None


# ---------------------------------------------------------------------------
# 2) Codec reinterpretation pairs: text misread as WRONG, actually RIGHT.
#    Generalizes every "X read as Y then re-saved" corruption, all languages.
# ---------------------------------------------------------------------------
def reinterpret(text: str, wrong_encoding: str, right_encoding: str) -> Tuple[str, int]:
    """Reinterpret text: encode with `wrong_encoding`, decode with `right_encoding`.

    Run-based so already-correct characters outside `wrong_encoding` survive.
    Returns (new_text, changed_lines).
    """
    codecs.lookup(wrong_encoding)
    codecs.lookup(right_encoding)
    changed_lines = 0
    out_lines: List[str] = []
    for line in text.split("\n"):
        parts: List[str] = []
        changed = False
        for chunk, encodable in _encode_runs(line, wrong_encoding):
            if encodable:
                try:
                    raw = chunk.encode(wrong_encoding)
                except (UnicodeEncodeError, ValueError):
                    parts.append(chunk)
                    continue
                candidate = _decode_run_strict(raw, right_encoding)
                if (candidate is not None and candidate != chunk
                        and not _has_new_controls(candidate, chunk)
                        and _letters_all_common(candidate)
                        and _count_mojibake_patterns(candidate) <= _count_mojibake_patterns(chunk)):
                    chunk = candidate
                    changed = True
            parts.append(chunk)
        new_line = "".join(parts)
        if changed:
            changed_lines += 1
        out_lines.append(new_line)
    return "\n".join(out_lines), changed_lines


# ---------------------------------------------------------------------------
# Language profiles: byte candidates, reinterpret pairs, corruption markers.
# `markers` are chars that (in practice) only show up through THIS profile's
# corruption, so Auto mode can safely trigger the profile on sight.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LanguageProfile:
    id: str
    byte_candidates: Tuple[str, ...]
    pairs: Tuple[Tuple[str, str], ...]
    markers: str = ""
    # Letters shared with the confused-with codec that usually survive the
    # corruption (e.g. çöü stay intact when 1254 is read as 1252). Used to
    # confirm auto-triggering with a single marker occurrence.
    shared_letters: str = ""


UNIVERSAL_CANDIDATES = (
    "windows-1252", "windows-1254", "windows-1250", "windows-1251",
    "windows-1253", "windows-1256", "windows-1255", "windows-1257",
    "windows-1258", "iso-8859-9", "iso-8859-2", "iso-8859-7",
    "iso-8859-6", "iso-8859-8", "iso-8859-13", "iso-8859-15",
    "koi8-r", "latin-1",
)

PROFILES: Dict[str, LanguageProfile] = {
    "auto": LanguageProfile("auto", UNIVERSAL_CANDIDATES, ()),
    "turkish": LanguageProfile(
        "turkish",
        ("windows-1254", "iso-8859-9", "windows-1252"),
        (("cp1252", "cp1254"), ("latin1", "cp1254")),
        markers="ÝýÞþÐð",  # 1254 bytes (İıŞşĞğ) displayed as 1252, then re-saved
        shared_letters="çÇöÖüÜ",  # survive 1254-as-1252 misreading intact
    ),
    "western": LanguageProfile(
        "western",
        ("windows-1252", "iso-8859-15", "iso-8859-1"),
        (),
    ),
    "central": LanguageProfile(
        "central",
        ("windows-1250", "iso-8859-2", "windows-1252"),
        (("cp1252", "cp1250"), ("latin1", "cp1250")),
    ),
    "cyrillic": LanguageProfile(
        "cyrillic",
        ("windows-1251", "koi8-r", "iso-8859-5", "windows-1252"),
        (("cp1252", "cp1251"), ("latin1", "cp1251"), ("cp1252", "koi8-r")),
    ),
    "greek": LanguageProfile(
        "greek",
        ("windows-1253", "iso-8859-7", "windows-1252"),
        (("cp1252", "cp1253"), ("latin1", "cp1253")),
    ),
    "arabic": LanguageProfile(
        "arabic",
        ("windows-1256", "iso-8859-6", "windows-1252"),
        (("cp1252", "cp1256"), ("latin1", "cp1256")),
    ),
    "hebrew": LanguageProfile(
        "hebrew",
        ("windows-1255", "iso-8859-8", "windows-1252"),
        (("cp1252", "cp1255"), ("latin1", "cp1255")),
    ),
    "baltic": LanguageProfile(
        "baltic",
        ("windows-1257", "iso-8859-13", "windows-1252"),
        (("cp1252", "cp1257"), ("latin1", "cp1257")),
    ),
    "vietnamese": LanguageProfile(
        "vietnamese",
        ("windows-1258", "windows-1252"),
        (("cp1252", "cp1258"), ("latin1", "cp1258")),
    ),
}

PROFILE_ORDER = ("auto", "turkish", "western", "central", "cyrillic",
                 "greek", "arabic", "hebrew", "baltic", "vietnamese")


def get_profile(profile_id: str) -> LanguageProfile:
    return PROFILES.get((profile_id or "auto").lower(), PROFILES["auto"])


def suggest_profile(text: str) -> Optional[str]:
    """Return a profile id when its distinctive corruption markers are present."""
    for pid in PROFILE_ORDER:
        if pid == "auto":
            continue
        markers = PROFILES[pid].markers
        if markers and any(m in text for m in markers):
            return pid
    return None


def _auto_pairs_allowed(text: str, profile: LanguageProfile) -> bool:
    """Decide whether marker-triggered pairs may run without asking.

    Guards against false positives (e.g. Icelandic ð/þ, or Cyrillic text
    re-saved through cp1252 which also contains ð), so the gate is
    deliberately conservative:
      - remaining UTF-8 mojibake patterns veto (different corruption family);
      - otherwise require 2+ marker hits AND surviving shared letters of
        that language (çöü almost always survive a 1254-as-1252 misread,
        but basically never occur in Icelandic).
    Anything weaker is only *suggested* (see possible_profile): no silent
    damage, the user confirms via preview with one click.
    """
    if not profile.markers:
        return False
    if _count_mojibake_patterns(text):
        return False
    hits = sum(text.count(m) for m in profile.markers)
    if hits < 2:
        return False
    if profile.shared_letters:
        return any(c in text for c in profile.shared_letters)
    return hits >= 3


# ---------------------------------------------------------------------------
# Byte-level decoding with scoring (no third-party chardet needed)
# ---------------------------------------------------------------------------
def _score_decoded(text: str) -> float:
    if not text:
        return 0.0
    good = 0
    bad = 0
    for ch in text:
        o = ord(ch)
        if o < 32 and ch not in "\t\n\r":
            bad += 2
            continue
        if o == 0x7F or 0x80 <= o <= 0x9F:
            # C1 controls almost never belong in subtitle text; a wrong
            # single-byte guess produces lots of them.
            bad += 1
            continue
        cat = unicodedata.category(ch)
        if cat[0] in "LMN" or cat == "Zs" or ch in _ALLOWED_PUNCT:
            good += 1
        elif cat[0] in "PS":
            good += 1
        else:
            bad += 0.5
    n = len(text)
    return (good - bad) / n


def decode_raw_bytes(raw: bytes, source: str = "auto",
                     profile_id: str = "auto") -> Tuple[str, str, bool]:
    """Decode raw bytes to text. Returns (text, detected_label, had_bom)."""
    from codecs import BOM_UTF8
    had_bom = raw.startswith(BOM_UTF8)
    src = (source or "auto").strip().lower().replace("_", "-")
    profile = get_profile(profile_id)

    if src != "auto":
        try:
            codec = codecs.lookup(src).name
        except LookupError:
            codec = "windows-1252"
            return raw.decode(codec, errors="replace"), codec + " (?)", had_bom
        try:
            return raw.decode(codec), codec, had_bom
        except (UnicodeDecodeError, ValueError):
            fallback = raw.decode("windows-1254", errors="replace")
            return fallback, codec + " (invalid, 1254 fallback)", had_bom

    # --- auto ---
    if had_bom:
        try:
            return raw.decode("utf-8-sig"), "UTF-8-SIG (BOM)", True
        except (UnicodeDecodeError, ValueError):
            pass
    try:
        return raw.decode("utf-8"), "UTF-8", had_bom
    except (UnicodeDecodeError, ValueError):
        pass

    candidates = list(profile.byte_candidates)
    if profile.id == "auto":
        pass  # already the universal list
    else:
        for extra in UNIVERSAL_CANDIDATES:  # let other codepages compete too
            if extra not in candidates:
                candidates.append(extra)

    scored: List[Tuple[float, str, str]] = []
    for cand in candidates:
        try:
            decoded = raw.decode(cand)
        except (UnicodeDecodeError, ValueError, LookupError):
            continue
        scored.append((_score_decoded(decoded), cand, decoded))
    if not scored:
        return raw.decode("latin-1"), "Latin-1 (last resort)", had_bom
    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best_cand, best_text = scored[0]

    # Tie-break: if the winner's text shows another profile's distinctive
    # corruption markers and that profile's own decoding scores nearly as
    # well, prefer it (e.g. Turkish 1254 bytes also decode "cleanly" as 1252).
    for score, cand, decoded in scored[1:4]:
        if best_score - score > 0.02:
            break
        for pid in PROFILE_ORDER:
            if pid == "auto":
                continue
            markers = PROFILES[pid].markers
            if markers and any(m in decoded for m in markers):
                own_first = PROFILES[pid].byte_candidates[0]
                if cand == own_first:
                    return decoded, f"{cand} (auto, {pid} markers)", had_bom
    label = best_cand + (" (auto)" if profile.id == "auto" else f" (auto, {profile.id})")
    return best_text, label, had_bom


# ---------------------------------------------------------------------------
# Custom user rules
# ---------------------------------------------------------------------------
@dataclass
class CustomRule:
    find: str
    replace: str
    enabled: bool = True
    use_regex: bool = False
    note: str = ""


def validate_rule(rule: CustomRule) -> Optional[str]:
    if not rule.find:
        return "empty-find"
    if rule.use_regex:
        try:
            re.compile(rule.find)
        except re.error as exc:
            return f"invalid-regex: {exc}"
    return None


def apply_custom_rules(text: str, rules: Sequence[CustomRule]) -> Tuple[str, int]:
    total = 0
    for rule in rules:
        if not rule.enabled or not rule.find:
            continue
        if rule.use_regex:
            try:
                pattern = re.compile(rule.find)
            except re.error:
                continue
            text, n = pattern.subn(rule.replace, text)
            total += n
        else:
            n = text.count(rule.find)
            if n:
                text = text.replace(rule.find, rule.replace)
                total += n
    return text, total


def load_rules(path: str) -> List[CustomRule]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("rules", []) if isinstance(data, dict) else data
    rules: List[CustomRule] = []
    for item in items:
        if isinstance(item, dict) and item.get("find"):
            rules.append(CustomRule(
                find=str(item.get("find", "")),
                replace=str(item.get("replace", "")),
                enabled=bool(item.get("enabled", True)),
                use_regex=bool(item.get("use_regex", False)),
                note=str(item.get("note", "")),
            ))
    return rules


def save_rules(path: str, rules: Sequence[CustomRule]) -> None:
    data = {"rules": [
        {"find": r.find, "replace": r.replace, "enabled": r.enabled,
         "use_regex": r.use_regex, "note": r.note} for r in rules
    ]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------
@dataclass
class FixReport:
    detected_encoding: str = "unknown"
    had_bom: bool = False
    mojibake_lines: int = 0
    mojibake_runs: int = 0
    pair_label: Optional[str] = None
    pair_changes: int = 0
    suggested_profile: Optional[str] = None
    possible_profile: Optional[str] = None
    custom_changes: int = 0
    tools: Optional[ToolReport] = None
    dry_run: bool = False
    output_encoding: str = "utf-8-sig"
    notes: List[str] = field(default_factory=list)


def fix_text(text: str, profile_id: str = "auto", use_mojibake: bool = True,
             use_pairs: bool = True,
             custom_rules: Sequence[CustomRule] = (),
             manual_pair: Optional[Tuple[str, str]] = None,
             tools: Optional[ToolOptions] = None) -> Tuple[str, FixReport]:
    """Apply, in order: mojibake repair -> codec pairs -> custom rules -> SRT tools."""
    report = FixReport()
    profile = get_profile(profile_id)

    if use_mojibake and _count_mojibake_patterns(text):
        text, lines, runs = repair_utf8_mojibake(text)
        report.mojibake_lines = lines
        report.mojibake_runs = runs

    pairs: List[Tuple[str, str]] = []
    if use_pairs:
        if profile.id == "auto":
            suggested = suggest_profile(text)
            if suggested:
                # Always reported so the UI can hint at the profile; applied
                # only when the safety gate passes.
                report.possible_profile = suggested
                candidate = get_profile(suggested)
                if _auto_pairs_allowed(text, candidate):
                    report.suggested_profile = suggested
                    pairs = list(candidate.pairs)
        elif _count_mojibake_patterns(text) == 0:
            pairs = list(profile.pairs)
        else:
            report.notes.append("pairs-skipped-patterns-remain")
    if manual_pair:
        pairs.append(manual_pair)

    for wrong, right in pairs:
        try:
            text, changed = reinterpret(text, wrong, right)
        except (LookupError, ValueError):
            report.notes.append(f"unknown-codec: {wrong}->{right}")
            continue
        if changed:
            report.pair_changes += changed
            report.pair_label = f"{wrong}->{right}"

    if custom_rules:
        text, n = apply_custom_rules(text, custom_rules)
        report.custom_changes = n

    tool_options = tools or ToolOptions()
    text, tool_report = apply_tools(text, tool_options)
    report.tools = tool_report

    if (report.mojibake_lines == 0 and report.pair_changes == 0
            and report.custom_changes == 0 and not tool_report.modified):
        report.notes.append("no-visible-corruption")
    return text, report


def fix_bytes(raw: bytes, source: str = "auto", profile_id: str = "auto",
              use_mojibake: bool = True, use_pairs: bool = True,
              custom_rules: Sequence[CustomRule] = (),
              manual_pair: Optional[Tuple[str, str]] = None,
              tools: Optional[ToolOptions] = None) -> Tuple[str, FixReport]:
    text, detected, had_bom = decode_raw_bytes(raw, source, profile_id)
    fixed, report = fix_text(text, profile_id, use_mojibake, use_pairs,
                             custom_rules, manual_pair, tools)
    report.detected_encoding = detected
    report.had_bom = had_bom
    return fixed, report


def detect_line_ending(raw: bytes) -> str:
    if b"\r\n" in raw:
        return "\r\n"
    if b"\r" in raw and b"\n" not in raw:
        return "\r"
    return "\n"


def _backup_original(raw: bytes, input_path: str, output_path: str,
                     backup_mode: str, backup_dir: Optional[str]) -> Optional[str]:
    """Copy the original bytes aside when overwriting. Returns backup path or None."""
    if backup_mode not in ("bak", "folder"):
        return None
    if os.path.abspath(input_path) != os.path.abspath(output_path):
        return None  # original file untouched, nothing to back up
    if backup_mode == "bak":
        backup_path = input_path + ".bak"
        with open(backup_path, "wb") as bf:
            bf.write(raw)
        return backup_path
    root = backup_dir or os.path.dirname(os.path.abspath(output_path)) or "."
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(root, "backups", stamp)
    os.makedirs(folder, exist_ok=True)
    base = os.path.basename(input_path)
    backup_path = os.path.join(folder, base)
    counter = 2
    while os.path.exists(backup_path):
        stem, ext = os.path.splitext(base)
        backup_path = os.path.join(folder, f"{stem}_{counter}{ext}")
        counter += 1
    with open(backup_path, "wb") as bf:
        bf.write(raw)
    return backup_path


def process_file(input_path: str, output_path: str, source: str = "auto",
                 profile_id: str = "auto", target: str = "utf-8-sig",
                 use_mojibake: bool = True, use_pairs: bool = True,
                 custom_rules: Sequence[CustomRule] = (),
                 manual_pair: Optional[Tuple[str, str]] = None,
                 make_backup: bool = False,
                 tools: Optional[ToolOptions] = None,
                 backup_mode: str = "none",
                 backup_dir: Optional[str] = None,
                 dry_run: bool = False) -> FixReport:
    if make_backup and backup_mode == "none":
        backup_mode = "bak"  # legacy flag
    with open(input_path, "rb") as f:
        raw = f.read()

    fixed, report = fix_bytes(raw, source, profile_id, use_mojibake,
                              use_pairs, custom_rules, manual_pair, tools)

    ending = detect_line_ending(raw) or "\r\n"
    normalized = fixed.replace("\r\n", "\n").replace("\r", "\n")
    out_text = normalized.replace("\n", ending) if ending != "\n" else normalized

    dst = (target or "utf-8-sig").strip().lower().replace("_", "-")
    if dst in ("utf-8", "utf8"):
        encoding, report.output_encoding = "utf-8", "UTF-8"
    else:
        encoding, report.output_encoding = "utf-8-sig", "UTF-8-SIG"

    if dry_run:
        report.dry_run = True
        return report

    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)

    saved_backup = _backup_original(raw, input_path, output_path, backup_mode, backup_dir)
    if saved_backup:
        report.notes.append(f"backup-created: {saved_backup}")

    with open(output_path, "w", encoding=encoding, newline="") as out:
        out.write(out_text)
    return report


def suggest_output_path(input_path: str, output_dir: Optional[str],
                        suffix: str = "_fixed", overwrite: bool = False) -> str:
    base = os.path.basename(input_path)
    stem, ext = os.path.splitext(base)
    ext = ext or ".srt"
    if output_dir:
        if overwrite:
            return os.path.join(output_dir, base)
        return os.path.join(output_dir, f"{stem}{suffix}{ext}")
    folder = os.path.dirname(input_path) or "."
    if overwrite:
        return os.path.join(folder, base)
    return os.path.join(folder, f"{stem}{suffix}{ext}")


def scan_folder(root: str, extension: str = ".srt") -> List[str]:
    """Recursively collect subtitle files under `root`, sorted."""
    found: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith(extension.lower()):
                found.append(os.path.abspath(os.path.join(dirpath, name)))
    return sorted(found)


# ---------------------------------------------------------------------------
# Conversion records + report export (CSV/HTML)
# ---------------------------------------------------------------------------
@dataclass
class ConversionRecord:
    input: str = ""
    output: str = ""
    profile: str = "auto"
    detected: str = ""
    status: str = "ok"  # ok | error | dry-run
    mojibake_lines: int = 0
    pair_changes: int = 0
    pair_label: str = ""
    custom_changes: int = 0
    tools_summary: str = ""
    cps_violations: int = 0
    error: str = ""


def record_from(input_path: str, output_path: str, profile_id: str,
                report: FixReport, status: str = "ok",
                error: str = "") -> ConversionRecord:
    tools_bits = []
    if report.tools is not None:
        tr = report.tools
        if tr.shifted_ms:
            tools_bits.append(f"shift {tr.shifted_ms}ms")
        if tr.overlaps_fixed:
            tools_bits.append(f"overlaps {tr.overlaps_fixed}")
        if tr.renumbered:
            tools_bits.append("renumbered")
        if tr.hi_removed:
            tools_bits.append(f"HI {tr.hi_removed}")
        if tr.html_removed:
            tools_bits.append(f"HTML {tr.html_removed}")
    return ConversionRecord(
        input=input_path, output=output_path, profile=profile_id,
        detected=report.detected_encoding, status=status,
        mojibake_lines=report.mojibake_lines, pair_changes=report.pair_changes,
        pair_label=report.pair_label or "",
        custom_changes=report.custom_changes,
        tools_summary=", ".join(tools_bits),
        cps_violations=len(report.tools.cps_violations) if report.tools else 0,
        error=error,
    )


_REPORT_FIELDS = ("input", "output", "profile", "detected", "status",
                  "mojibake_lines", "pair_changes", "pair_label", "custom_changes",
                  "tools_summary", "cps_violations", "error")


def export_csv(path: str, records: Sequence[ConversionRecord]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(_REPORT_FIELDS)
        for rec in records:
            writer.writerow([getattr(rec, name) for name in _REPORT_FIELDS])


def export_html(path: str, records: Sequence[ConversionRecord]) -> None:
    rows = []
    for rec in records:
        color = {"ok": "#d7f5d7", "dry-run": "#fff3c9"}.get(rec.status, "#f5c9c9")
        cells = "".join(f"<td>{_html.escape(str(getattr(rec, name)))}</td>"
                        for name in _REPORT_FIELDS)
        rows.append(f'<tr style="background:{color}">{cells}</tr>')
    header = "".join(f"<th>{name}</th>" for name in _REPORT_FIELDS)
    doc = ("""<!DOCTYPE html><html><head><meta charset="utf-8">"""
           """<title>Subtitle Text Fixer — report</title>"""
           """<style>body{font-family:sans-serif}table{border-collapse:collapse}"""
           """td,th{border:1px solid #999;padding:4px 8px;font-size:13px}</style>"""
           """</head><body><h1>Subtitle Text Fixer — conversion report</h1>"""
           f"<table><tr>{header}</tr>{''.join(rows)}</table></body></html>")
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)


def export_report(path: str, records: Sequence[ConversionRecord]) -> None:
    lower = path.lower()
    if lower.endswith(".html") or lower.endswith(".htm"):
        export_html(path, records)
    elif lower.endswith(".csv"):
        export_csv(path, records)
    else:
        raise ValueError(f"report format not recognized (use .csv or .html): {path}")
