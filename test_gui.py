#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Headless GUI logic tests: drop parsing, language switch, profiles,
custom rules, preview and conversion (no window shown)."""
import os
import sys
import tempfile

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tkinterdnd2 import TkinterDnD  # noqa: E402
from subtitle_fixer import App, parse_drop_paths  # noqa: E402
from fixer_core import PROFILE_ORDER, CustomRule, load_rules, save_rules  # noqa: E402
from i18n import t  # noqa: E402

fails = []


def check(name, cond, detail=""):
    status = "PASS " if cond else "FAIL "
    try:
        print(status + name + ((" -- " + detail) if detail and not cond else ""))
    except UnicodeEncodeError:
        print(status + name.encode("ascii", "backslashreplace").decode("ascii"))
    if not cond:
        fails.append(name)


got = parse_drop_paths("{C:/Films/my film.srt} C:/Films/other.srt")
check("G1 drop parse", got == ["C:/Films/my film.srt", "C:/Films/other.srt"], repr(got))

root = TkinterDnD.Tk()
root.withdraw()
app = App(root)

# Language switch changes visible strings both ways
app.lang = "en"
app.apply_language()
check("G2 english title", app.title_label.cget("text") == t("en", "app_title"))
app.lang = "tr"
app.apply_language()
check("G2 turkish title", app.title_label.cget("text") == t("tr", "app_title"))
check("G2 turkish convert", app.convert_button.cget("text") == t("tr", "btn_convert"))
app.lang = "en"
app.apply_language()

# Regression: switching UI language must not corrupt canonical selections
# (source/profile/target are index-based, not label-parsed).
app.profile_combo.current(PROFILE_ORDER.index("turkish"))
app.lang = "tr"
app.apply_language()
app.lang = "en"
app.apply_language()
check("G2 selections survive language switch",
      app._selected_profile_id() == "turkish"
      and app._selected_source() == "auto"
      and app._selected_target() == "utf-8-sig",
      "{}/{}/{}".format(app._selected_profile_id(), app._selected_source(),
                         app._selected_target()))
app.profile_combo.current(PROFILE_ORDER.index("auto"))

with tempfile.TemporaryDirectory() as tmp:
    broken = (
        "1\n00:02:51,204 --> 00:02:53,161\nYard\u00fdm edin!\n\n"
        "12\n00:04:10,036 --> 00:04:11,945\n\u00dc\u00e7 deyince!\n"
    )
    src = os.path.join(tmp, "film.srt")
    with open(src, "w", encoding="utf-8") as f:
        f.write(broken)

    app.add_path(src)
    check("G3 file added", len(app.files) == 1)

    # Turkish profile preview shows the fix
    app.profile_combo.current(PROFILE_ORDER.index("turkish"))
    app.refresh_preview()
    after = app.text_after.get("1.0", "end")
    check("G3 preview fixed", "Yard\u0131m edin!" in after, repr(after[:120]))

    # Custom rule via the rules list + tree
    app.rules.append(CustomRule(find="edin!", replace="ediniz!", note="polite"))
    app.refresh_rules_tree()
    check("G4 rule in tree", len(app.rules_tree.get_children()) == 1)
    app.custom_var.set(True)
    app.refresh_preview()
    after2 = app.text_after.get("1.0", "end")
    check("G4 rule applied in preview", "ediniz!" in after2, repr(after2[:150]))

    # Toggle off via API (same as double-click)
    app.rules[0].enabled = False
    app.refresh_rules_tree()
    app.refresh_preview()
    after3 = app.text_after.get("1.0", "end")
    check("G4 rule toggle", "ediniz!" not in after3 and "Yard\u0131m" in after3)
    app.rules[0].enabled = True

    # Rules save/load roundtrip
    rp = os.path.join(tmp, "rules.json")
    save_rules(rp, app.rules)
    back = load_rules(rp)
    check("G4 rules file roundtrip", len(back) == 1 and back[0].replace == "ediniz!")

    # Manual pair through the Advanced tab settings
    app.manual_pair_var.set(True)
    app.wrong_var.set("cp1252")
    app.right_var.set("cp1254")
    check("G5 manual pair accessor", app._manual_pair() == ("cp1252", "cp1254"))
    app.manual_pair_var.set(False)

    # Conversion writes fixed output to chosen folder
    outdir = os.path.join(tmp, "out")
    app.same_dir_var.set(False)
    app.output_dir_var.set(outdir)
    app.suffix_var.set("_fixed")
    app.profile_combo.current(PROFILE_ORDER.index("turkish"))
    app.convert_all()
    out = os.path.join(outdir, "film_fixed.srt")
    check("G6 output written", os.path.isfile(out), out)
    with open(out, "rb") as f:
        raw_out = f.read()
    check("G6 output content",
          "Yard\u0131m ediniz!".encode("utf-8") in raw_out
          and "\u00dc\u00e7 deyince!".encode("utf-8") in raw_out,
          repr(raw_out[:200]))

    # Diff highlight tags exist on changed lines
    app.refresh_preview()
    before_tags = app.text_before.tag_ranges("chg")
    after_tags = app.text_after.tag_ranges("chg")
    check("G7 diff tags", len(before_tags) > 0 and len(after_tags) > 0)

    # Per-file profile override
    src2 = os.path.join(tmp, "diger.srt")
    with open(src2, "w", encoding="utf-8") as f:
        f.write("1\n00:00:01,000 --> 00:00:02,000\nYard\u00fdm!\n")
    app.add_path(src2)
    app.file_profiles[src2] = "western"  # western has no 1252->1254 pair
    app._refresh_file_list()
    row = app.file_list.get(1)
    check("G8 row shows override", "Western" in row, repr(row))
    check("G8 effective profile", app._effective_profile(src2) == "western"
          and app._effective_profile(src) == "turkish")
    out2 = os.path.join(outdir, "diger_fixed.srt")
    rec2 = app._convert_one(src2, out2, app._effective_profile(src2))
    with open(out2, "rb") as f:
        raw2 = f.read()
    check("G8 override honored",
          "Yard\u00fdm!".encode("utf-8") in raw2 and rec2.profile == "western",
          repr(raw2[:100]))

    # Tools through the GUI pipeline
    app.strip_hi_var.set(True)
    app.shift_var.set("1500")
    src3 = os.path.join(tmp, "muzik.srt")
    with open(src3, "w", encoding="utf-8") as f:
        f.write("1\n00:00:01,000 --> 00:00:02,000\n[MUSIC]\nHello\n")
    out3 = os.path.join(outdir, "muzik_fixed.srt")
    rec3 = app._convert_one(src3, out3, "auto")
    with open(out3, encoding="utf-8-sig") as f:
        content3 = f.read()
    check("G9 tools in pipeline", "[MUSIC]" not in content3
          and "00:00:02,500 --> 00:00:03,500" in content3, repr(content3[:150]))
    app.strip_hi_var.set(False)
    app.shift_var.set("0")

    # Report export (CSV + HTML)
    csv_out = os.path.join(tmp, "report.csv")
    html_out = os.path.join(tmp, "report.html")
    from subtitle_fixer import export_report as _export
    _export(csv_out, app.records)
    _export(html_out, app.records)
    with open(csv_out, encoding="utf-8-sig") as f:
        first = f.readline()
    check("G10 report files", os.path.isfile(html_out) and first.startswith("input,output"),
          repr(first[:50]))

    # Dry-run writes nothing
    app.dry_run_var.set(True)
    out_dry = os.path.join(outdir, "dry.srt")
    rec_dry = app._convert_one(src, out_dry, "turkish")
    check("G11 dry-run", rec_dry.status == "dry-run" and not os.path.exists(out_dry))
    app.dry_run_var.set(False)

    # Theme toggle + per-theme colors (start from a known theme: a stale
    # config.json from an earlier run must not affect the measurement)
    app.set_theme("light")
    light_bg = app.drop_label.cget("background")
    app.set_theme("dark")
    dark_bg = app.drop_label.cget("background")
    check("G14 theme changes colors", app.theme == "dark" and dark_bg != light_bg,
          f"{light_bg} -> {dark_bg}")
    app.toggle_theme()
    check("G14 theme toggle back", app.theme == "light"
          and app.drop_label.cget("background") == light_bg)
    check("G14 light icon", app.theme_button.cget("text") == "\U0001f319")
    app.set_theme("dark")
    check("G14 dark icon", app.theme_button.cget("text") == "☀")
    app.set_theme("light")

    # No doubled preview headers when the list is empty
    app.clear_list()
    app.refresh_preview()
    after_text = app.text_after.get("1.0", "end")
    header = t(app.lang, "preview_after")
    check("G15 no double header", after_text.count(header) == 1, repr(after_text[:80]))

    # Every UI language renders without errors
    from i18n import SUPPORTED_LANGUAGES as _langs
    rendered_ok = True
    failed_lang = ""
    try:
        for code in _langs:
            app.lang = code
            app.apply_language()
            app.refresh_preview()
            root.update_idletasks()
    except Exception as exc:  # noqa: BLE001
        rendered_ok = False
        failed_lang = f"{code}: {exc}"
    app.lang = "en"
    app.apply_language()
    check("G16 all languages render", rendered_ok, failed_lang)

root.destroy()

# FolderWatcher detects a new file (functional, outside the GUI)
import time as _time
from subtitle_fixer import FolderWatcher as _Watcher
with tempfile.TemporaryDirectory() as wtmp:
    seen_paths = []
    watcher = _Watcher(wtmp, 1.0, seen_paths.append)
    watcher.start()
    _time.sleep(1.2)
    with open(os.path.join(wtmp, "new.srt"), "w", encoding="utf-8") as f:
        f.write("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    _time.sleep(2.6)
    watcher.stop()
    check("G12 watcher detects",
          any(p.endswith("new.srt") for p in seen_paths), repr(seen_paths))

# CLI: dry-run, shift, report, per-file profile, stdin
from subtitle_fixer import cli_main as _cli
with tempfile.TemporaryDirectory() as ctmp:
    csrc = os.path.join(ctmp, "c.srt")
    with open(csrc, "w", encoding="utf-8") as f:
        f.write("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    rc = _cli([csrc, "-o", os.path.join(ctmp, "o"), "--dry-run", "--shift-ms", "700",
               "--file-profile", f"{csrc}:western",
               "--report", os.path.join(ctmp, "r.csv")])
    check("G13 cli dry-run+shift+report",
          rc == 0 and not os.path.exists(os.path.join(ctmp, "o", "c_fixed.srt"))
          and os.path.isfile(os.path.join(ctmp, "r.csv")))
    rc2 = _cli([csrc, "-o", os.path.join(ctmp, "o2"), "--shift-ms", "700"])
    with open(os.path.join(ctmp, "o2", "c_fixed.srt"), encoding="utf-8-sig") as f:
        shifted_out = f.read()
    check("G13 cli shift applied", rc2 == 0 and "00:00:01,700 --> 00:00:02,700" in shifted_out,
          repr(shifted_out[:100]))

import io as _io
_stdin_data = "1\n00:00:01,000 --> 00:00:02,000\nCaf\u00e9\n".encode("utf-8").decode("latin1")
_old_stdin, _old_stdout = sys.stdin, sys.stdout
class _FakeStdin:
    buffer = _io.BytesIO(_stdin_data.encode("utf-8"))
sys.stdin = _FakeStdin()
_captured = _io.StringIO()
sys.stdout = _captured
try:
    rc3 = _cli(["-"])
finally:
    sys.stdin, sys.stdout = _old_stdin, _old_stdout
check("G13 cli stdin", rc3 == 0 and "Caf\u00e9" in _captured.getvalue(),
      repr(_captured.getvalue()[:100]))

# Scrollable pages: small window must still reach every control
root2 = TkinterDnD.Tk()
root2.withdraw()
app2 = App(root2)
check("G17 four scroll pages", len(app2._scroll_pages) == 4)
with tempfile.TemporaryDirectory() as stmp:
    for i in range(25):
        p = os.path.join(stmp, f"f{i:02d}.srt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("1\n00:00:01,000 --> 00:00:02,000\nHi\n")
        app2.add_path(p)
    root2.deiconify()
    root2.geometry("900x600")
    root2.update_idletasks()
    root2.update()
    page, body, canvas, vbar, _window = app2._scroll_pages[0]
    bbox = canvas.bbox("all")
    content_h = (bbox[3] - bbox[1]) if bbox else 0
    check("G17 content overflows small window", content_h > canvas.winfo_height(),
          f"content={content_h} view={canvas.winfo_height()}")
    check("G17 scrollbar appears on overflow", bool(vbar.winfo_ismapped()))

    class _Wheel:
        def __init__(self, widget, delta=0, num=0):
            self.widget = widget
            self.delta = delta
            self.num = num

    before_y = canvas.yview()[0]
    result = app2._on_app_wheel(_Wheel(app2.files_card, delta=-240))
    after_y = canvas.yview()[0]
    check("G17 wheel scrolls page", result == "break" and after_y > before_y,
          f"{before_y} -> {after_y}")

    y_before_text = canvas.yview()[0]
    result_text = app2._on_app_wheel(_Wheel(app2.text_after, delta=-240))
    check("G17 wheel over Text keeps text scroll",
          result_text is None and canvas.yview()[0] == y_before_text)

    # Short tab in a tall window -> scrollbar hides again
    app2.notebook.select(3)  # Advanced tab: tiny content
    root2.geometry("1120x900")
    root2.update_idletasks()
    root2.update()
    _p3, _b3, _c3, _v3, _w3 = app2._scroll_pages[3]
    check("G17 scrollbar hides when everything fits",
          not bool(_v3.winfo_ismapped()))
root2.destroy()
print()
if fails:
    print(f"{len(fails)} GUI TESTS FAILED: {fails}")
    sys.exit(1)
print("ALL GUI TESTS PASSED")
