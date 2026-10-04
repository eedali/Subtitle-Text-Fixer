# Subtitle Text Fixer

[![Build portable app](https://github.com/eedali/Subtitle-Text-Fixer/actions/workflows/build.yml/badge.svg)](https://github.com/eedali/Subtitle-Text-Fixer/actions/workflows/build.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Fix broken subtitle (`.srt`) text for **any language**: drag & drop → pick output → convert.
Output is always clean **UTF-8**. Timestamps (`00:02:51,204 --> 00:02:53,161`) are never touched
by the repair engine (optional timing tools excluded, see below).
The UI itself speaks **7 languages** (English, Türkçe, Deutsch, Français, Español, Русский,
العربية — switcher at the top, auto-detected on first run) and ships with a modern
light/dark theme (moon/sun button in the header, remembered between runs).

## Features

- **Drag & drop batch conversion** — drop many `.srt` files (or whole folders) at once.
- **Before / After preview with diff highlighting** — pink = original, green = fixed.
- **Per-file language profiles** — one list can mix Turkish, Russian, Arabic… files.
- **Universal repair engine** — double-encoded UTF-8 in any script, raw single-byte codepages
  (Windows-125x, ISO-8859-x, KOI8-R…), and text mis-decoded and re-saved.
- **SRT Tools** — shift timings ±ms, fix overlaps, renumber, strip hearing-impaired tags and
  formatting tags, reading-speed (CPS) check.
- **Custom Rules** — your own find → replace list (plain or regex), applied last.
- **Watch folder** — new `.srt` files are converted automatically.
- **Reports & backups** — CSV/HTML conversion reports; no/`.bak`/timestamped-folder backups.
- **Dry run + stdin/stdout** — preview changes without writing; pipe-friendly CLI.
- **Portable build** — single-folder `.exe` via PyInstaller; CI workflow included.

## What it fixes

| # | Corruption | Example |
|---|-----------|---------|
| 1 | File stored in a single-byte codepage but opened as UTF-8 | auto-detected from 18 candidate encodings |
| 2 | Double-encoded UTF-8 ("mojibake"), any script | `CafÃ©→Café`, `fÃ¼r→für`, `niÃ±o→niño`, `ÐŸÑ€Ð¸Ð²ÐµÑ‚→Привет`, `ÎºÎ±Î»Î·Î¼Î­ÏÎ±→Καλημέρα`, `Ù…Ø±Ø­Ø¨Ø§→مرحبا`, `ä¸­æ–‡→中文` |
| 3 | Text mis-decoded and **re-saved** (raw bytes lost), e.g. Turkish Windows-1254 read as Windows-1252 | `Yardým→Yardım`, `Ýmdat→İmdat`, `bacaðým→bacağım` (Turkish profile, auto-triggered) |
| 4 | Anything else | **Custom Rules** + forced codec pair in the Advanced tab |

## How it works

Pipeline order: **mojibake repair → codec reinterpretation → custom rules → SRT tools**.
Line endings are preserved; output defaults to UTF-8 with BOM for maximum compatibility
with TVs and hardware players.

Safety first: a fix that cannot be proven safe is **never applied silently**. Ambiguous cases
(a lone `Ýmdat!`, Icelandic `ð/þ` text, re-saved Cyrillic) only produce a profile *hint* in the
preview — you confirm with one click. Repairs that would introduce letters from scripts
outside common subtitle alphabets are rejected (e.g. the byte collision `Üç` → Syriac `ܜ`).

## Install & run

Requires Python 3.9+ (tkinter ships with the standard Windows Python installer).

```bat
pip install -r requirements.txt
run.bat
```

`requirements.txt` holds the two UI dependencies: `tkinterdnd2` (drag & drop) and `sv-ttk`
(Sun Valley theme). Without them the program still works via the **Add Files** button with
the classic look. Alternatively: `python subtitle_fixer.py`.

No-install option: build once with `build_exe.bat` (or download the CI artifact) and run
`dist\SubtitleTextFixer\SubtitleTextFixer.exe` — same GUI and CLI, portable.

## GUI guide

The Files tab walks you through numbered cards: **1 · Files**, **2 · Preview**,
**3 · Output & convert**.

**1 · Files:**

- Drop `.srt` files onto the blue area (it glows on hover), or **Add Files… / Add Folder…**
  (recursive). Click a file to see its **BEFORE / AFTER** preview with changes highlighted
  (pink = original, green = fixed).
- Mixed languages? Select rows, pick a profile in **Profile for selected**, **Apply** —
  overrides show as `[Türkçe]` etc. next to the filename.
- **Watch folder** row: set watch + output folders and press Start — new `.srt` files
  (stable, recursively found) are converted automatically, mirroring subfolders.

**3 · Output & convert:**

1. Choose **Save to** (default: next to each source file) and a filename suffix
   (`film.srt → film_fixed.srt`; empty suffix keeps the original name).
2. Tune profile / source / target / backups / switches as needed.
3. Press the blue **CONVERT ALL** button. The log reports per file what was repaired.
   **Export report…** saves the run as CSV or HTML.

**Settings on the same tab:**

- **Language profile** — `Auto (universal)` fits most cases; pick yours if the preview
  still looks wrong.
- **Source** — overrides byte decoding when auto-detection guesses wrong.
- **Target** — UTF-8 with BOM (recommended) or without.
- **Backups** — None / `.bak` file / timestamped folder (only matters when overwriting).
- Checkboxes — mojibake repair, codec reinterpretation, custom rules,
  **dry run** (list changes, write nothing).
- **Watch folder** — set watch + output folders and press Start: new `.srt` files
  (stable, recursively found) are converted automatically, mirroring subfolders.

**Tools tab** (all optional, off by default except the read-only CPS check):

- Shift timings ±ms, fix overlapping timestamps, renumber cues from 1,
  remove hearing-impaired tags (`[MUSIC]`, `(laughs)`, `♪`),
  strip formatting tags (`<i>`, `<font>`, `{...}`), reading-speed check with CPS limit.
- Note: modifying tools rewrite cues in canonical SRT format.

**Custom Rules tab:**

Your own replacements, applied **after** the automatic fixes, in list order: Add / Edit /
Delete / reorder / double-click a row to toggle it / Load / Save (JSON). Each rule is
plain text or a regular expression (`use_regex`) with an optional note.
See `custom_rules.example.json` to start. Rules live in `custom_rules.json` next to the
script and auto-load on next start (unless `--no-custom`).

**Advanced tab:**

Force a reinterpretation pair — *"wrongly read as X, actually is Y"*
(e.g. `cp1252 → cp1251` for re-saved Cyrillic). Applies to both Preview and Convert.
CLI equivalent: `--pair cp1252:cp1251`.

## CLI reference

```bat
python subtitle_fixer.py *.srt -o C:\Fixed --lang en
python subtitle_fixer.py film.srt --profile turkish --rules my_rules.json
python subtitle_fixer.py film.srt --pair cp1252:cp1251 --source windows-1251
python subtitle_fixer.py --watch C:\Incoming -o C:\Fixed
python subtitle_fixer.py - < broken.srt > fixed.srt
```

| Option | Meaning |
|---|---|
| `files` | subtitle files (globs allowed); `-` = stdin → stdout |
| `-o, --outdir` | output folder (default: next to each source) |
| `--suffix` | filename suffix (default `_fixed`; empty = overwrite name) |
| `--source` | byte decoding: `auto` (default) or any codec, e.g. `windows-1254` |
| `--target` | `utf-8-sig` (default for files) or `utf-8` |
| `--profile` | global profile: `auto`, `turkish`, `western`, `central`, `cyrillic`, `greek`, `arabic`, `hebrew`, `baltic`, `vietnamese` |
| `--file-profile` | per-file override `FILE:PROFILE`, repeatable |
| `--rules` | custom rules JSON file (default: `custom_rules.json` if present) |
| `--no-mojibake`, `--no-pairs`, `--no-custom` | disable pipeline stages |
| `--pair` | forced pair, e.g. `cp1252:cp1251` |
| `--shift-ms`, `--fix-overlaps`, `--renumber`, `--strip-hi`, `--strip-html` | SRT tools |
| `--no-cps`, `--cps-limit` | reading-speed check (default on, limit 20) |
| `--backup-mode`, `--backup-dir` | `none` (default), `bak`, `folder` |
| `--dry-run` | process everything, write nothing |
| `--report` | write CSV/HTML report (extension decides) |
| `--watch`, `--watch-interval` | watch folder mode (needs `--outdir`) |
| `--lang` | message language: `auto` (default), `tr`, `en` |

## Files

| File | Purpose |
|---|---|
| `subtitle_fixer.py` | GUI + CLI (all identifiers in English) |
| `fixer_core.py` | Repair engine: detection, profiles, rules, reports, backups (stdlib only) |
| `srt_tools.py` | SRT cue tools (shift/overlap/renumber/strip/CPS) |
| `i18n.py` | UI strings (EN, TR, DE, FR, ES, RU, AR) |
| `test_core.py`, `test_gui.py` | Test suites |
| `examples/` | Broken demo files per language |
| `custom_rules.example.json` | Starter custom rules |
| `run.bat`, `build_exe.bat`, `requirements.txt` | Launcher, portable-build script, UI deps (drag & drop, theme) |
| `subtitle_fixer.spec`, `.github/workflows/build.yml` | PyInstaller spec, CI build (Win/macOS/Linux) |

`config.json` (UI language, theme, last settings incl. tools) and `custom_rules.json`
are created next to the script on first use and are git-ignored. Migrating from the old
Turkish-only version: `srt_duzeltici.py → subtitle_fixer.py`, `calistir.bat → run.bat`,
`ornekler/ → examples/`, `srt_fix.py → fixer_core.py`.

## Tests

```bat
python test_core.py
python test_gui.py
```

86 checks: 7-script mojibake roundtrips, Turkish auto-trigger, Icelandic/Cyrillic safety
gates, byte-collision guard (`Üç`), raw codepages, manual pairs, custom rules, SRT tools
(shift/overlap/renumber/strip/CPS), dry-run, both backup modes, CSV/HTML reports, folder
scan, i18n key + placeholder parity across 7 languages, an EN↔TR language-switch regression
test, diff highlighting, per-file profiles, watch-folder detection, report export, theme
toggle + per-theme colors, no-doubled-header check, all-language render pass, and
end-to-end conversion (GUI + CLI + stdin + packaged `.exe` verified).

## Limitations

- Files already saved with `�` (U+FFFD replacement characters) lost the original letters and
  cannot be recovered — re-download those instead.
- Ambiguous single-byte files (valid in two codepages with no distinctive markers) need a
  manual profile/source choice; use the preview to decide.
- Deliberately out of scope: shell context-menu integration and statistical
  (`charset-normalizer`) detection — explicit profile/source override covers those cases.
