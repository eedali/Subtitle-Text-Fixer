#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Subtitle Text Fixer — drag & drop GUI + batch CLI.

GUI:
    python subtitle_fixer.py
CLI:
    python subtitle_fixer.py film1.srt film2.srt
    python subtitle_fixer.py *.srt -o C:\\Fixed --profile turkish
    python subtitle_fixer.py *.srt --rules my_rules.json --pair cp1252:cp1251
    python subtitle_fixer.py --watch C:\\Incoming -o C:\\Fixed
    python subtitle_fixer.py - < broken.srt > fixed.srt
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from fixer_core import (
    PROFILE_ORDER,
    ConversionRecord,
    CustomRule,
    apply_custom_rules,  # noqa: F401 (re-exported for tools/tests)
    export_report,
    fix_bytes,
    get_profile,
    load_rules,
    process_file,
    record_from,
    save_rules,
    scan_folder,
    suggest_output_path,
    validate_rule,
)
from srt_tools import ToolOptions
from i18n import LANGUAGE_NAMES, SUPPORTED_LANGUAGES, detect_system_language, t

try:
    import sv_ttk
    HAS_THEME = True
except ImportError:
    HAS_THEME = False

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False

APP_TITLE = "Subtitle Text Fixer"
__version__ = "2.0.0"
PREVIEW_LINES = 80
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
DEFAULT_RULES_PATH = os.path.join(BASE_DIR, "custom_rules.json")

SOURCE_CODECS = [
    "utf-8", "windows-1250", "windows-1251", "windows-1252",
    "windows-1253", "windows-1254", "windows-1255", "windows-1256",
    "windows-1257", "windows-1258", "iso-8859-1", "iso-8859-2",
    "iso-8859-5", "iso-8859-6", "iso-8859-7", "iso-8859-8",
    "iso-8859-9", "iso-8859-13", "iso-8859-15", "koi8-r", "latin-1",
]
PAIR_CODECS = [
    "cp1252", "latin1", "cp1254", "cp1250", "cp1251", "cp1253",
    "cp1255", "cp1256", "cp1257", "cp1258", "koi8-r", "mac_roman",
]
BACKUP_MODES = ("none", "bak", "folder")

# Custom (non-ttk) widget colors per theme. ttk widgets follow sv-ttk.
PALETTES = {
    "light": {
        "drop_bg": "#e8f0fe", "drop_fg": "#1a3a5f", "drop_hot": "#d7e6ff",
        "list_bg": "#ffffff", "list_fg": "#161616",
        "select_bg": "#cfe0ff", "select_fg": "#161616",
        "before_bg": "#fef4f4", "before_fg": "#2b1212", "before_chg": "#ffc9c9",
        "after_bg": "#f0faf0", "after_fg": "#122b12", "after_chg": "#b9e8b9",
        "cap_before_fg": "#b42318", "cap_after_fg": "#1c7a2e",
        "legend_fg": "#555555",
    },
    "dark": {
        "drop_bg": "#1f2a3d", "drop_fg": "#cfe0ff", "drop_hot": "#2a3a55",
        "list_bg": "#2b2b2b", "list_fg": "#f0f0f0",
        "select_bg": "#3a5a8c", "select_fg": "#ffffff",
        "before_bg": "#3a2424", "before_fg": "#f5e6e6", "before_chg": "#7a2f2f",
        "after_bg": "#1f3a26", "after_fg": "#e6f5e6", "after_chg": "#2f7a3d",
        "cap_before_fg": "#ff9d94", "cap_after_fg": "#8fe39a",
        "legend_fg": "#b0b0b0",
    },
}

DEFAULT_CONFIG = {
    "ui_lang": None,  # None -> system language
    "theme": "light",    "profile": "auto",
    "source": "auto",
    "target": "utf-8-sig",
    "output_dir": "",
    "same_dir": True,
    "suffix": "_fixed",
    "use_mojibake": True,
    "use_pairs": True,
    "use_custom": True,
    "backup_mode": "none",
    "backup_dir": "",
    "dry_run": False,
    "use_manual_pair": False,
    "manual_wrong": "cp1252",
    "manual_right": "cp1251",
    "rules_path": DEFAULT_RULES_PATH,
    "watch_dir": "",
    "watch_interval": 5,
    "tools": {
        "shift_ms": 0,
        "fix_overlaps": False,
        "renumber": False,
        "strip_hi": False,
        "strip_html": False,
        "check_cps": True,
        "cps_limit": 20.0,
    },
}


def load_config() -> dict:
    import copy
    config = copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            stored = json.load(f)
        if isinstance(stored, dict):
            for key in DEFAULT_CONFIG:
                if key in stored:
                    config[key] = stored[key]
            # Migrate the old boolean "backup" flag.
            if "backup_mode" not in stored and stored.get("backup") is True:
                config["backup_mode"] = "bak"
            if isinstance(config.get("tools"), dict):
                merged = dict(DEFAULT_CONFIG["tools"])
                merged.update({k: v for k, v in config["tools"].items()
                               if k in merged})
                config["tools"] = merged
    except (OSError, ValueError):
        pass
    if config.get("ui_lang") not in SUPPORTED_LANGUAGES:
        config["ui_lang"] = detect_system_language()
    if config.get("profile") not in PROFILE_ORDER:
        config["profile"] = "auto"
    if config.get("backup_mode") not in BACKUP_MODES:
        config["backup_mode"] = "none"
    return config


