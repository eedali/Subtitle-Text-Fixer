#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Core engine tests: Turkish + French + German + Spanish + Russian + Greek
+ Arabic + Chinese mojibake, raw codepages, manual pairs, custom rules, i18n."""
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fixer_core import (
    CustomRule,
    apply_custom_rules,
    decode_raw_bytes,
    fix_bytes,
    fix_text,
    load_rules,
    process_file,
    reinterpret,
    repair_utf8_mojibake,
    save_rules,
    suggest_profile,
    validate_rule,
)
from i18n import detect_system_language, t

fails = []


def check(name, cond, detail=""):
    status = "PASS " if cond else "FAIL "
    try:
        print(status + name + ((" -- " + detail) if detail and not cond else ""))
    except UnicodeEncodeError:
        print(status + name.encode("ascii", "backslashreplace").decode("ascii"))
    if not cond:
        fails.append(name)


def mojibaked(correct: str) -> str:
    """Simulate UTF-8 bytes misread as Latin-1 and re-saved as UTF-8."""
    return correct.encode("utf-8").decode("latin1")


# --- 1) Turkish single-char corruption, auto profile suggestion ------------
tr_broken = (
    "1\n00:02:51,204 --> 00:02:53,161\nYard\u00fdm edin!\n\n"
    "8\n00:03:44,343 --> 00:03:49,135\n\u00ddmdat! Biri bana yard\u00fdm etsin!\n\n"
    "9\n00:03:57,022 --> 00:03:59,727\nAh, baca\u00f0\u00fdm!\n\n"
    "12\n00:04:10,036 --> 00:04:11,945\n\u00dc\u00e7 deyince!\n"
)
fixed, rep = fix_text(tr_broken, "auto")
check("T1 tr Yardim", "Yard\u0131m edin!" in fixed, repr(fixed[:100]))
check("T1 tr suggested profile", rep.suggested_profile == "turkish", repr(rep.suggested_profile))
check("T1 tr Imdat", "\u0130mdat!" in fixed, repr(fixed[:200]))
check("T1 tr bacagim", "baca\u011f\u0131m!" in fixed, repr(fixed))

# --- 2..8) Double-encoded UTF-8 roundtrips, all scripts --------------------
cases = {
    "french": "Caf\u00e9 cr\u00e8me br\u00fbl\u00e9e na\u00efve",
    "german": "f\u00fcr Gr\u00fc\u00dfe \u00c4pfel \u00d6l \u00dcbung Stra\u00dfe",
    "spanish": "ni\u00f1o \u00a1Hola! \u00bfQu\u00e9 tal? \u00f1and\u00fa",
    "russian": "\u041f\u0440\u0438\u0432\u0435\u0442 \u043c\u0438\u0440",
    "greek": "\u039a\u03b1\u03bb\u03b7\u03bc\u03ad\u03c1\u03b1 \u03ba\u03cc\u03c3\u03bc\u03b5",
    "arabic": "\u0645\u0631\u062d\u0628\u0627 \u0628\u0627\u0644\u0639\u0627\u0644\u0645",
    "chinese": "\u4e2d\u6587\u6d4b\u8bd5 \u5b57\u5e55",
}
for name, correct in cases.items():
    broken = "1\n00:00:01,000 --> 00:00:02,000\n" + mojibaked(correct) + "\n"
    out, r = fix_text(broken, "auto")
    check(f"T2 {name} roundtrip", correct in out, repr(out[:120]))

# --- 9) Legit Western text must pass through untouched ----------------------
legit = "1\n00:00:01,000 --> 00:00:02,000\ncaf\u00e9 \u00e0 la carte \u00c4pfel \u00d6l\n"
out, r = fix_text(legit, "auto")
check("T3 legit western untouched",
      "caf\u00e9 \u00e0 la carte \u00c4pfel \u00d6l" in out and r.mojibake_lines == 0,
      repr(out[:120]))

# Byte collisions that LOOK like mojibake but are correct text ("Üç" = DC E7,
# valid UTF-8 for Syriac ܜ) must survive.
tr_clean = "1\n00:00:01,000 --> 00:00:02,000\n\u00dc\u00e7 de\u011fi\u015fince a\u011fa\u00e7 \u00d6l\u00e7\u00fc\n"
out, r = fix_text(tr_clean, "auto")
check("T3 clean turkish untouched",
      "\u00dc\u00e7 de\u011fi\u015fince a\u011fa\u00e7 \u00d6l\u00e7\u00fc" in out
      and r.mojibake_lines == 0 and r.pair_changes == 0,
      repr(out[:120]))

# --- 10) Raw single-byte files ---------------------------------------------
raw_tr = ("Yard\u0131m \u0130mdat \u011f\u015f\u00e7\u00f6\u00fc\n").encode("windows-1254")
out, rep = fix_bytes(raw_tr, "auto", "auto")
check("T4 raw 1254 auto", "Yard\u0131m \u0130mdat" in out, repr(out[:80]) + " " + rep.detected_encoding)

raw_ru = ("\u041f\u0440\u0438\u0432\u0435\u0442\n").encode("windows-1251")
out, rep = fix_bytes(raw_ru, "auto", "cyrillic")
check("T4 raw 1251 cyrillic profile", "\u041f\u0440\u0438\u0432\u0435\u0442" in out, repr(out[:60]))

# --- 11) Re-saved Cyrillic restored with manual pair ------------------------
ru = "\u041f\u0440\u0438\u0432\u0435\u0442"
ru_resaved = ru.encode("cp1251").decode("cp1252")
out_auto, _ = fix_text(ru_resaved, "auto")
check("T5 resaved cyrillic not mangled by auto", out_auto == ru_resaved, repr(out_auto[:60]))
out_pair, n = reinterpret(ru_resaved, "cp1252", "cp1251")
check("T5 manual pair restores cyrillic", ru in out_pair, repr(out_pair[:60]))

# --- 12) Custom rules --------------------------------------------------------
rules = [
    CustomRule(find="teh", replace="the"),
    CustomRule(find="  +", replace=" ", use_regex=True, note="collapse spaces"),
    CustomRule(find="skipme", replace="X", enabled=False),
    CustomRule(find="([", replace="X", use_regex=True),  # invalid -> skipped
]
out, n = apply_custom_rules("teh  cat skipme", rules)
check("T6 custom rules", out == "the cat skipme" and n == 2, repr(out))
check("T6 invalid regex reported", validate_rule(rules[3]) is not None)
check("T6 empty find reported", validate_rule(CustomRule(find="", replace="x")) == "empty-find")

# --- 13) Rules file roundtrip -------------------------------------------------
with tempfile.TemporaryDirectory() as tmp:
    rp = os.path.join(tmp, "rules.json")
    save_rules(rp, [CustomRule(find="a", replace="b", note="n")])
    back = load_rules(rp)
    check("T7 rules roundtrip",
          len(back) == 1 and back[0].find == "a" and back[0].replace == "b",
          repr(back))

# --- 14) End-to-end file, timestamps + line endings ---------------------------
with tempfile.TemporaryDirectory() as tmp:
    src = os.path.join(tmp, "in.srt")
    dst = os.path.join(tmp, "out", "in_fixed.srt")
    with open(src, "w", encoding="utf-8", newline="") as f:
        f.write(tr_broken.replace("\n", "\r\n"))
    rep = process_file(src, dst, profile_id="auto")
    with open(dst, "rb") as f:
        raw_out = f.read()
    check("T8 timestamp kept", b"00:02:51,204 --> 00:02:53,161" in raw_out)
    check("T8 crlf kept", b"\r\n" in raw_out)
    check("T8 bom written", raw_out.startswith(b"\xef\xbb\xbf"))
    check("T8 content fixed", "Yard\u0131m".encode("utf-8") in raw_out)

# --- 15) Profile suggester ----------------------------------------------------
check("T9 suggest none for clean",
      suggest_profile("Hello world, caf\u00e9.") is None)
check("T9 suggest turkish", suggest_profile("Yard\u00fdm") == "turkish")

# --- 16) Safety gates ---------------------------------------------------------
# Single marker alone: reported as possible, NOT auto-applied.
out, rep = fix_text("\u00ddmdat!", "auto")
check("T9 single marker untouched", out == "\u00ddmdat!", repr(out))
check("T9 single marker hinted", rep.possible_profile == "turkish"
      and rep.suggested_profile is None, repr(rep))
# ...but the explicit profile fixes it.
out, rep = fix_text("\u00ddmdat!", "turkish")
check("T9 explicit profile fixes", out == "\u0130mdat!", repr(out))
# Icelandic-style text with ð/þ must survive auto mode.
icelandic = "1\n00:00:01,000 --> 00:00:02,000\nMa\u00f0ur \u00feakka \u00fej\u00f3\u00f0\n"
out, rep = fix_text(icelandic, "auto")
check("T9 icelandic untouched",
      "Ma\u00f0ur \u00feakka \u00fej\u00f3\u00f0" in out and rep.pair_changes == 0,
      repr(out[:100]))

# --- 16) i18n ------------------------------------------------------------------
check("T10 tr string", t("tr", "btn_convert") == "\u25b6  T\u00dcM\u00dcN\u00dc D\u00d6N\u00dc\u015eT\u00dcR")
check("T10 en string", t("en", "btn_convert") == "\u25b6  CONVERT ALL")
check("T10 fallback", t("tr", "no-such-key") == "no-such-key")
check("T10 detect", detect_system_language() in ("tr", "en"))
check("T10 decode label", decode_raw_bytes("plain ascii".encode(), "auto")[1] == "UTF-8")

# --- 16b) i18n parity: every language has every key + same placeholders -----
import re as _re
from i18n import STRINGS as _STRINGS, SUPPORTED_LANGUAGES as _LANGS
_base_keys = set(_STRINGS["en"])
_parity_ok = all(set(_STRINGS[l]) == _base_keys for l in _LANGS)
_ph_ok = all(
    set(_re.findall(r"{(\w+)}", _STRINGS["en"][k]))
    == set(_re.findall(r"{(\w+)}", _STRINGS[l][k]))
    for l in _LANGS for k in _base_keys)
check("T10 i18n key parity", _parity_ok and len(_LANGS) >= 7,
      f"langs={len(_LANGS)} keys={len(_base_keys)}")
check("T10 i18n placeholders", _ph_ok)

# --- 17) SRT tools ------------------------------------------------------------
import srt_tools as _st

cue_doc = ("1\n00:00:01,000 --> 00:00:03,000\nHello <i>world</i>\n\n"
           "5\n00:00:02,500 --> 00:00:04,000\n[MUSIC]\nSecond line\n\n"
           "9\n00:00:05,000 --> 00:00:06,000\n(laughs)\n")
cues = _st.parse_srt(cue_doc)
check("T11 parse cues", len(cues) == 3 and cues[0].start_ms == 1000 and cues[0].end_ms == 3000,
      repr([(c.index, c.start_ms) for c in cues]))
check("T11 timestamp fmt", _st.format_timestamp(3723004) == "01:02:03,004")

shifted = _st.parse_srt(cue_doc)
_st.shift_cues(shifted, 500)
check("T11 shift", shifted[0].start_ms == 1500 and shifted[0].end_ms == 3500)

overlapped = _st.parse_srt(cue_doc)
n = _st.fix_overlaps(overlapped)
check("T11 overlaps", n == 1 and overlapped[0].end_ms == 2499, repr(overlapped[0].end_ms))

_st.renumber_cues(cues)
check("T11 renumber", [c.index for c in cues] == [1, 2, 3])

out, rep = _st.apply_tools(cue_doc, _st.ToolOptions(strip_hi=True, strip_html=True,
                                                   renumber=True, check_cps=False))
check("T11 strip hi+html", "<i>" not in out and "[MUSIC]" not in out
      and "(laughs)" not in out and "world" in out and "Second line" in out,
      repr(out[:160]))
check("T11 strip counts", rep.hi_removed == 2 and rep.html_removed == 2 and rep.modified,
      repr(rep))

fast = "1\n00:00:01,000 --> 00:00:01,200\n" + "word " * 20 + "\n"
_, rep2 = _st.apply_tools(fast, _st.ToolOptions(check_cps=True, cps_limit=20.0))
check("T11 cps violation", rep2.cps_violations == [1], repr(rep2.cps_violations))
_, rep3 = _st.apply_tools(fast, _st.ToolOptions(check_cps=False))
check("T11 cps off", rep3.cps_violations == [] and not rep3.modified)

# --- 18) File pipeline: tools, dry-run, backups, reports ----------------------
from fixer_core import (ConversionRecord, export_report, record_from, scan_folder)
import srt_tools as _st2

with tempfile.TemporaryDirectory() as tmp:
    srt_in = os.path.join(tmp, "a.srt")
    with open(srt_in, "w", encoding="utf-8") as f:
        f.write("1\n00:00:01,000 --> 00:00:03,000\nHi <i>there</i>\n")

    # tools through process_file
    out1 = os.path.join(tmp, "o1.srt")
    rep = process_file(srt_in, out1, tools=_st2.ToolOptions(shift_ms=1000, strip_html=True))
    with open(out1, encoding="utf-8-sig") as f:
        content = f.read()
    check("T12 shift+strip in file", "00:00:02,000 --> 00:00:04,000" in content
          and "<i>" not in content and rep.tools is not None and rep.tools.modified,
          repr(content[:120]))

    # dry-run writes nothing
    out_dry = os.path.join(tmp, "dry.srt")
    rep_dry = process_file(srt_in, out_dry, dry_run=True)
    check("T12 dry-run", not os.path.exists(out_dry) and rep_dry.dry_run)

    # .bak backup on overwrite
    rep_bak = process_file(srt_in, srt_in, backup_mode="bak")
    check("T12 bak backup", os.path.isfile(srt_in + ".bak"))

    # timestamped folder backup on overwrite
    bdir = os.path.join(tmp, "bkroot")
    rep_f = process_file(srt_in, srt_in, backup_mode="folder", backup_dir=bdir)
    buds = []
    for dp, _dn, fn in os.walk(os.path.join(bdir, "backups")):
        buds.extend(fn)
    check("T12 folder backup", len(buds) == 1 and buds[0] == "a.srt", repr(buds))

    # records + export
    rec = record_from(srt_in, out1, "auto", rep, "ok")
    csv_path = os.path.join(tmp, "rep.csv")
    html_path = os.path.join(tmp, "rep.html")
    export_report(csv_path, [rec])
    export_report(html_path, [rec])
    with open(csv_path, encoding="utf-8-sig") as f:
        header = f.readline()
    with open(html_path, encoding="utf-8") as f:
        html_doc = f.read()
    check("T12 csv header", header.startswith("input,output,profile"), repr(header[:60]))
    check("T12 html table", "<table>" in html_doc and "a.srt" in html_doc)
    try:
        export_report(os.path.join(tmp, "rep.txt"), [rec])
        check("T12 bad ext rejected", False)
    except ValueError:
        check("T12 bad ext rejected", True)

    # folder scan
    os.makedirs(os.path.join(tmp, "sub", "deep"))
    open(os.path.join(tmp, "sub", "b.srt"), "w").write("x")
    open(os.path.join(tmp, "sub", "deep", "c.srt"), "w").write("x")
    open(os.path.join(tmp, "sub", "note.txt"), "w").write("x")
    found = scan_folder(tmp)
    check("T12 scan folder",
          any(p.endswith(os.path.join("sub", "b.srt")) for p in found)
          and any(p.endswith(os.path.join("deep", "c.srt")) for p in found)
          and not any(p.endswith("note.txt") for p in found)
          and all(p.endswith(".srt") for p in found),
          repr(found))

print()
if fails:
    print(f"{len(fails)} TESTS FAILED: {fails}")
    sys.exit(1)
print("ALL CORE TESTS PASSED")