def save_config(config: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def parse_drop_paths(data: str):
    """Split TkinterDnD's '{path with spaces} {other}' file list."""
    paths = []
    for match in re.finditer(r"\{([^}]+)\}|(\S+)", data):
        path = match.group(1) if match.group(1) is not None else match.group(2)
        path = path.strip()
        if path:
            paths.append(path)
    return paths


class FolderWatcher:
    """Poll a folder for new/changed .srt files; stable files trigger callback."""

    def __init__(self, watch_dir: str, interval: float, callback):
        self.watch_dir = os.path.abspath(watch_dir)
        self.interval = max(1.0, float(interval))
        self.callback = callback
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen: dict[str, tuple[int, float]] = {}
        self._pending: dict[str, tuple[int, float]] = {}

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _snapshot(self) -> dict[str, tuple[int, float]]:
        snap: dict[str, tuple[int, float]] = {}
        try:
            entries = scan_folder(self.watch_dir)
        except OSError:
            return snap
        for path in entries:
            try:
                stat = os.stat(path)
            except OSError:
                continue
            snap[path] = (stat.st_size, stat.st_mtime)
        return snap

    def _loop(self):
        while not self._stop.is_set():
            try:
                current = self._snapshot()
            except Exception:  # noqa: BLE001
                current = {}
            for path, marker in current.items():
                previous = self._seen.get(path)
                if previous != marker:
                    # Require stability across two polls (copy may be in progress).
                    if self._pending.get(path) == marker:
                        self._seen[path] = marker
                        self._pending.pop(path, None)
                        try:
                            self.callback(path)
                        except Exception:  # noqa: BLE001
                            pass
                    else:
                        self._pending[path] = marker
                else:
                    self._pending.pop(path, None)
            for gone in [p for p in self._seen if p not in current]:
                self._seen.pop(gone, None)
                self._pending.pop(gone, None)
            self._stop.wait(self.interval)


class RuleDialog:
    """Add/Edit dialog for a single custom rule."""

    def __init__(self, parent: tk.Tk, lang: str, rule: CustomRule | None = None):
        self.lang = lang
        self.result: CustomRule | None = None
        self.top = tk.Toplevel(parent)
        self.top.title(t(lang, "dlg_edit_rule" if rule else "dlg_add_rule"))
        self.top.transient(parent)
        self.top.grab_set()
        self.top.resizable(False, False)

        rule = rule or CustomRule(find="", replace="")
        self.find_var = tk.StringVar(value=rule.find)
        self.replace_var = tk.StringVar(value=rule.replace)
        self.regex_var = tk.BooleanVar(value=rule.use_regex)
        self.enabled_var = tk.BooleanVar(value=rule.enabled)
        self.note_var = tk.StringVar(value=rule.note)

        frame = ttk.Frame(self.top, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=t(lang, "fld_find")).grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(frame, textvariable=self.find_var, width=46).grid(row=0, column=1, pady=2)
        ttk.Label(frame, text=t(lang, "fld_replace")).grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(frame, textvariable=self.replace_var, width=46).grid(row=1, column=1, pady=2)
        ttk.Label(frame, text=t(lang, "fld_note")).grid(row=2, column=0, sticky="w", pady=2)
        ttk.Entry(frame, textvariable=self.note_var, width=46).grid(row=2, column=1, pady=2)
        ttk.Checkbutton(frame, text=t(lang, "fld_regex"),
                        variable=self.regex_var).grid(row=3, column=1, sticky="w", pady=2)
        ttk.Checkbutton(frame, text=t(lang, "fld_enabled"),
                        variable=self.enabled_var).grid(row=4, column=1, sticky="w", pady=2)

        buttons = ttk.Frame(frame)
        buttons.grid(row=5, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(buttons, text=t(lang, "btn_ok"),
                   command=self._on_ok).pack(side="left", padx=4)
        ttk.Button(buttons, text=t(lang, "btn_cancel"),
                   command=self.top.destroy).pack(side="left", padx=4)

        self.top.bind("<Return>", lambda _e: self._on_ok())
        self.top.bind("<Escape>", lambda _e: self.top.destroy())
        parent.wait_window(self.top)

    def _on_ok(self):
        candidate = CustomRule(
            find=self.find_var.get(), replace=self.replace_var.get(),
            enabled=self.enabled_var.get(), use_regex=self.regex_var.get(),
            note=self.note_var.get(),
        )
        error = validate_rule(candidate)
        if error == "empty-find":
            messagebox.showwarning(self.top.title(), t(self.lang, "err_empty_find"))
            return
        if error and error.startswith("invalid-regex"):
            messagebox.showwarning(
                self.top.title(),
                t(self.lang, "err_invalid_regex", error=error.split(":", 1)[1]))
            return
        self.result = candidate
        self.top.destroy()


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.config = load_config()
        self.lang = self.config["ui_lang"]
        self.theme = self.config.get("theme", "light")
        if self.theme not in PALETTES:
            self.theme = "light"
        self.rules: list[CustomRule] = []
        self.files: list[str] = []
        self.file_profiles: dict[str, str] = {}  # path -> profile id override
        self.records: list[ConversionRecord] = []
        self.watcher: FolderWatcher | None = None
        self._scroll_pages: list = []  # (page, body, canvas, vbar, window)
        self._load_initial_rules()

        self._apply_base_theme()
        root.title(f"{APP_TITLE} {__version__}")
        root.geometry("1120x860")
        root.minsize(880, 620)

        self._build_ui()
        self._sync_widgets_from_config()
        self.apply_language()
        self._apply_theme_colors()
        self._log(t(self.lang, "log_ready"))

    # ------------------------------------------------------------------ theme
    def _apply_base_theme(self):
        if HAS_THEME:
            try:
                if self.theme == "dark":
                    sv_ttk.use_dark_theme()
                else:
                    sv_ttk.use_light_theme()
            except Exception:  # noqa: BLE001
                pass

    def set_theme(self, mode: str):
        if mode not in PALETTES:
            return
        self.theme = mode
        self._apply_base_theme()
        self._apply_theme_colors()
        self.theme_button.configure(text="☀" if self.theme == "dark" else "🌙")
        self._persist()

    def toggle_theme(self):
        self.set_theme("dark" if self.theme == "light" else "light")

    def _apply_theme_colors(self):
        pal = PALETTES[self.theme]
        self.drop_label.configure(background=pal["drop_bg"], foreground=pal["drop_fg"])
        self.file_list.configure(background=pal["list_bg"], foreground=pal["list_fg"],
                                 selectbackground=pal["select_bg"],
                                 selectforeground=pal["select_fg"])
        self.text_before.configure(background=pal["before_bg"], foreground=pal["before_fg"],
                                   insertbackground=pal["before_fg"])
        self.text_after.configure(background=pal["after_bg"], foreground=pal["after_fg"],
                                  insertbackground=pal["after_fg"])
        self.text_before.tag_configure("chg", background=pal["before_chg"])
        self.text_after.tag_configure("chg", background=pal["after_chg"])
        self.caption_before.configure(foreground=pal["cap_before_fg"])
        self.caption_after.configure(foreground=pal["cap_after_fg"])
        self.diff_legend.configure(foreground=pal["legend_fg"])
        for _page, _body, canvas, _vbar, _window in self._scroll_pages:
            try:
                canvas.configure(background=self._page_bg())
            except Exception:  # noqa: BLE001
                pass

    def _page_bg(self) -> str:
        try:
            bg = ttk.Style().lookup("TFrame", "background")
            if bg:
                return str(bg)
        except Exception:  # noqa: BLE001
            pass
        return "#2b2b2b" if self.theme == "dark" else "#f5f5f5"

    # ------------------------------------------------------- scrollable pages
    def _make_scrollable_page(self):
        """Notebook page whose content scrolls when taller than the window.

        Returns (page, body): page goes to the notebook, widgets go in body.
        The scrollbar only appears when content overflows. Mouse-wheel scrolls
        the page, except over Text/Listbox/Treeview which keep their own scroll.
        """
        page = ttk.Frame(self.notebook)
        bg = self._page_bg()
        canvas = tk.Canvas(page, highlightthickness=0, borderwidth=0,
                           background=bg)
        vbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=lambda _f, _l, bar=vbar: None)
        canvas.pack(side="left", fill="both", expand=True)
        # vbar packed on demand by _refresh_scroll
        body = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=body, anchor="nw")

        def _refresh(_event=None):
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
                canvas.itemconfigure(window, width=canvas.winfo_width())
                need = body.winfo_reqheight() > canvas.winfo_height() + 1
                mapped = bool(vbar.winfo_ismapped())
                if need and not mapped:
                    vbar.pack(side="right", fill="y")
                    canvas.configure(yscrollcommand=vbar.set)
                elif not need and mapped:
                    vbar.pack_forget()
                    canvas.yview_moveto(0)
            except Exception:  # noqa: BLE001
                pass

        body.bind("<Configure>", _refresh)
        canvas.bind("<Configure>", _refresh)
        self._scroll_pages.append([page, body, canvas, vbar, window])
        return page, body

    def _on_app_wheel(self, event):
        """App-wide wheel: scroll the notebook page under the cursor."""
        try:
            widget = event.widget
            wclass = widget.winfo_class()
        except Exception:  # noqa: BLE001
            return None
        if wclass in ("Text", "Listbox", "Treeview"):
            return None  # these scroll themselves
        try:
            path = str(widget)
        except Exception:  # noqa: BLE001
            return None
        for _page, body, canvas, _vbar, _window in self._scroll_pages:
            try:
                if path.startswith(str(body)) and widget.winfo_viewable():
                    delta = getattr(event, "delta", 0)
                    if delta:
                        steps = int(delta / 120) if abs(delta) >= 120 else (
                            1 if delta > 0 else -1)
                        canvas.yview_scroll(-steps, "units")
                    elif getattr(event, "num", 0) in (4, 5):
                        canvas.yview_scroll(-1 if event.num == 4 else 1, "units")
                    else:
                        return None
                    return "break"
            except Exception:  # noqa: BLE001
                return None
        return None

    # ------------------------------------------------------------------ setup
    def _load_initial_rules(self):
        path = self.config.get("rules_path") or DEFAULT_RULES_PATH
        if os.path.isfile(path):
            try:
                self.rules = load_rules(path)
            except (OSError, ValueError):
                self.rules = []

    def _profile_ids(self) -> list[str]:
        return list(PROFILE_ORDER)

    def _profile_labels(self) -> list[str]:
        return [t(self.lang, f"profile_{pid}") for pid in self._profile_ids()]

    # ------------------------------------------------------------------ UI build
    def _build_ui(self):
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=12, pady=(12, 0))
        self.title_label = ttk.Label(header, font=("Segoe UI", 17, "bold"))
        self.title_label.pack(side="left")
        controls = ttk.Frame(header)
        controls.pack(side="right")
        self.theme_button = ttk.Button(controls, width=3, command=self.toggle_theme)
        self.theme_button.pack(side="left", padx=(0, 8))
        self.theme_button.configure(text="☀" if self.theme == "dark" else "🌙")
        self.lang_label = ttk.Label(controls)
        self.lang_label.pack(side="left", padx=(0, 4))
        self.lang_var = tk.StringVar(value=LANGUAGE_NAMES[self.lang])
        self.lang_combo = ttk.Combobox(controls, textvariable=self.lang_var,
                                       values=[LANGUAGE_NAMES[c] for c in SUPPORTED_LANGUAGES],
                                       state="readonly", width=12)
        self.lang_combo.pack(side="left")
        self.lang_combo.bind("<<ComboboxSelected>>", self._on_language_changed)

        self.subtitle_label = ttk.Label(self.root, font=("Segoe UI", 10))
        self.subtitle_label.pack(anchor="w", padx=12, pady=(0, 6))

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=12, pady=4)
        self.root.bind_all("<MouseWheel>", self._on_app_wheel)
        self.root.bind_all("<Button-4>", self._on_app_wheel)
        self.root.bind_all("<Button-5>", self._on_app_wheel)

        self.tab_files, _files_body = self._make_scrollable_page()
        self.tab_rules, _rules_body = self._make_scrollable_page()
        self.tab_tools, _tools_body = self._make_scrollable_page()
        self.tab_advanced, _advanced_body = self._make_scrollable_page()
        self.notebook.add(self.tab_files, text="files")
        self.notebook.add(self.tab_rules, text="rules")
        self.notebook.add(self.tab_tools, text="tools")
        self.notebook.add(self.tab_advanced, text="advanced")

        self._build_files_tab(_files_body)
        self._build_rules_tab(_rules_body)
        self._build_tools_tab(_tools_body)
        self._build_advanced_tab(_advanced_body)

        log_frame = ttk.Frame(self.root)
        log_frame.pack(fill="both", expand=False, padx=12, pady=(0, 10))
        self.log_caption = ttk.Label(log_frame)
        self.log_caption.pack(anchor="w")
        self.log_text = tk.Text(log_frame, height=5, wrap="word", font=("Consolas", 9),
                                background="#111111", foreground="#d7ffd7",
                                relief="solid", borderwidth=1)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    def _build_files_tab(self, parent):
        self.files_card = ttk.LabelFrame(parent, text="", padding=12)
        self.files_card.pack(fill="x", padx=12, pady=(10, 4))

        self.drop_label = tk.Label(self.files_card, font=("Segoe UI", 12, "bold"),
                                   relief="solid", borderwidth=1,
                                   justify="center", pady=20)
        self.drop_label.pack(fill="x")
        if HAS_DND:
            self.drop_label.drop_target_register(DND_FILES)
            self.drop_label.dnd_bind("<<Drop>>", self._on_drop)
            self.drop_label.dnd_bind("<<DropEnter>>", lambda _e: self._drop_hot(True))
            self.drop_label.dnd_bind("<<DropLeave>>", lambda _e: self._drop_hot(False))

        toolbar = ttk.Frame(self.files_card)
        toolbar.pack(fill="x", pady=(12, 0))
        self.add_button = ttk.Button(toolbar, command=self.add_files_dialog)
        self.add_button.pack(side="left", padx=(0, 6))
        self.add_folder_button = ttk.Button(toolbar, command=self.add_folder_dialog)
        self.add_folder_button.pack(side="left", padx=6)
        self.remove_button = ttk.Button(toolbar, command=self.remove_selected)
        self.remove_button.pack(side="left", padx=6)
        self.clear_button = ttk.Button(toolbar, command=self.clear_list)
        self.clear_button.pack(side="left", padx=6)
        self.count_label = ttk.Label(toolbar, text="0")
        self.count_label.pack(side="right")

        profile_row = ttk.Frame(self.files_card)
        profile_row.pack(fill="x", pady=(8, 0))
        self.per_file_caption = ttk.Label(profile_row)
        self.per_file_caption.pack(side="left")
        self.per_file_var = tk.StringVar()
        self.per_file_combo = ttk.Combobox(profile_row, textvariable=self.per_file_var,
                                           state="readonly", width=24)
        self.per_file_combo.pack(side="left", padx=6)
        self.apply_profile_button = ttk.Button(profile_row, command=self.apply_profile_to_selected)
        self.apply_profile_button.pack(side="left", padx=6)

        list_frame = ttk.Frame(self.files_card)
        list_frame.pack(fill="both", expand=False, pady=(8, 0))
        self.file_list = tk.Listbox(list_frame, height=6, selectmode="extended",
                                    font=("Consolas", 10), relief="solid", borderwidth=1,
                                    activestyle="none")
        self.file_list.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.file_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.file_list.configure(yscrollcommand=scrollbar.set)
        self.file_list.bind("<<ListboxSelect>>", lambda _e: self.refresh_preview())

        watch_row = ttk.Frame(self.files_card)
        watch_row.pack(fill="x", pady=(8, 0))
        self.watch_caption = ttk.Label(watch_row)
        self.watch_caption.pack(side="left")
        self.watch_dir_var = tk.StringVar()
        ttk.Entry(watch_row, textvariable=self.watch_dir_var, width=34).pack(side="left", padx=6)
        self.watch_browse_button = ttk.Button(watch_row, command=self.browse_watch_dir)
        self.watch_browse_button.pack(side="left")
        self.watch_interval_caption = ttk.Label(watch_row)
        self.watch_interval_caption.pack(side="left", padx=(10, 0))
        self.watch_interval_var = tk.StringVar()
        ttk.Spinbox(watch_row, from_=2, to=120, textvariable=self.watch_interval_var,
                    width=5).pack(side="left", padx=4)
        self.watch_seconds_caption = ttk.Label(watch_row)
        self.watch_seconds_caption.pack(side="left")
        self.watch_start_button = ttk.Button(watch_row, command=self.toggle_watch)
        self.watch_start_button.pack(side="left", padx=10)
        self.watch_status = ttk.Label(watch_row, text="")
        self.watch_status.pack(side="left", padx=6)

        self.preview_card = ttk.LabelFrame(parent, text="", padding=12)
        self.preview_card.pack(fill="both", expand=True, padx=12, pady=4)
        self.preview_caption = ttk.Label(self.preview_card, font=("Segoe UI", 10, "bold"))
        self.preview_caption.pack(anchor="w")
        caption_row = ttk.Frame(self.preview_card)
        caption_row.pack(fill="x", pady=(6, 4))
        self.caption_before = ttk.Label(caption_row, font=("Segoe UI", 10, "bold"))
        self.caption_before.pack(side="left", fill="x", expand=True)
        self.caption_after = ttk.Label(caption_row, font=("Segoe UI", 10, "bold"))
        self.caption_after.pack(side="left", fill="x", expand=True)
        preview_frame = ttk.Frame(self.preview_card)
        preview_frame.pack(fill="both", expand=True)
        self.text_before = tk.Text(preview_frame, height=8, wrap="none",
                                   font=("Consolas", 10),
                                   relief="solid", borderwidth=1)
        self.text_after = tk.Text(preview_frame, height=8, wrap="none",
                                  font=("Consolas", 10),
                                  relief="solid", borderwidth=1)
        self.text_before.pack(side="left", fill="both", expand=True, padx=(0, 4))
        self.text_after.pack(side="left", fill="both", expand=True, padx=(4, 0))
        self.diff_legend = ttk.Label(self.preview_card, font=("Segoe UI", 9, "italic"))
        self.diff_legend.pack(anchor="w", pady=(6, 0))

        self.output_card = ttk.LabelFrame(parent, text="", padding=12)
        self.output_card.pack(fill="x", padx=12, pady=(4, 10))
        self.settings_box = self.output_card  # alias kept for apply_language()

        row1 = ttk.Frame(self.output_card)
        row1.pack(fill="x", pady=2)
        self.outdir_caption = ttk.Label(row1, width=14, anchor="e")
        self.outdir_caption.pack(side="left")
        self.output_dir_var = tk.StringVar()
        self.outdir_entry = ttk.Entry(row1, textvariable=self.output_dir_var)
        self.outdir_entry.pack(side="left", padx=8, fill="x", expand=True)
        self.browse_button = ttk.Button(row1, command=self.browse_output_dir)
        self.browse_button.pack(side="left")
        self.same_dir_var = tk.BooleanVar(value=True)
        self.same_dir_check = ttk.Checkbutton(row1, variable=self.same_dir_var,
                                              command=self._toggle_outdir)
        self.same_dir_check.pack(side="left", padx=(12, 0))

        row2 = ttk.Frame(self.output_card)
        row2.pack(fill="x", pady=2)
        self.suffix_caption = ttk.Label(row2, width=14, anchor="e")
        self.suffix_caption.pack(side="left")
        self.suffix_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.suffix_var, width=12).pack(side="left", padx=8)
        self.suffix_hint = ttk.Label(row2)
        self.suffix_hint.pack(side="left", padx=(0, 16))
        self.profile_caption = ttk.Label(row2, anchor="e")
        self.profile_caption.pack(side="left")
        self.profile_var = tk.StringVar()
        self.profile_combo = ttk.Combobox(row2, textvariable=self.profile_var,
                                          state="readonly", width=22)
        self.profile_combo.pack(side="left", padx=8)
        self.profile_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_settings_changed())
        self.backup_caption = ttk.Label(row2, anchor="e")
        self.backup_caption.pack(side="left", padx=(16, 0))
        self.backup_var = tk.StringVar()
        self.backup_combo = ttk.Combobox(row2, textvariable=self.backup_var,
                                         state="readonly", width=20)
        self.backup_combo.pack(side="left", padx=8)
        self.backup_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_settings_changed())

        row3 = ttk.Frame(self.output_card)
        row3.pack(fill="x", pady=2)
        self.source_caption = ttk.Label(row3, width=14, anchor="e")
        self.source_caption.pack(side="left")
        self.source_var = tk.StringVar()
        self.source_combo = ttk.Combobox(row3, textvariable=self.source_var,
                                         state="readonly", width=20)
        self.source_combo.pack(side="left", padx=8)
        self.source_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_settings_changed())
        self.target_caption = ttk.Label(row3, anchor="e")
        self.target_caption.pack(side="left", padx=(16, 0))
        self.target_var = tk.StringVar()
        self.target_combo = ttk.Combobox(row3, textvariable=self.target_var,
                                         state="readonly", width=34)
        self.target_combo.pack(side="left", padx=8)
        self.target_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_settings_changed())

        switch_style = "Switch.TCheckbutton" if HAS_THEME else "TCheckbutton"
        row4 = ttk.Frame(self.output_card)
        row4.pack(fill="x", pady=(6, 2))
        self.mojibake_var = tk.BooleanVar(value=True)
        self.pairs_var = tk.BooleanVar(value=True)
        self.custom_var = tk.BooleanVar(value=True)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.mojibake_check = ttk.Checkbutton(row4, variable=self.mojibake_var,
                                              command=self._on_settings_changed,
                                              style=switch_style)
        self.pairs_check = ttk.Checkbutton(row4, variable=self.pairs_var,
                                           command=self._on_settings_changed,
                                           style=switch_style)
        self.custom_check = ttk.Checkbutton(row4, variable=self.custom_var,
                                            command=self._on_settings_changed,
                                            style=switch_style)
        self.dry_run_check = ttk.Checkbutton(row4, variable=self.dry_run_var,
                                             command=self._on_settings_changed,
                                             style=switch_style)
        self.mojibake_check.pack(side="left", padx=(0, 16))
        self.pairs_check.pack(side="left", padx=16)
        self.custom_check.pack(side="left", padx=16)
        self.dry_run_check.pack(side="left", padx=16)

        convert_row = ttk.Frame(self.output_card)
        convert_row.pack(fill="x", pady=(10, 0))
        self.convert_button = ttk.Button(convert_row, command=self.convert_all_threaded,
                                         style="Accent.TButton")
        self.convert_button.pack(side="left", ipadx=20, ipady=6)
        self.report_button = ttk.Button(convert_row, command=self.export_report_dialog)
        self.report_button.pack(side="left", padx=12)
        self.progress = ttk.Progressbar(convert_row, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(12, 0))

    def _drop_hot(self, hot: bool):
        pal = PALETTES[self.theme]
        self.drop_label.configure(
            background=pal["drop_hot"] if hot else pal["drop_bg"])

    def _build_rules_tab(self, parent):
        pad = {"padx": 12, "pady": 6}
        self.rules_hint = ttk.Label(parent, wraplength=980, justify="left")
        self.rules_hint.pack(anchor="w", **pad)

        tree_frame = ttk.Frame(parent)
        tree_frame.pack(fill="both", expand=True, **pad)
        self.rules_tree = ttk.Treeview(tree_frame, columns=("use", "find", "replace", "kind", "note"),
                                       show="headings", height=12)
        self.rules_tree.pack(side="left", fill="both", expand=True)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical",
                                    command=self.rules_tree.yview)
        tree_scroll.pack(side="right", fill="y")
        self.rules_tree.configure(yscrollcommand=tree_scroll.set)
        self.rules_tree.bind("<Double-1>", lambda _e: self.toggle_rule())
        self.rules_tree.bind("<MouseWheel>",
                             lambda e: self._tree_wheel(e, -1 * (int(e.delta / 120) or (1 if e.delta > 0 else -1))))
        self.rules_tree.bind("<Button-4>", lambda _e: self._tree_wheel(None, -1))
        self.rules_tree.bind("<Button-5>", lambda _e: self._tree_wheel(None, 1))

        rules_buttons = ttk.Frame(parent)
        rules_buttons.pack(fill="x", **pad)
        self.rule_add_button = ttk.Button(rules_buttons, command=self.add_rule)
        self.rule_add_button.pack(side="left", padx=(0, 4))
        self.rule_edit_button = ttk.Button(rules_buttons, command=self.edit_rule)
        self.rule_edit_button.pack(side="left", padx=4)
        self.rule_delete_button = ttk.Button(rules_buttons, command=self.delete_rule)
        self.rule_delete_button.pack(side="left", padx=4)
        self.rule_up_button = ttk.Button(rules_buttons, command=lambda: self.move_rule(-1))
        self.rule_up_button.pack(side="left", padx=4)
        self.rule_down_button = ttk.Button(rules_buttons, command=lambda: self.move_rule(1))
        self.rule_down_button.pack(side="left", padx=4)
        self.rule_load_button = ttk.Button(rules_buttons, command=self.load_rules_dialog)
        self.rule_load_button.pack(side="right", padx=4)
        self.rule_save_button = ttk.Button(rules_buttons, command=self.save_rules_dialog)
        self.rule_save_button.pack(side="right", padx=4)

    def _build_tools_tab(self, parent):
        pad = {"padx": 12, "pady": 4}
        self.tools_note = ttk.Label(parent, wraplength=980, justify="left")
        self.tools_note.pack(anchor="w", **pad)

        timing_card = ttk.Frame(parent, padding=4)
        timing_card.pack(fill="x", padx=8)
        shift_row = ttk.Frame(timing_card)
        shift_row.pack(fill="x", **pad)
        self.shift_caption = ttk.Label(shift_row, width=24, anchor="e")
        self.shift_caption.pack(side="left")
        self.shift_var = tk.StringVar(value="0")
        ttk.Spinbox(shift_row, from_=-3600000, to=3600000, increment=100,
                    textvariable=self.shift_var, width=12,
                    command=self._on_settings_changed).pack(side="left", padx=8)
        self.shift_var.trace_add("write", lambda *_a: self._on_settings_changed())

        self.overlaps_var = tk.BooleanVar(value=False)
        self.renumber_var = tk.BooleanVar(value=False)
        self.strip_hi_var = tk.BooleanVar(value=False)
        self.strip_html_var = tk.BooleanVar(value=False)
        self.cps_var = tk.BooleanVar(value=True)
        switch_style = "Switch.TCheckbutton" if HAS_THEME else "TCheckbutton"
        self.overlaps_check = ttk.Checkbutton(timing_card, variable=self.overlaps_var,
                                              command=self._on_settings_changed,
                                              style=switch_style)
        self.renumber_check = ttk.Checkbutton(timing_card, variable=self.renumber_var,
                                              command=self._on_settings_changed,
                                              style=switch_style)
        self.overlaps_check.pack(anchor="w", **pad)
        self.renumber_check.pack(anchor="w", **pad)

        cleanup_card = ttk.Frame(parent, padding=4)
        cleanup_card.pack(fill="x", padx=8)
        self.strip_hi_check = ttk.Checkbutton(cleanup_card, variable=self.strip_hi_var,
                                              command=self._on_settings_changed,
                                              style=switch_style)
        self.strip_html_check = ttk.Checkbutton(cleanup_card, variable=self.strip_html_var,
                                                command=self._on_settings_changed,
                                                style=switch_style)
        self.cps_check = ttk.Checkbutton(cleanup_card, variable=self.cps_var,
                                         command=self._on_settings_changed,
                                         style=switch_style)
        self.strip_hi_check.pack(anchor="w", **pad)
        self.strip_html_check.pack(anchor="w", **pad)
        self.cps_check.pack(anchor="w", **pad)

        cps_row = ttk.Frame(cleanup_card)
        cps_row.pack(fill="x", **pad)
        self.cps_limit_caption = ttk.Label(cps_row, width=24, anchor="e")
        self.cps_limit_caption.pack(side="left")
        self.cps_limit_var = tk.StringVar(value="20")
        ttk.Spinbox(cps_row, from_=5, to=60, textvariable=self.cps_limit_var, width=8,
                    command=self._on_settings_changed).pack(side="left", padx=8)
        self.cps_limit_var.trace_add("write", lambda *_a: self._on_settings_changed())

    def _build_advanced_tab(self, parent):
        pad = {"padx": 12, "pady": 8}
        self.adv_hint = ttk.Label(parent, wraplength=980, justify="left")
        self.adv_hint.pack(anchor="w", **pad)
        pair_row = ttk.Frame(parent)
        pair_row.pack(fill="x", **pad)
        self.wrong_caption = ttk.Label(pair_row)
        self.wrong_caption.pack(side="left")
        self.wrong_var = tk.StringVar()
        ttk.Combobox(pair_row, textvariable=self.wrong_var, values=PAIR_CODECS,
                     state="readonly", width=16).pack(side="left", padx=6)
        self.right_caption = ttk.Label(pair_row)
        self.right_caption.pack(side="left")
        self.right_var = tk.StringVar()
        ttk.Combobox(pair_row, textvariable=self.right_var, values=PAIR_CODECS,
                     state="readonly", width=16).pack(side="left", padx=6)
        self.manual_pair_var = tk.BooleanVar(value=False)
        self.manual_pair_check = ttk.Checkbutton(pair_row, variable=self.manual_pair_var,
                                                 command=self._on_settings_changed)
        self.manual_pair_check.pack(side="left", padx=12)

    # ------------------------------------------------------------------ config
    def _sync_widgets_from_config(self):
        self.output_dir_var.set(self.config.get("output_dir", ""))
        self.same_dir_var.set(bool(self.config.get("same_dir", True)))
        self.suffix_var.set(self.config.get("suffix", "_fixed"))
        self.mojibake_var.set(bool(self.config.get("use_mojibake", True)))
        self.pairs_var.set(bool(self.config.get("use_pairs", True)))
        self.custom_var.set(bool(self.config.get("use_custom", True)))
        self.dry_run_var.set(bool(self.config.get("dry_run", False)))
        self.manual_pair_var.set(bool(self.config.get("use_manual_pair", False)))
        self.wrong_var.set(self.config.get("manual_wrong", "cp1252"))
        self.right_var.set(self.config.get("manual_right", "cp1251"))
        self.watch_dir_var.set(self.config.get("watch_dir", ""))
        self.watch_interval_var.set(str(self.config.get("watch_interval", 5)))
        tools_cfg = self.config.get("tools", {})
        self.shift_var.set(str(tools_cfg.get("shift_ms", 0)))
        self.overlaps_var.set(bool(tools_cfg.get("fix_overlaps", False)))
        self.renumber_var.set(bool(tools_cfg.get("renumber", False)))
        self.strip_hi_var.set(bool(tools_cfg.get("strip_hi", False)))
        self.strip_html_var.set(bool(tools_cfg.get("strip_html", False)))
        self.cps_var.set(bool(tools_cfg.get("check_cps", True)))
        self.cps_limit_var.set(str(tools_cfg.get("cps_limit", 20)))
        # Seed selection indices from stored canonical IDs (display labels
        # are rendered by apply_language()).
        profile = self.config.get("profile", "auto")
        self.profile_combo.configure(values=self._profile_labels())
        self.profile_combo.current(
            self._profile_ids().index(profile) if profile in self._profile_ids() else 0)
        self.profile_var.set(self.profile_combo.get())
        self.per_file_combo.configure(values=self._per_file_labels())
        self.per_file_combo.current(0)
        self.per_file_var.set(self.per_file_combo.get())
        source = self.config.get("source", "auto")
        self.source_combo.configure(values=[t(self.lang, "src_auto")] + SOURCE_CODECS)
        self.source_combo.current(
            1 + SOURCE_CODECS.index(source) if source in SOURCE_CODECS else 0)
        self.source_var.set(self.source_combo.get())
        self.target_combo.configure(
            values=[t(self.lang, "dst_sig"), t(self.lang, "dst_nosig")])
        self.target_combo.current(
            0 if self.config.get("target", "utf-8-sig") == "utf-8-sig" else 1)
        self.target_var.set(self.target_combo.get())
        backup_mode = self.config.get("backup_mode", "none")
        self.backup_combo.configure(values=self._backup_labels())
        self.backup_combo.current(
            BACKUP_MODES.index(backup_mode) if backup_mode in BACKUP_MODES else 0)
        self.backup_var.set(self.backup_combo.get())
        self._toggle_outdir()

    def _per_file_labels(self) -> list[str]:
        return [t(self.lang, "profile_global")] + self._profile_labels()

    def _backup_labels(self) -> list[str]:
        return [t(self.lang, f"backup_{mode}") for mode in BACKUP_MODES]

    def _collect_config(self) -> dict:
        return {
            "ui_lang": self.lang,
            "theme": self.theme,
            "profile": self._selected_profile_id(),
            "source": self._selected_source(),
            "target": self._selected_target(),
            "output_dir": self.output_dir_var.get(),
            "same_dir": self.same_dir_var.get(),
            "suffix": self.suffix_var.get(),
            "use_mojibake": self.mojibake_var.get(),
            "use_pairs": self.pairs_var.get(),
            "use_custom": self.custom_var.get(),
            "backup_mode": self._selected_backup_mode(),
            "backup_dir": self.config.get("backup_dir", ""),
            "dry_run": self.dry_run_var.get(),
            "use_manual_pair": self.manual_pair_var.get(),
            "manual_wrong": self.wrong_var.get(),
            "manual_right": self.right_var.get(),
            "rules_path": self.config.get("rules_path", DEFAULT_RULES_PATH),
            "watch_dir": self.watch_dir_var.get(),
            "watch_interval": self._watch_interval(),
            "tools": {
                "shift_ms": self._shift_ms(),
                "fix_overlaps": self.overlaps_var.get(),
                "renumber": self.renumber_var.get(),
                "strip_hi": self.strip_hi_var.get(),
                "strip_html": self.strip_html_var.get(),
                "check_cps": self.cps_var.get(),
                "cps_limit": self._cps_limit(),
            },
        }

    def _persist(self):
        self.config = self._collect_config()
        save_config(self.config)

    # ------------------------------------------------------------------ language
    def _on_language_changed(self, _event=None):
        index = self.lang_combo.current()
        if 0 <= index < len(SUPPORTED_LANGUAGES):
            self.lang = SUPPORTED_LANGUAGES[index]
        self.lang_var.set(LANGUAGE_NAMES[self.lang])
        self._persist()
        self.apply_language()

    def apply_language(self):
        lang = self.lang
        self.root.title(f"{APP_TITLE} {__version__} — {t(lang, 'app_title')}")
        self.title_label.configure(text=t(lang, "app_title"))
        self.subtitle_label.configure(text=t(lang, "app_subtitle"))
        self.lang_label.configure(text=t(lang, "lang_label"))
        self.notebook.tab(self.tab_files, text=t(lang, "tab_files"))
        self.notebook.tab(self.tab_rules, text=t(lang, "tab_rules"))
        self.notebook.tab(self.tab_tools, text=t(lang, "tab_tools"))
        self.notebook.tab(self.tab_advanced, text=t(lang, "tab_advanced"))

        self.drop_label.configure(
            text=t(lang, "drop_dnd") if HAS_DND else t(lang, "drop_no_dnd"))
        self.add_button.configure(text=t(lang, "btn_add"))
        self.add_folder_button.configure(text=t(lang, "btn_add_folder"))
        self.remove_button.configure(text=t(lang, "btn_remove"))
        self.clear_button.configure(text=t(lang, "btn_clear"))
        self._update_count()

        self.per_file_caption.configure(text=t(lang, "profile_for_selected"))
        self.per_file_combo.configure(values=self._per_file_labels())
        self.apply_profile_button.configure(text=t(lang, "btn_apply_profile"))

        self.watch_caption.configure(text=t(lang, "watch_label"))
        self.watch_browse_button.configure(text=t(lang, "btn_browse"))
        self.watch_interval_caption.configure(text=t(lang, "watch_interval"))
        self.watch_seconds_caption.configure(text=t(lang, "watch_seconds"))
        self._refresh_watch_button()

        self.preview_caption.configure(text=t(lang, "preview_title"))
        self.caption_before.configure(text=t(lang, "preview_before"))
        self.caption_after.configure(text=t(lang, "preview_after"))
        self.files_card.configure(text=t(lang, "step_files"))
        self.preview_card.configure(text=t(lang, "step_preview"))
        self.settings_box.configure(text=t(lang, "step_output"))
        self.diff_legend.configure(text=t(lang, "diff_legend"))
        self.outdir_caption.configure(text=t(lang, "outdir_label"))
        self.browse_button.configure(text=t(lang, "btn_browse"))
        self.same_dir_check.configure(text=t(lang, "same_dir"))
        self.suffix_caption.configure(text=t(lang, "suffix_label"))
        self.suffix_hint.configure(text=t(lang, "suffix_hint"))
        self.profile_caption.configure(text=t(lang, "profile_label"))
        self.backup_caption.configure(text=t(lang, "backup_label"))
        self.source_caption.configure(text=t(lang, "source_label"))
        self.target_caption.configure(text=t(lang, "target_label"))
        self.mojibake_check.configure(text=t(lang, "opt_mojibake"))
        self.pairs_check.configure(text=t(lang, "opt_pairs"))
        self.custom_check.configure(text=t(lang, "opt_custom"))
        self.dry_run_check.configure(text=t(lang, "opt_dryrun"))
        self.convert_button.configure(text=t(lang, "btn_convert"))
        self.report_button.configure(text=t(lang, "btn_export_report"))
        self.log_caption.configure(text=t(lang, "log_title"))

        current_profile = self._selected_profile_id()
        self.profile_combo.configure(values=self._profile_labels())
        self.profile_combo.current(self._profile_ids().index(current_profile))
        self.profile_var.set(self.profile_combo.get())

        current_per_file = self.per_file_combo.current()
        self.per_file_combo.configure(values=self._per_file_labels())
        self.per_file_combo.current(
            current_per_file if current_per_file >= 0 else 0)
        self.per_file_var.set(self.per_file_combo.get())

        current_source = self._selected_source()
        self.source_combo.configure(values=[t(lang, "src_auto")] + SOURCE_CODECS)
        self.source_combo.current(
            1 + SOURCE_CODECS.index(current_source) if current_source in SOURCE_CODECS else 0)
        self.source_var.set(self.source_combo.get())

        current_target = self._selected_target()
        self.target_combo.configure(values=[t(lang, "dst_sig"), t(lang, "dst_nosig")])
        self.target_combo.current(0 if current_target == "utf-8-sig" else 1)
        self.target_var.set(self.target_combo.get())

        current_backup = self._selected_backup_mode()
        self.backup_combo.configure(values=self._backup_labels())
        self.backup_combo.current(BACKUP_MODES.index(current_backup))
        self.backup_var.set(self.backup_combo.get())

        self.rules_hint.configure(text=t(lang, "rules_hint"))
        for col, key in (("use", "col_use"), ("find", "col_find"),
                         ("replace", "col_replace"), ("kind", "col_type"),
                         ("note", "col_note")):
            self.rules_tree.heading(col, text=t(lang, key))
        self.rules_tree.column("use", width=50, anchor="center")
        self.rules_tree.column("find", width=200)
        self.rules_tree.column("replace", width=200)
        self.rules_tree.column("kind", width=80, anchor="center")
        self.rules_tree.column("note", width=220)
        self.rule_add_button.configure(text=t(lang, "rules_add"))
        self.rule_edit_button.configure(text=t(lang, "rules_edit"))
        self.rule_delete_button.configure(text=t(lang, "rules_delete"))
        self.rule_up_button.configure(text=t(lang, "rules_up"))
        self.rule_down_button.configure(text=t(lang, "rules_down"))
        self.rule_load_button.configure(text=t(lang, "rules_load"))
        self.rule_save_button.configure(text=t(lang, "rules_save"))
        self.refresh_rules_tree()

        self.tools_note.configure(text=t(lang, "tools_note"))
        self.shift_caption.configure(text=t(lang, "tools_shift_label"))
        self.overlaps_check.configure(text=t(lang, "opt_overlaps"))
        self.renumber_check.configure(text=t(lang, "opt_renumber"))
        self.strip_hi_check.configure(text=t(lang, "opt_strip_hi"))
        self.strip_html_check.configure(text=t(lang, "opt_strip_html"))
        self.cps_check.configure(text=t(lang, "opt_cps"))
        self.cps_limit_caption.configure(text=t(lang, "cps_limit_label"))

        self.adv_hint.configure(text=t(lang, "adv_hint"))
        self.wrong_caption.configure(text=t(lang, "adv_wrong_label"))
        self.right_caption.configure(text=t(lang, "adv_right_label"))
        self.manual_pair_check.configure(text=t(lang, "adv_use_pair"))

        self._refresh_file_list()
        self.refresh_preview()

    # ------------------------------------------------------------------ accessors
    # Canonical values are derived from combobox SELECTION INDEX, never by
    # parsing localized labels (labels change with UI language; indices don't).
    def _selected_profile_id(self) -> str:
        index = self.profile_combo.current()
        ids = self._profile_ids()
        if 0 <= index < len(ids):
            return ids[index]
        label = self.profile_var.get()
        for pid in ids:
            if label in (t(self.lang, f"profile_{pid}"), t("en", f"profile_{pid}"),
                         t("tr", f"profile_{pid}")):
                return pid
        return "auto"

    def _effective_profile(self, path: str) -> str:
        return self.file_profiles.get(path, self._selected_profile_id())

    def _selected_source(self) -> str:
        index = self.source_combo.current()
        if index <= 0:
            return "auto"
        if 1 <= index <= len(SOURCE_CODECS):
            return SOURCE_CODECS[index - 1]
        value = self.source_var.get()
        return value if value and value not in (
            t("en", "src_auto"), t("tr", "src_auto")) else "auto"

    def _selected_target(self) -> str:
        return "utf-8" if self.target_combo.current() == 1 else "utf-8-sig"

    def _selected_backup_mode(self) -> str:
        index = self.backup_combo.current()
        if 0 <= index < len(BACKUP_MODES):
            return BACKUP_MODES[index]
        return "none"

    def _shift_ms(self) -> int:
        try:
            return int(float(self.shift_var.get()))
        except (TypeError, ValueError):
            return 0

    def _cps_limit(self) -> float:
        try:
            value = float(self.cps_limit_var.get())
            return value if value > 0 else 20.0
        except (TypeError, ValueError):
            return 20.0

    def _watch_interval(self) -> float:
        try:
            value = float(self.watch_interval_var.get())
            return min(120.0, max(2.0, value))
        except (TypeError, ValueError):
            return 5.0

    def _manual_pair(self):
        if not self.manual_pair_var.get():
            return None
        wrong, right = self.wrong_var.get(), self.right_var.get()
        if wrong and right:
            return (wrong, right)
        return None

    def _active_rules(self):
        return self.rules if self.custom_var.get() else []

    def _tool_options(self) -> ToolOptions:
        return ToolOptions(
            shift_ms=self._shift_ms(),
            fix_overlaps=self.overlaps_var.get(),
            renumber=self.renumber_var.get(),
            strip_hi=self.strip_hi_var.get(),
            strip_html=self.strip_html_var.get(),
            check_cps=self.cps_var.get(),
            cps_limit=self._cps_limit(),
        )

    # ------------------------------------------------------------------ files
    def _on_drop(self, event):
        for path in parse_drop_paths(event.data):
            self.add_path(path)

    def add_files_dialog(self):
        paths = filedialog.askopenfilenames(title=t(self.lang, "add_title"),
                                            filetypes=[("Subtitles", "*.srt"),
                                                       ("All files", "*.*")])
        for path in paths:
            self.add_path(path)

    def add_folder_dialog(self):
        folder = filedialog.askdirectory(title=t(self.lang, "btn_add_folder"))
        if not folder:
            return
        for path in scan_folder(folder):
            self.add_path(path)

    def add_path(self, path: str):
        path = os.path.abspath(path.strip().strip('"'))
        if not os.path.isfile(path):
            self._log(t(self.lang, "log_skipped", path=path))
            return
        if path not in self.files:
            self.files.append(path)
            self._refresh_file_list()
            self._update_count()
            self.refresh_preview()
        self._log(t(self.lang, "log_added", name=os.path.basename(path)))

    def _row_text(self, path: str) -> str:
        text = f"\u2022 {os.path.basename(path)}  —  {os.path.dirname(path)}"
        override = self.file_profiles.get(path)
        if override:
            text += f"  [{t(self.lang, f'profile_{override}')}]"
        return text

    def _refresh_file_list(self):
        selection = {self.files[i] for i in self.file_list.curselection()} if self.files else set()
        self.file_list.delete(0, "end")
        for path in self.files:
            self.file_list.insert("end", self._row_text(path))
        for index, path in enumerate(self.files):
            if path in selection:
                self.file_list.selection_set(index)

    def apply_profile_to_selected(self):
        index = self.per_file_combo.current()
        if index < 0:
            return
        chosen = "global" if index == 0 else self._profile_ids()[index - 1]
        for list_index in self.file_list.curselection():
            path = self.files[list_index]
            if chosen == "global":
                self.file_profiles.pop(path, None)
            else:
                self.file_profiles[path] = chosen
        self._refresh_file_list()
        self.refresh_preview()

    def remove_selected(self):
        for index in reversed(self.file_list.curselection()):
            path = self.files.pop(index)
            self.file_profiles.pop(path, None)
        self._refresh_file_list()
        self._update_count()
        self.refresh_preview()

    def clear_list(self):
        self.files.clear()
        self.file_profiles.clear()
        self.file_list.delete(0, "end")
        self._update_count()
        self.refresh_preview()

    def _update_count(self):
        self.count_label.configure(text=t(self.lang, "files_count", n=len(self.files)))

    def browse_output_dir(self):
        folder = filedialog.askdirectory(title=t(self.lang, "browse_title"))
        if folder:
            self.output_dir_var.set(folder)
            self.same_dir_var.set(False)
            self._toggle_outdir()
            self._persist()

    def browse_watch_dir(self):
        folder = filedialog.askdirectory(title=t(self.lang, "watch_label"))
        if folder:
            self.watch_dir_var.set(folder)
            self._persist()

    def _toggle_outdir(self):
        state = "disabled" if self.same_dir_var.get() else "normal"
        self.outdir_entry.configure(state=state)

    def _on_settings_changed(self, _event=None):
        self._persist()
        self.refresh_preview()

    # ------------------------------------------------------------------ watch
    def _refresh_watch_button(self):
        running = self.watcher is not None and self.watcher.running
        self.watch_start_button.configure(
            text=t(self.lang, "btn_watch_stop") if running else t(self.lang, "btn_watch_start"))
        if not running and not self.watch_status.cget("text").startswith("\u25cf"):
            self.watch_status.configure(text="")

    def toggle_watch(self):
        if self.watcher is not None and self.watcher.running:
            self.watcher.stop()
            self.watcher = None
            self.watch_status.configure(text=t(self.lang, "watch_stopped"))
            self._refresh_watch_button()
            return
        watch_dir = self.watch_dir_var.get().strip()
        output_dir = self.output_dir_var.get().strip()
        if not watch_dir or self.same_dir_var.get() or not output_dir:
            messagebox.showwarning(APP_TITLE, t(self.lang, "watch_need_dirs"))
            return
        os.makedirs(output_dir, exist_ok=True)
        self._persist()
        self.watcher = FolderWatcher(watch_dir, self._watch_interval(), self._on_watched_file)
        self.watcher.start()
        self.watch_status.configure(
            text="\u25cf " + t(self.lang, "watch_running", dir=watch_dir))
        self._refresh_watch_button()
        self._log(t(self.lang, "watch_running", dir=watch_dir))

    def _watch_output_path(self, path: str, watch_dir: str, output_dir: str) -> str:
        relative = os.path.relpath(path, watch_dir)
        base = os.path.basename(relative)
        stem, ext = os.path.splitext(base)
        ext = ext or ".srt"
        suffix = self.suffix_var.get()
        target_dir = os.path.join(output_dir, os.path.dirname(relative))
        os.makedirs(target_dir, exist_ok=True)
        return os.path.join(target_dir, f"{stem}{suffix}{ext}" if suffix else base)

    def _on_watched_file(self, path: str):
        try:
            output_dir = os.path.abspath(self.output_dir_var.get().strip())
        except Exception:  # noqa: BLE001
            return
        if os.path.abspath(path).startswith(output_dir + os.sep):
            return  # never re-process our own output
        watch_dir = self.watch_dir_var.get().strip()
        self._log(t(self.lang, "watch_detected", name=os.path.basename(path)))
        output = self._watch_output_path(path, watch_dir, output_dir)
        record = self._convert_one(path, output, self._effective_profile(path))
        self.records.append(record)

    # ------------------------------------------------------------------ rules tab
    def refresh_rules_tree(self):
        self.rules_tree.delete(*self.rules_tree.get_children())
        for index, rule in enumerate(self.rules):
            kind = t(self.lang, "type_regex" if rule.use_regex else "type_literal")
            self.rules_tree.insert("", "end", iid=str(index), values=(
                "✓" if rule.enabled else "",
                rule.find, rule.replace, kind, rule.note))

    def _selected_rule_index(self):
        selection = self.rules_tree.selection()
        return int(selection[0]) if selection else None

    def _tree_wheel(self, _event, steps: int):
        try:
            self.rules_tree.yview_scroll(steps, "units")
        except Exception:  # noqa: BLE001
            pass
        return "break"

    def add_rule(self):
        dialog = RuleDialog(self.root, self.lang)
        if dialog.result:
            self.rules.append(dialog.result)
            self.refresh_rules_tree()
            self.refresh_preview()

    def edit_rule(self):
        index = self._selected_rule_index()
        if index is None:
            return
        dialog = RuleDialog(self.root, self.lang, self.rules[index])
        if dialog.result:
            self.rules[index] = dialog.result
            self.refresh_rules_tree()
            self.refresh_preview()

    def delete_rule(self):
        index = self._selected_rule_index()
        if index is None:
            return
        del self.rules[index]
        self.refresh_rules_tree()
        self.refresh_preview()

    def move_rule(self, direction: int):
        index = self._selected_rule_index()
        if index is None:
            return
        other = index + direction
        if 0 <= other < len(self.rules):
            self.rules[index], self.rules[other] = self.rules[other], self.rules[index]
            self.refresh_rules_tree()
            self.rules_tree.selection_set(str(other))
            self.refresh_preview()

    def toggle_rule(self):
        index = self._selected_rule_index()
        if index is None:
            return
        self.rules[index].enabled = not self.rules[index].enabled
        self.refresh_rules_tree()
        self.refresh_preview()

    def load_rules_dialog(self):
        path = filedialog.askopenfilename(
            title=t(self.lang, "rules_load_title"),
            filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            self.rules = load_rules(path)
            self.config["rules_path"] = path
            self._persist()
            self.refresh_rules_tree()
            self.refresh_preview()
            self._log(t(self.lang, "rules_loaded", n=len(self.rules), path=path))
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    def save_rules_dialog(self):
        path = filedialog.asksaveasfilename(
            title=t(self.lang, "rules_save_title"), defaultextension=".json",
            initialfile="custom_rules.json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            save_rules(path, self.rules)
            self.config["rules_path"] = path
            self._persist()
            self._log(t(self.lang, "rules_saved", n=len(self.rules), path=path))
        except OSError as exc:
            messagebox.showerror(APP_TITLE, str(exc))

    # ------------------------------------------------------------------ preview
    def _selected_path(self) -> str | None:
        selection = self.file_list.curselection()
        if selection:
            return self.files[selection[0]]
        return self.files[0] if self.files else None

    def _format_summary(self, report) -> str:
        parts = []
        if report.mojibake_lines:
            parts.append(t(self.lang, "report_mojibake", n=report.mojibake_lines))
        if report.pair_changes:
            parts.append(t(self.lang, "report_pairs", n=report.pair_changes,
                           pair=report.pair_label or ""))
        if report.custom_changes:
            parts.append(t(self.lang, "report_custom", n=report.custom_changes))
        tools = report.tools
        if tools is not None:
            if tools.shifted_ms:
                parts.append(t(self.lang, "report_tools_shift", ms=tools.shifted_ms))
            if tools.overlaps_fixed:
                parts.append(t(self.lang, "report_overlaps", n=tools.overlaps_fixed))
            if tools.renumbered:
                parts.append(t(self.lang, "report_renumber"))
            if tools.hi_removed:
                parts.append(t(self.lang, "report_hi", n=tools.hi_removed))
            if tools.html_removed:
                parts.append(t(self.lang, "report_html", n=tools.html_removed))
            if tools.cps_violations:
                parts.append(t(self.lang, "report_cps", n=len(tools.cps_violations),
                               limit=self._cps_limit_value(report)))
        if not parts:
            parts.append(t(self.lang, "report_clean"))
        if report.suggested_profile:
            profile_name = t(self.lang, f"profile_{report.suggested_profile}")
            parts.append(t(self.lang, "log_suggested", profile=profile_name))
        elif (report.possible_profile and report.pair_changes == 0
                and report.mojibake_lines == 0):
            profile_name = t(self.lang, f"profile_{report.possible_profile}")
            parts.append(t(self.lang, "log_possible", profile=profile_name))
        return "; ".join(parts) + "."

    def _cps_limit_value(self, report) -> str:
        try:
            return str(self._cps_limit()).rstrip("0").rstrip(".")
        except Exception:  # noqa: BLE001
            return "?"

    def refresh_preview(self):
        path = self._selected_path()
        if not path:
            self._set_preview("", "")
            return
        try:
            with open(path, "rb") as f:
                raw = f.read()
            try:
                before = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                before = raw.decode(
                    get_profile(self._effective_profile(path)).byte_candidates[0],
                    errors="replace")
            before_snip = "\n".join(before.split("\n")[:PREVIEW_LINES])
            fixed, report = fix_bytes(
                raw, self._selected_source(), self._effective_profile(path),
                self.mojibake_var.get(), self.pairs_var.get(),
                self._active_rules(), self._manual_pair(), self._tool_options())
            after_snip = "\n".join(fixed.split("\n")[:PREVIEW_LINES])
            note = f"[{report.detected_encoding}] {self._format_summary(report)}"
            self._set_preview(before_snip, after_snip + f"\n\n---\n{note}")
        except Exception as exc:  # noqa: BLE001
            self._set_preview(str(exc), "")

    def _diff_tags(self, before: str, after: str):
        """Line spans (start,end offsets) that differ, for both panes."""
        before_lines = before.split("\n")
        after_lines = after.split("\n")
        matcher = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
        before_spans, after_spans = [], []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "equal":
                before_spans.append((i1, i2))
                after_spans.append((j1, j2))
        return before_spans, after_spans

    def _set_preview(self, before: str, after: str, before_spans=None, after_spans=None):
        if before_spans is None or after_spans is None:
            before_spans, after_spans = self._diff_tags(before, after)
        for widget, header, body, spans in (
                (self.text_before, t(self.lang, "preview_before"), before, before_spans),
                (self.text_after, t(self.lang, "preview_after"), after, after_spans)):
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            prefix = f"{header}\n\n"
            widget.insert("1.0", prefix + body)
            base = len(prefix.split("\n"))  # 1-based first body line
            for start, end in spans:
                widget.tag_add("chg", f"{base + start}.0", f"{base + end}.0")
            widget.configure(state="disabled")

    # ------------------------------------------------------------------ convert
    def _log(self, message: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")
        self.root.update_idletasks()

    def _resolve_output_path(self, input_path: str) -> str:
        suffix = self.suffix_var.get()
        if self.same_dir_var.get() or not self.output_dir_var.get().strip():
            return suggest_output_path(input_path, None, suffix=suffix,
                                       overwrite=(suffix == ""))
        output_dir = self.output_dir_var.get().strip()
        os.makedirs(output_dir, exist_ok=True)
        return suggest_output_path(input_path, output_dir, suffix=suffix,
                                   overwrite=(suffix == ""))

    def _convert_one(self, path: str, output: str, profile_id: str) -> ConversionRecord:
        dry = self.dry_run_var.get()
        backup_dir = self.output_dir_var.get().strip() or None
        try:
            report = process_file(
                path, output, self._selected_source(), profile_id,
                self._selected_target(), self.mojibake_var.get(),
                self.pairs_var.get(), list(self._active_rules()),
                self._manual_pair(), False, self._tool_options(),
                self._selected_backup_mode(), backup_dir, dry)
            status = "dry-run" if dry else "ok"
            return record_from(path, output, profile_id, report, status)
        except Exception as exc:  # noqa: BLE001
            empty_report = fix_bytes(b"", "auto", "auto")[1]
            return record_from(path, output, profile_id, empty_report, "error", str(exc))

    def convert_all_threaded(self):
        if not self.files:
            messagebox.showwarning(t(self.lang, "msg_no_files"),
                                   t(self.lang, "msg_no_files_body"))
            return
        thread = threading.Thread(target=self.convert_all, daemon=True)
        thread.start()

    def convert_all(self):
        total = len(self.files)
        self.records = []
        self.progress.configure(maximum=total, value=0)
        for pos, path in enumerate(list(self.files), 1):
            profile_id = self._effective_profile(path)
            output = self._resolve_output_path(path)
            record = self._convert_one(path, output, profile_id)
            self.records.append(record)
            prefix = "[DRY] " if record.status == "dry-run" else ""
            if record.status == "error":
                self._log(prefix + t(self.lang, "log_error", i=pos, total=total,
                                     name=os.path.basename(path), error=record.error))
            else:
                summary = self._record_summary(record)
                self._log(prefix + t(self.lang, "log_converted", i=pos, total=total,
                                     name=os.path.basename(path), out=output,
                                     detected=record.detected, summary=summary))
            self.progress.configure(value=pos)
        done = sum(1 for r in self.records if r.status == "ok")
        self._log(t(self.lang, "log_finished", ok=done, total=total))
        self.root.after(0, lambda: messagebox.showinfo(
            t(self.lang, "msg_done_title"),
            t(self.lang, "msg_done_body", ok=done, total=total)))

    def _record_summary(self, record: ConversionRecord) -> str:
        parts = []
        if record.mojibake_lines:
            parts.append(t(self.lang, "report_mojibake", n=record.mojibake_lines))
        if record.pair_changes:
            parts.append(t(self.lang, "report_pairs", n=record.pair_changes,
                           pair=record.pair_label))
        if record.custom_changes:
            parts.append(t(self.lang, "report_custom", n=record.custom_changes))
        if record.tools_summary:
            parts.append(record.tools_summary)
        if record.cps_violations:
            parts.append(t(self.lang, "report_cps", n=record.cps_violations,
                           limit=self._cps_limit_value(None)))
        return ("; ".join(parts) + ".") if parts else t(self.lang, "report_clean")

    def export_report_dialog(self):
        if not self.records:
            messagebox.showwarning(t(self.lang, "msg_no_report_title"),
                                   t(self.lang, "msg_no_report"))
            return
        path = filedialog.asksaveasfilename(
            title=t(self.lang, "btn_export_report"), defaultextension=".csv",
            initialfile="subtitle_report.csv",
            filetypes=[("CSV", "*.csv"), ("HTML", "*.html"), ("All files", "*.*")])
        if not path:
            return
        try:
            export_report(path, self.records)
            self._log(t(self.lang, "report_saved", path=path))
        except (OSError, ValueError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))


# ------------------------------------------------------------------ batch CLI
def _setup_console():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def _tool_options_from_args(args) -> ToolOptions:
    return ToolOptions(
        shift_ms=args.shift_ms,
        fix_overlaps=args.fix_overlaps,
        renumber=args.renumber,
        strip_hi=args.strip_hi,
        strip_html=args.strip_html,
        check_cps=not args.no_cps,
        cps_limit=args.cps_limit,
    )


def _match_file_profile(path: str, mapping: dict[str, str], default: str) -> str:
    if path in mapping:
        return mapping[path]
    base = os.path.basename(path)
    if base in mapping:
        return mapping[base]
    return default


def _convert_cli(paths: list[str], args, lang: str, rules,
                 file_profiles: dict[str, str]) -> tuple[list[ConversionRecord], int]:
    records: list[ConversionRecord] = []
    done = 0
    tools = _tool_options_from_args(args)
    backup_dir = args.backup_dir or args.outdir or None
    for path in paths:
        profile = _match_file_profile(os.path.abspath(path), file_profiles, args.profile)
        output = suggest_output_path(path, args.outdir or None,
                                     suffix=args.suffix,
                                     overwrite=(args.suffix == ""))
        try:
            report = process_file(path, output, args.source, profile,
                                  args.target, not args.no_mojibake,
                                  not args.no_pairs, rules, None,
                                  False, tools, args.backup_mode,
                                  backup_dir, args.dry_run)
            status = "dry-run" if args.dry_run else "ok"
            record = record_from(path, output, profile, report, status)
            records.append(record)
            prefix = "[DRY] " if args.dry_run else ""
            print(f"{prefix}[OK] {path} -> {output} [{report.detected_encoding}]")
            done += 1
        except Exception as exc:  # noqa: BLE001
            empty = fix_bytes(b"", "auto", "auto")[1]
            records.append(record_from(path, output, profile, empty, "error", str(exc)))
            print(f"[ERROR] {path}: {exc}")
    return records, done


def cli_main(argv: list[str]) -> int:
    import argparse
    _setup_console()
    parser = argparse.ArgumentParser(description="Subtitle Text Fixer (batch mode)")
    parser.add_argument("files", nargs="*", help="subtitle files (globs allowed); '-' = stdin")
    parser.add_argument("-o", "--outdir", default="")
    parser.add_argument("--suffix", default="_fixed")
    parser.add_argument("--source", default="auto")
    parser.add_argument("--target", default=None,
                        help="utf-8-sig (default for files) or utf-8")
    parser.add_argument("--profile", default="auto", choices=list(PROFILE_ORDER))
    parser.add_argument("--file-profile", action="append", default=[],
                        metavar="FILE:PROFILE",
                        help="per-file profile override, repeatable")
    parser.add_argument("--rules", default="")
    parser.add_argument("--no-mojibake", action="store_true")
    parser.add_argument("--no-pairs", action="store_true")
    parser.add_argument("--no-custom", action="store_true")
    parser.add_argument("--pair", default="",
                        help="forced reinterpret pair, e.g. cp1252:cp1251")
    parser.add_argument("--shift-ms", type=int, default=0)
    parser.add_argument("--fix-overlaps", action="store_true")
    parser.add_argument("--renumber", action="store_true")
    parser.add_argument("--strip-hi", action="store_true")
    parser.add_argument("--strip-html", action="store_true")
    parser.add_argument("--no-cps", action="store_true")
    parser.add_argument("--cps-limit", type=float, default=20.0)
    parser.add_argument("--backup-mode", default="none", choices=list(BACKUP_MODES))
    parser.add_argument("--backup-dir", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", default="")
    parser.add_argument("--watch", default="",
                        help="watch folder for new .srt files (needs --outdir)")
    parser.add_argument("--watch-interval", type=float, default=5.0)
    parser.add_argument("--lang", default="auto", choices=["auto", "tr", "en"])
    args = parser.parse_args(argv)
    lang = detect_system_language() if args.lang == "auto" else args.lang
    if lang not in ("tr", "en"):
        lang = "en"  # CLI messages currently cover tr/en

    rules: list[CustomRule] = []
    if args.rules:
        rules = load_rules(args.rules)
    elif not args.no_custom and os.path.isfile(DEFAULT_RULES_PATH):
        try:
            rules = load_rules(DEFAULT_RULES_PATH)
        except (OSError, ValueError):
            rules = []
    if args.no_custom:
        rules = []

    manual_pair = None
    if args.pair:
        if ":" not in args.pair:
            print("Expected --pair WRONG:RIGHT, e.g. cp1252:cp1251")
            return 1
        wrong, right = args.pair.split(":", 1)
        manual_pair = (wrong.strip(), right.strip())

    file_profiles: dict[str, str] = {}
    for item in args.file_profile:
        if ":" not in item:
            print(f"Expected --file-profile FILE:PROFILE, got: {item}")
            return 1
        fpath, prof = item.rsplit(":", 1)
        prof = prof.strip()
        if prof not in PROFILE_ORDER:
            print(f"Unknown profile: {prof}")
            return 1
        key = os.path.abspath(fpath.strip())
        file_profiles[key] = prof
        file_profiles[os.path.basename(key)] = prof

    # --- stdin mode ---
    if args.files == ["-"]:
        raw = sys.stdin.buffer.read()
        target = args.target or "utf-8"
        fixed, _report = fix_bytes(raw, args.source, args.profile,
                                   not args.no_mojibake, not args.no_pairs,
                                   rules, manual_pair,
                                   _tool_options_from_args(args))
        sys.stdout.write(fixed)
        return 0

    # --- watch mode ---
    if args.watch:
        if not args.outdir:
            print("Watch mode needs --outdir.")
            return 1
        os.makedirs(args.outdir, exist_ok=True)
        print(f"Watching {args.watch} -> {args.outdir} (Ctrl+C to stop)…")
        watcher = FolderWatcher(args.watch, args.watch_interval, lambda _p: None)
        watcher._seen = {}
        try:
            while True:
                current = watcher._snapshot()
                fresh = [p for p in current
                         if p not in watcher._seen
                         and not os.path.abspath(p).startswith(
                             os.path.abspath(args.outdir) + os.sep)]
                for path in sorted(fresh):
                    profile = _match_file_profile(path, file_profiles, args.profile)
                    relative = os.path.relpath(path, args.watch)
                    dest_dir = os.path.join(args.outdir, os.path.dirname(relative))
                    os.makedirs(dest_dir, exist_ok=True)
                    base = os.path.basename(relative)
                    stem, ext = os.path.splitext(base)
                    output = os.path.join(dest_dir, f"{stem}{args.suffix}{ext or '.srt'}")
                    try:
                        report = process_file(
                            path, output, args.source, profile,
                            args.target or "utf-8-sig",
                            not args.no_mojibake, not args.no_pairs,
                            rules, manual_pair, False,
                            _tool_options_from_args(args), args.backup_mode,
                            args.backup_dir or args.outdir, args.dry_run)
                        print(f"[OK] {path} -> {output} [{report.detected_encoding}]")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[ERROR] {path}: {exc}")
                    watcher._seen[path] = current[path]
                for gone in [p for p in watcher._seen if p not in current]:
                    watcher._seen.pop(gone, None)
                time.sleep(max(1.0, args.watch_interval))
        except KeyboardInterrupt:
            print("Watch stopped.")
        return 0

    import glob as _glob
    paths: list[str] = []
    for pattern in args.files:
        hits = _glob.glob(pattern)
        paths.extend(hits if hits else [pattern])
    paths = [p for p in paths if os.path.isfile(p)]
    if not paths:
        parser.print_usage()
        return 1

    if args.target is None:
        args.target = "utf-8-sig"
    records, done = _convert_cli(paths, args, lang, rules, file_profiles)
    if args.report:
        export_report(args.report, records)
        print(f"Report: {args.report}")
    print(t(lang, "cli_done", ok=done, total=len(paths)))
    return 0 if done == len(paths) else 2


def main():
    wants_cli = len(sys.argv) > 1 and any(
        a.lower().endswith(".srt") or a == "-" or a.startswith("-")
        for a in sys.argv[1:])
    if wants_cli:
        raise SystemExit(cli_main(sys.argv[1:]))

    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    try:
        root.tk.call("tk", "scaling", 1.15)
    except Exception:  # noqa: BLE001
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
