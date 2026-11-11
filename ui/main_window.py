import os
import sys
import time
import webbrowser
import threading
import logging
from typing import Optional, Dict, List, Any
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import customtkinter as ctk

from ui.theme import (
    BG_CANVAS,
    BG_SIDEBAR,
    BG_CARD,
    BG_TILE,
    BG_TILE_ALT,
    BG_CONSOLE,
    BG_INPUT,
    BORDER_SUBTLE,
    BORDER_ACCENT,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    TEXT_MUTED,
    ACCENT_BLUE,
    ACCENT_BLUE_HOVER,
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_AMBER,
    ACCENT_RED,
    ACCENT_PURPLE,
    ACCENT_SLATE,
    FONT_FAMILY,
    get_layout,
    rtl_wrap,
    make_hero_header,
    make_status_badge,
    update_status_badge,
    make_card,
    make_console_box,
    set_console_text,
    make_button,
    pack_button_grid,
    make_telemetry_tile,
    style_entry,
)

from setup_bins import verify_binaries, repair_tools, get_adb_path, get_fastboot_path, get_scrcpy_path
from database.db_manager import DatabaseManager
from core.adb_bridge import ADBBridge
from core.profiler import DeviceProfiler, DeviceProfile
from core.board_diag import (
    MotherboardDiagnostics,
    BoardDiagnosticResult,
    PMICStatus,
    DeadPhoneBootROMDiagnosis,
    FastChargeDiagnostics,
    StorageLifeReport,
    StressTestSnapshot,
    StressTestReport,
    RootForensicsReport,
    EFSBackupResult
)
from core.hardware_tester import (
    HardwareTester,
    HardwareAuditReport,
    FingerprintDiagnostics,
    PhysicalButtonsDiagnostics,
    SIMBasebandDiagnostics,
    DisplayBacklightDiagnostics,
    WiFiDiagnostics,
    BluetoothDiagnostics,
    PeripheralChipsDiagnostics,
    AudioCodecReport,
    DisplayPanelInfo
)
from core.storage_benchmark import StorageBenchmark, StorageHealthReport
from core.partition_backup import PartitionBackupManager, PartitionInfo, BackupResult
from core.batch_apk_manager import BatchAPKManager, InstallResult, ExtractResult
from core.touch_matrix import TouchMatrixTester
from core.malware_hunter import MalwareHunter, SecurityAuditScan, AppSecurityInfo
from core.optimizer import RealSystemOptimizer, OptimizationResult
from core.rescue_center import RescueCenter, FastbootVariables
from core.provisioning_manager import ProvisioningManager, ProvisioningStatus
from core.result import Evidence, Status, CheckResult, aggregate_status
from ui.report_exporter import DiagnosticReportExporter, REPORTS_DIR
from ui.i18n import t, render, set_current_language

logger = logging.getLogger("MainWindow")
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")
ctk.ThemeManager.theme["CTkFont"]["family"] = "Segoe UI"


def _tri_key(value: Any) -> str:
    """Translation key for a yes / no / unknown value."""
    return "common.unknown" if value is None else ("common.yes" if value else "common.no")


def _feature_key(value: Any) -> str:
    return "feature.UNKNOWN" if value is None else ("feature.DECLARED" if value else "feature.NOT_DECLARED")


def _flinger_key(value: Any) -> str:
    return "audio.flinger.UNKNOWN" if value is None else ("audio.flinger.RUNNING" if value else "audio.flinger.NOT_RUNNING")


def maintenance_pill_status(actions: List[Any]) -> str:
    """Overall maintenance status: the same rule as every other aggregate (no actions -> UNKNOWN)."""
    return str(aggregate_status([getattr(a, "status", Status.UNKNOWN) for a in actions]))


def bootrom_pill_status(diag: Any) -> str:
    """The port scan reports which USB modes were seen, not a pass/fail reading. Fastboot or MTP
    are normal modes and even EDL/BROM can be intentional, so the pill never claims FAIL or PASS."""
    return str(Status.UNKNOWN)


def _fmt(value: Any, unit: str = "") -> str:
    """Format a reading; None is shown as UNKNOWN (never as 0 or a guess)."""
    if value is None:
        return "UNKNOWN"
    return f"{value} {unit}".strip()


def format_evidence_for_display(evidence_items: Any, is_ar: bool = True) -> str:
    """Renders a list of Evidence objects with source and excerpt formatted separately for UI display."""
    if not evidence_items:
        return ""
    if isinstance(evidence_items, str):
        evidence_list = [{"source": "Raw Data", "raw_excerpt": evidence_items.strip(), "exit_code": None}]
    elif isinstance(evidence_items, list):
        evidence_list = evidence_items
    else:
        evidence_list = [evidence_items]

    if not evidence_list:
        return ""

    header = t("ui.technical_evidence", ("ar" if is_ar else "en"))
    lines = [header]
    for ev in evidence_list:
        src = getattr(ev, "source", None) or (ev.get("source") if isinstance(ev, dict) else "")
        exc = getattr(ev, "raw_excerpt", None) or (ev.get("raw_excerpt") if isinstance(ev, dict) else "")
        code = getattr(ev, "exit_code", 0) if hasattr(ev, "exit_code") else (ev.get("exit_code") if isinstance(ev, dict) else 0)
        code_str = f" [exit {code}]" if code is not None and code != 0 else ""
        lines.append(t("ui.source", ("ar" if is_ar else "en"), src=src, code_str=code_str))
        if exc:
            for eline in str(exc).strip().splitlines():
                lines.append(f"    {eline}")
    return "\n".join(lines)


class AndroidDoctorApp(ctk.CTk):
    def __init__(self, lang: str = "ar"):
        super().__init__()

        self.current_lang = lang
        set_current_language(lang)
        self.title(self._tr("ui.android_doctor_pro"))
        self.geometry("1340x900")
        self.minsize(1100, 750)

        # Initialize Subsystems
        self.db = DatabaseManager()
        self.bridge = ADBBridge()
        self.profiler = DeviceProfiler(self.bridge)
        self.board_diag = MotherboardDiagnostics(self.bridge, self.db)
        self.hardware_tester = HardwareTester(self.bridge)
        self.malware_hunter = MalwareHunter(self.bridge, self.db)
        self.optimizer = RealSystemOptimizer(self.bridge, self.db)
        self.rescue_center = RescueCenter(self.bridge)
        self.provisioning = ProvisioningManager(self.bridge)
        self.storage_bench = StorageBenchmark(self.bridge)
        self.partition_backup = PartitionBackupManager(self.bridge)
        self.batch_apk = BatchAPKManager(self.bridge)
        self.touch_matrix = TouchMatrixTester(self.bridge)

        # State Variables
        self.current_serial = tk.StringVar(value="")
        self.available_devices: List[Dict[str, str]] = []
        self.last_connection_diag: Dict[str, Any] = {}
        self.current_profile: Optional[DeviceProfile] = None
        self.last_board_diag: Optional[BoardDiagnosticResult] = None
        self.last_hw_audit: Optional[HardwareAuditReport] = None
        self.last_sec_scan: Optional[SecurityAuditScan] = None
        self.last_opt_result: Optional[OptimizationResult] = None
        self.last_bootrom_diag: Optional[DeadPhoneBootROMDiagnosis] = None
        self.last_fast_charge: Optional[FastChargeDiagnostics] = None
        self.last_storage_report: Optional[StorageHealthReport] = None
        self.last_storage_life: Optional[StorageLifeReport] = None
        self.last_stress_report: Optional[StressTestReport] = None
        self.last_audio_codec: Optional[AudioCodecReport] = None
        self.last_panel_info: Optional[DisplayPanelInfo] = None
        self.last_blackbox: Optional[Dict[str, Any]] = None
        self.last_bloat_scan: Optional[List[Dict[str, Any]]] = None
        self.last_root_forensics: Optional[RootForensicsReport] = None
        self.last_efs_backup: Optional[EFSBackupResult] = None
        self._telemetry_running = False
        self._telemetry_thread: Optional[threading.Thread] = None
        self.pointer_location_var = tk.BooleanVar(value=False)

        self.configure(fg_color=BG_CANVAS)
        self.status_pills: dict[str, ctk.CTkLabel] = {}
        self.kpi_labels: dict[str, ctk.CTkLabel] = {}
        self._current_tab = "dashboard"
        self._setup_ui()
        self._start_device_monitor()

    def _tr(self, key: str, **kwargs: Any) -> str:
        return t(key, self.current_lang, **kwargs)

    def _layout(self) -> dict[str, Any]:
        return get_layout(self.current_lang)

    def _L(self, text: str) -> str:
        return rtl_wrap(text, self.current_lang)

    def _set_box(self, box: ctk.CTkTextbox | None, text: str) -> None:
        set_console_text(box, text, self.current_lang)

    def _append_box(self, box: ctk.CTkTextbox | None, text: str) -> None:
        if box is None:
            return
        try:
            if not box.winfo_exists():
                return
            wrapped = rtl_wrap(text, self.current_lang)
            box.insert("end", wrapped)
            layout = self._layout()
            box._textbox.tag_configure("rtl", justify=layout["justify"])
            box._textbox.tag_add("rtl", "1.0", "end")
            box.see("end")
        except Exception:
            pass

    def _set_pill(self, key: str, status: str) -> None:
        update_status_badge(self.status_pills.get(key), status)

    def _m(self, value: Any) -> str:
        """Render a core message (Msg), a list of them, or a plain value in the current language."""
        return render(value, self.current_lang)

    def _setup_ui(self):
        L = self._layout()
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        # ------------------ SIDEBAR ------------------
        self.sidebar_frame = ctk.CTkFrame(
            self,
            width=265,
            corner_radius=0,
            fg_color=BG_SIDEBAR,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        self.sidebar_frame.grid(row=0, column=0, rowspan=2, sticky="nsew")
        self.sidebar_frame.grid_propagate(False)
        self.sidebar_frame.grid_columnconfigure(0, weight=1)

        for r in range(6):
            self.sidebar_frame.grid_rowconfigure(r, weight=0)
        self.sidebar_frame.grid_rowconfigure(6, weight=1)
        self.sidebar_frame.grid_rowconfigure(7, weight=0)

        # Brand Header Card
        brand_card = ctk.CTkFrame(
            self.sidebar_frame,
            fg_color=BG_TILE,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_ACCENT,
        )
        brand_card.grid(row=0, column=0, padx=14, pady=(14, 10), sticky="ew")

        brand_top = ctk.CTkFrame(brand_card, fg_color="transparent")
        brand_top.pack(fill="x", padx=12, pady=(10, 2))

        self.logo_label = ctk.CTkLabel(
            brand_top,
            text=self._L(self._tr("ui.android_doctor_pro_2")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color="#60a5fa",
            anchor=L["anchor"],
        )
        self.logo_label.pack(side=L["lead"], fill="x", expand=True)

        pro_pill = ctk.CTkLabel(
            brand_top,
            text="PRO",
            fg_color="#172554",
            text_color="#93c5fd",
            corner_radius=6,
            padx=7,
            pady=1,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
        )
        pro_pill.pack(side=L["trail"], padx=(4, 0) if L["lead"] == "left" else (0, 4))

        self.subtitle_label = ctk.CTkLabel(
            brand_card,
            text=self._L(self._tr("ui.hardware_diagnostics_suite")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_SECONDARY,
            anchor=L["anchor"],
            justify=L["justify"],
            wraplength=210,
        )
        self.subtitle_label.pack(fill="x", padx=12, pady=(0, 10))

        # Language Switcher (Segmented Pill)
        lang_bar = ctk.CTkFrame(
            self.sidebar_frame,
            fg_color=BG_TILE,
            corner_radius=9,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        lang_bar.grid(row=1, column=0, padx=14, pady=(0, 10), sticky="ew")
        lang_bar.grid_columnconfigure((0, 1), weight=1, uniform="lang_col")

        ar_active = self.current_lang == "ar"
        self.btn_lang_ar = ctk.CTkButton(
            lang_bar,
            text=self._L(self._tr("ui.text")),
            height=28,
            corner_radius=7,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=ACCENT_BLUE if ar_active else "transparent",
            hover_color=ACCENT_BLUE_HOVER if ar_active else BG_CARD,
            text_color="#ffffff" if ar_active else TEXT_SECONDARY,
            command=lambda: self.switch_language("ar"),
        )
        self.btn_lang_ar.grid(row=0, column=0, padx=3, pady=3, sticky="ew")

        en_active = self.current_lang == "en"
        self.btn_lang_en = ctk.CTkButton(
            lang_bar,
            text=self._tr("ui.english"),
            height=28,
            corner_radius=7,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=ACCENT_BLUE if en_active else "transparent",
            hover_color=ACCENT_BLUE_HOVER if en_active else BG_CARD,
            text_color="#ffffff" if en_active else TEXT_SECONDARY,
            command=lambda: self.switch_language("en"),
        )
        self.btn_lang_en.grid(row=0, column=1, padx=3, pady=3, sticky="ew")

        # Device Connection Panel
        dev_card = ctk.CTkFrame(
            self.sidebar_frame,
            fg_color=BG_TILE,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        dev_card.grid(row=2, column=0, padx=14, pady=(0, 12), sticky="ew")

        self.dev_select_label = ctk.CTkLabel(
            dev_card,
            text=self._L(self._tr("target_device")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=TEXT_SECONDARY,
            anchor=L["anchor"],
        )
        self.dev_select_label.pack(fill="x", padx=12, pady=(10, 4))

        self.device_combo = ctk.CTkComboBox(
            dev_card,
            values=[self._tr("no_devices")],
            command=self._on_device_selected,
            height=32,
            fg_color=BG_INPUT,
            border_color=BORDER_ACCENT,
            button_color=ACCENT_BLUE,
            button_hover_color=ACCENT_BLUE_HOVER,
            dropdown_fg_color=BG_CARD,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
        )
        self.device_combo.pack(fill="x", padx=12, pady=(0, 8))

        self.btn_refresh_dev = make_button(
            dev_card,
            text=self._L(self._tr("refresh_devices")),
            command=self.refresh_devices_async,
            variant="secondary",
            height=30,
            font_size=11,
        )
        self.btn_refresh_dev.pack(fill="x", padx=12, pady=(0, 10))

        # Navigation Menu
        self.nav_container = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.nav_container.grid(row=3, column=0, sticky="ew", padx=14, pady=(0, 6))

        self.nav_buttons = {}
        tab_names = [
            ("dashboard", "tab_dashboard"),
            ("board_diag", "tab_board_diag"),
            ("hardware_tester", "tab_hardware"),
            ("malware_hunter", "tab_malware"),
            ("optimizer", "tab_optimizer"),
            ("rescue_center", "tab_rescue"),
            ("reports", "tab_reports"),
        ]

        for key, tr_key in tab_names:
            btn = ctk.CTkButton(
                self.nav_container,
                text=self._L(self._tr(tr_key)),
                anchor=L["anchor"],
                height=38,
                corner_radius=8,
                border_width=0,
                border_color=BORDER_SUBTLE,
                fg_color="transparent",
                text_color=TEXT_SECONDARY,
                hover_color="#162033",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                command=lambda k=key: self.select_tab(k),
            )
            btn.pack(fill="x", pady=2)
            self.nav_buttons[key] = btn

        # Expanding Spacer
        self.sidebar_spacer = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.sidebar_spacer.grid(row=6, column=0, sticky="nsew")

        # Live ADB Status Footer Pill
        self.status_box = ctk.CTkFrame(
            self.sidebar_frame,
            fg_color=BG_TILE,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        self.status_box.grid(row=7, column=0, padx=14, pady=14, sticky="ew")

        status_inner = ctk.CTkFrame(self.status_box, fg_color="transparent")
        status_inner.pack(fill="x", padx=10, pady=9)

        self.adb_status_dot = ctk.CTkFrame(
            status_inner, width=9, height=9, corner_radius=5, fg_color=TEXT_MUTED
        )
        self.adb_status_dot.pack(side=L["lead"], padx=(0, 8) if L["lead"] == "left" else (8, 0))

        self.adb_status_label = ctk.CTkLabel(
            status_inner,
            text=self._L(self._tr("adb_disconnected")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_SECONDARY,
            anchor=L["anchor"],
            justify=L["justify"],
            wraplength=190,
        )
        self.adb_status_label.pack(side=L["lead"], fill="x", expand=True)

        # ------------------ MAIN CONTENT AREA ------------------
        self.tools_banner = ctk.CTkFrame(
            self,
            fg_color="#2a1215",
            corner_radius=10,
            border_width=1,
            border_color="#b91c1c",
        )
        self.tools_banner_lbl = ctk.CTkLabel(
            self.tools_banner,
            text="",
            text_color="#fca5a5",
            justify=L["justify"],
            anchor=L["anchor"],
            wraplength=700,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        )
        self.tools_banner_lbl.pack(side=L["lead"], padx=14, pady=8, fill="x", expand=True)
        self.btn_repair_tools = make_button(
            self.tools_banner,
            text=self._L(self._tr("tools.repair")),
            command=self._repair_tools_async,
            variant="danger",
            height=30,
            width=150,
        )
        self.btn_repair_tools.pack(side=L["trail"], padx=14, pady=8)

        self.content_frame = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_CANVAS)
        self.content_frame.grid(row=1, column=1, sticky="nsew", padx=8, pady=8)
        self.content_frame.grid_columnconfigure(0, weight=1)
        self.content_frame.grid_rowconfigure(0, weight=1)

        self.tab_frames = {}
        self._init_tab_builders()
        self.select_tab("dashboard")
        self.after(150, self._build_next_pending_tab)
        self.after(300, self.refresh_devices_async)
        self.after(5000, self._auto_poll_devices)
        self.after(400, self._check_tools_background)

    def _auto_poll_devices(self):
        try:
            self.refresh_devices_async()
        except Exception:
            pass
        self.after(5000, self._auto_poll_devices)

    def switch_language(self, lang: str):
        if self.current_lang == lang:
            return
        active_tab = getattr(self, "_current_tab", "dashboard")
        self.current_lang = lang
        set_current_language(lang)
        L = self._layout()

        ar_active = lang == "ar"
        en_active = lang == "en"
        self.btn_lang_ar.configure(
            fg_color=ACCENT_BLUE if ar_active else "transparent",
            hover_color=ACCENT_BLUE_HOVER if ar_active else BG_CARD,
            text_color="#ffffff" if ar_active else TEXT_SECONDARY,
        )
        self.btn_lang_en.configure(
            fg_color=ACCENT_BLUE if en_active else "transparent",
            hover_color=ACCENT_BLUE_HOVER if en_active else BG_CARD,
            text_color="#ffffff" if en_active else TEXT_SECONDARY,
        )

        # Destroy existing tabs & rebuild with updated directional layout
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        self.tab_frames.clear()
        self.status_pills.clear()
        self.kpi_labels.clear()

        self.logo_label.configure(
            text=self._L(self._tr("ui.android_doctor_pro_2")), anchor=L["anchor"]
        )
        self.subtitle_label.configure(
            text=self._L(self._tr("ui.hardware_diagnostics_suite")),
            anchor=L["anchor"],
            justify=L["justify"],
        )
        self.btn_repair_tools.configure(text=self._L(self._tr("tools.repair")))

        tab_tr = {
            "dashboard": "tab_dashboard",
            "board_diag": "tab_board_diag",
            "hardware_tester": "tab_hardware",
            "malware_hunter": "tab_malware",
            "optimizer": "tab_optimizer",
            "rescue_center": "tab_rescue",
            "reports": "tab_reports",
        }
        for k, tr_key in tab_tr.items():
            if k in self.nav_buttons:
                self.nav_buttons[k].configure(
                    text=self._L(self._tr(tr_key)), anchor=L["anchor"]
                )

        self.dev_select_label.configure(
            text=self._L(self._tr("target_device")), anchor=L["anchor"]
        )
        self.btn_refresh_dev.configure(text=self._L(self._tr("refresh_devices")))
        self.adb_status_label.configure(anchor=L["anchor"], justify=L["justify"])

        self.select_tab(active_tab)
        self.after(120, self._build_next_pending_tab)
        self.refresh_devices_async()

    def _init_tab_builders(self):
        self._tab_builders = {
            "dashboard": self._build_dashboard_tab,
            "board_diag": self._build_board_diag_tab,
            "hardware_tester": self._build_hardware_tester_tab,
            "malware_hunter": self._build_malware_hunter_tab,
            "optimizer": self._build_optimizer_tab,
            "rescue_center": self._build_rescue_tab,
            "reports": self._build_reports_tab,
        }

    def _ensure_tab(self, key: str):
        if key not in self.tab_frames and key in self._tab_builders:
            self._tab_builders[key]()

    def _build_next_pending_tab(self):
        """Build one not-yet-built tab per idle slot, so every tab exists shortly after startup."""
        for key in self._tab_builders:
            if key not in self.tab_frames:
                self._ensure_tab(key)
                self.after(50, self._build_next_pending_tab)
                return

    def select_tab(self, key: str):
        self._ensure_tab(key)
        self._current_tab = key
        for k in self._tab_builders:
            frame = self.tab_frames.get(k)
            if k == key:
                frame.grid(row=0, column=0, sticky="nsew")
                if k in self.nav_buttons:
                    self.nav_buttons[k].configure(
                        fg_color="#172554",
                        border_width=1,
                        border_color=ACCENT_BLUE,
                        text_color="#ffffff",
                    )
            else:
                if frame is not None:
                    frame.grid_forget()
                if k in self.nav_buttons:
                    self.nav_buttons[k].configure(
                        fg_color="transparent",
                        border_width=0,
                        border_color=BORDER_SUBTLE,
                        text_color=TEXT_SECONDARY,
                    )

    # ------------------ bundled tools check / repair ------------------
    def _check_tools_background(self):
        """Hash check of bin/ against the manifest (runs on the Tk thread shortly after the window appears)."""
        try:
            status = verify_binaries()
        except Exception as e:
            status = {"ok": False, "packages": {}, "error": str(e)}
        self._show_tools_status(status)

    def _show_tools_status(self, status):
        self.tools_status = status
        if status.get("ok"):
            self.tools_banner.grid_forget()
            return
        problems = []
        for name, info in status.get("packages", {}).items():
            bad = info.get("missing", []) + info.get("mismatched", [])
            if bad:
                problems.append(f"{name}: {', '.join(bad[:4])}")
        if status.get("error"):
            problems.append(status["error"])
        self.tools_banner_lbl.configure(text=self._tr("tools.banner", details="; ".join(problems) or "-"))
        self.tools_banner.grid(row=0, column=1, sticky="ew", padx=10, pady=(10, 0))

    def _repair_tools_async(self):
        self.btn_repair_tools.configure(state="disabled")

        def progress(key, pct):
            self.after(0, lambda: self.tools_banner_lbl.configure(text=self._tr(key, pct=pct)))

        def task():
            try:
                results = repair_tools(progress)
            except Exception as e:
                results = {"repair": {"ok": False, "error": str(e)}}
            failed = [f"{n}: {r['error']}" for n, r in results.items() if not r.get("ok")]
            self.bridge.refresh_paths()

            def done():
                self.btn_repair_tools.configure(state="normal")
                if failed:
                    messagebox.showerror(self._tr("tools.repair"), self._tr("tools.repair_failed", details="\n".join(failed)))
                else:
                    messagebox.showinfo(self._tr("tools.repair"), self._tr("tools.repair_done"))
                self.after(400, self._check_tools_background)
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    # =========================================================================
    # TAB 1: DASHBOARD & SPECS
    # =========================================================================
    def _build_dashboard_tab(self):
        L = self._layout()
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color=BG_CANVAS)
        self.tab_frames["dashboard"] = tab

        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        make_hero_header(
            wrap,
            self._L(self._tr("dash_title")),
            self._L(self._tr("dash_sub")),
            L,
            accent_color=ACCENT_BLUE,
        )

        # Dedicated banner host keeps dynamic alerts above actions without reordering
        banner_host = ctk.CTkFrame(wrap, fg_color="transparent")
        banner_host.pack(fill="x")

        # Dynamic Connection Warning Alert Card
        self.alert_card = ctk.CTkFrame(
            banner_host,
            fg_color="#2a1215",
            border_color=ACCENT_RED,
            border_width=1,
            corner_radius=12,
        )
        self.alert_title_lbl = ctk.CTkLabel(
            self.alert_card,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color="#f87171",
            anchor=L["anchor"],
            justify=L["justify"],
        )
        self.alert_title_lbl.pack(fill="x", padx=15, pady=(12, 4))

        self.alert_desc_lbl = ctk.CTkLabel(
            self.alert_card,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color="#fecaca",
            justify=L["justify"],
            anchor=L["anchor"],
            wraplength=760,
        )
        self.alert_desc_lbl.pack(fill="x", padx=15, pady=(0, 10))

        alert_btns = ctk.CTkFrame(self.alert_card, fg_color="transparent")
        alert_btns.pack(fill="x", padx=15, pady=(0, 12))

        self.btn_alert_action = make_button(
            alert_btns,
            text=self._L(self._tr("ui.rescan_now")),
            command=self.refresh_devices_async,
            variant="danger",
            height=30,
        )
        self.btn_alert_action.pack(side=L["lead"], padx=4)

        make_button(
            alert_btns,
            text=self._L(self._tr("ui.restart_the_adb_server")),
            command=self._restart_adb_service,
            variant="secondary",
            height=30,
        ).pack(side=L["lead"], padx=4)

        # Connection Help Banner (Shown when no device is connected)
        self.guide_card = ctk.CTkFrame(
            banner_host,
            fg_color="#1f1709",
            border_color=ACCENT_AMBER,
            border_width=1,
            corner_radius=12,
        )
        self.guide_card.pack(fill="x", pady=(0, 12))

        guide_top = ctk.CTkFrame(self.guide_card, fg_color="transparent")
        guide_top.pack(fill="x", padx=15, pady=(12, 6))

        ctk.CTkLabel(
            guide_top,
            text=self._L(self._tr("guide_title")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color="#fbbf24",
            anchor=L["anchor"],
            justify=L["justify"],
        ).pack(side=L["lead"], fill="x", expand=True)

        make_button(
            guide_top,
            text=self._L(self._tr("guide_btn_restart_adb")),
            command=self._restart_adb_service,
            variant="warning",
            height=28,
            font_size=11,
        ).pack(side=L["trail"], padx=4)

        for step_key, step_pad in (("guide_step1", 2), ("guide_step2", 2), ("guide_step3", (2, 12))):
            ctk.CTkLabel(
                self.guide_card,
                text=self._L(self._tr(step_key)),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color="#fef3c7",
                anchor=L["anchor"],
                justify=L["justify"],
                wraplength=820,
            ).pack(fill="x", padx=18, pady=step_pad)

        # Responsive Quick Actions Toolbar
        _, action_btns = pack_button_grid(
            wrap,
            [
                (self._L(self._tr("btn_full_scan")), self.run_full_deep_scan_async, "success"),
                (self._L(self._tr("btn_reboot_sys")), lambda: self._exec_reboot(""), "secondary"),
                (self._L(self._tr("btn_reboot_rec")), lambda: self._exec_reboot("recovery"), "secondary"),
                (self._L(self._tr("btn_reboot_bl")), lambda: self._exec_reboot("bootloader"), "secondary"),
            ],
            cols=4,
            is_rtl=L["is_rtl"],
            pady=(0, 12),
            height=38,
        )
        self.btn_full_scan, self.btn_reboot_norm, self.btn_reboot_rec, self.btn_reboot_bl = action_btns

        # 4-Column KPI Summary Strip
        kpi_grid = ctk.CTkFrame(wrap, fg_color="transparent")
        kpi_grid.pack(fill="x", pady=(0, 12))
        kpi_grid.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="dash_kpi")

        kpi_specs = [
            ("model", self._L(self._tr("spec_model").rstrip(":")), ACCENT_BLUE),
            ("android", self._L(self._tr("spec_android").rstrip(":")), ACCENT_GREEN),
            ("root", self._L(self._tr("spec_root").rstrip(":")), ACCENT_PURPLE),
            ("selinux", self._L(self._tr("spec_selinux").rstrip(":")), ACCENT_AMBER),
        ]
        for idx, (k_key, k_title, k_color) in enumerate(kpi_specs):
            col = (3 - idx) if L["is_rtl"] else idx
            tile, k_val = make_telemetry_tile(kpi_grid, k_title, "—", k_color, L)
            tile.grid(row=0, column=col, padx=4, sticky="nsew")
            self.kpi_labels[k_key] = k_val

        # Dual Grid: Hardware Specifications + Live Telemetry Gauges
        grid = ctk.CTkFrame(wrap, fg_color="transparent")
        grid.pack(fill="both", expand=True)
        grid.grid_columnconfigure((0, 1), weight=1, uniform="dash_dual")

        specs_col = 1 if L["is_rtl"] else 0
        gauges_col = 0 if L["is_rtl"] else 1

        self.specs_card, _ = make_card(
            grid,
            self._L(self._tr("core_specs")),
            ACCENT_BLUE,
            L,
            pack_card=False,
        )
        self.specs_card.grid(
            row=0,
            column=specs_col,
            padx=(6, 0) if L["is_rtl"] else (0, 6),
            pady=4,
            sticky="nsew",
        )

        self.spec_labels = {}
        spec_fields = [
            ("model", self._L(self._tr("spec_model"))),
            ("serial", self._L(self._tr("spec_serial"))),
            ("soc", self._L(self._tr("spec_soc"))),
            ("cores", self._L(self._tr("spec_cores"))),
            ("android", self._L(self._tr("spec_android"))),
            ("build_id", self._L(self._tr("spec_build_id"))),
            ("security", self._L(self._tr("spec_security"))),
            ("kernel", self._L(self._tr("spec_kernel"))),
            ("root", self._L(self._tr("spec_root"))),
            ("selinux", self._L(self._tr("spec_selinux"))),
            ("display", self._L(self._tr("spec_display"))),
        ]

        specs_body = ctk.CTkFrame(self.specs_card, fg_color="transparent")
        specs_body.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        for r_idx, (key, title_str) in enumerate(spec_fields):
            row_bg = BG_TILE if r_idx % 2 == 0 else BG_TILE_ALT
            row = ctk.CTkFrame(specs_body, fg_color=row_bg, corner_radius=6)
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(
                row,
                text=title_str,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=TEXT_SECONDARY,
                anchor=L["anchor"],
            ).pack(side=L["lead"], padx=10, pady=5)
            val_lbl = ctk.CTkLabel(
                row,
                text="—",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                text_color=TEXT_PRIMARY,
                anchor="w",
            )
            val_lbl.pack(side=L["trail"], padx=10, pady=5)
            self.spec_labels[key] = val_lbl

        self.gauges_card, _ = make_card(
            grid,
            self._L(self._tr("live_gauges")),
            ACCENT_CYAN,
            L,
            pack_card=False,
        )
        self.gauges_card.grid(
            row=0,
            column=gauges_col,
            padx=(0, 6) if L["is_rtl"] else (6, 0),
            pady=4,
            sticky="nsew",
        )

        gauges_body = ctk.CTkFrame(self.gauges_card, fg_color="transparent")
        gauges_body.pack(fill="both", expand=True, padx=14, pady=(0, 12))

        def _make_gauge_block(title_key: str, bar_color: str):
            box = ctk.CTkFrame(
                gauges_body,
                fg_color=BG_TILE,
                corner_radius=8,
                border_width=1,
                border_color=BORDER_SUBTLE,
            )
            box.pack(fill="x", pady=(0, 10))
            lbl = ctk.CTkLabel(
                box,
                text=self._L(self._tr(title_key)),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                text_color=TEXT_PRIMARY,
                anchor=L["anchor"],
                justify=L["justify"],
            )
            lbl.pack(fill="x", padx=12, pady=(10, 6))
            bar = ctk.CTkProgressBar(
                box,
                height=11,
                corner_radius=6,
                fg_color=BORDER_SUBTLE,
                progress_color=bar_color,
            )
            bar.set(0)
            bar.pack(fill="x", padx=12, pady=(0, 12))
            return lbl, bar, box

        self.ram_title_lbl, self.ram_bar, _ = _make_gauge_block("ui.ram", ACCENT_BLUE)
        self.storage_title_lbl, self.storage_bar, _ = _make_gauge_block("ui.internal_storage", ACCENT_GREEN)
        self.batt_title_lbl, self.batt_bar, batt_box = _make_gauge_block("ui.battery", ACCENT_AMBER)

        self.batt_quick_lbl = ctk.CTkLabel(
            batt_box,
            text=self._L(self._tr("ui.voltage_temp_health")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_SECONDARY,
            anchor=L["anchor"],
            justify=L["justify"],
        )
        self.batt_quick_lbl.pack(fill="x", padx=12, pady=(0, 10))

    def _restart_adb_service(self):
        def task():
            ok, msg = self.bridge.restart_server()
            def done():
                if ok:
                    messagebox.showinfo(self._tr("ui.adb_server"), self._tr("ui.adb_server_restarted_successfully"))
                else:
                    messagebox.showwarning(self._tr("ui.adb_notice"), self._tr("ui.adb_result", msg=msg))
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    # =========================================================================
    # TAB 2: MOTHERBOARD & IC DIAGNOSTICS & BOOTROM SCANNER
    # =========================================================================
    def _build_board_diag_tab(self):
        L = self._layout()
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color=BG_CANVAS)
        self.tab_frames["board_diag"] = tab

        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        make_hero_header(
            wrap,
            self._L(self._tr("board_title")),
            self._L(self._tr("board_sub")),
            L,
            accent_color=ACCENT_CYAN,
        )

        # Responsive 4-Column Button Grid (Eliminates horizontal overflow on all widths)
        board_specs = [
            (self._L(self._tr("btn_pmic")), self.run_pmic_diag_async, "primary"),
            (self._L(self._tr("btn_leakage")), self.run_leakage_test_async, "danger"),
            (self._L(self._tr("btn_fast_charge")), self.run_fast_charge_diag_async, "success"),
            (self._L(self._tr("btn_reset_gauge")), self.run_reset_gauge_async, "warning"),
            (self._L(self._tr("btn_thermals")), self.run_thermal_scan_async, "secondary"),
            (self._L(self._tr("btn_panics")), self.run_panic_scan_async, "secondary"),
            (self._L(self._tr("btn_scan_bootrom")), self.run_bootrom_scan_async, "purple"),
            (self._L(self._tr("btn_live_telemetry")), self._toggle_live_telemetry, "cyan"),
            (self._L(self._tr("btn_nand_lifespan")), self.run_nand_lifespan_async, "purple"),
            (self._L(self._tr("btn_speed_bench")), self.run_speed_bench_async, "cyan"),
            (self._L(self._tr("btn_stress_test")), self.run_stress_test_async, "danger"),
            (self._L(self._tr("btn_run_blackbox")), self.run_blackbox_forensics_async, "secondary"),
            (self._L(self._tr("btn_run_root_forensics")), self.run_root_forensics_async, "purple"),
            (self._L(self._tr("btn_backup_efs_raw")), self.run_efs_backup_async, "success"),
        ]
        _, b_btns = pack_button_grid(
            wrap, board_specs, cols=3, is_rtl=L["is_rtl"], pady=(0, 12), height=38
        )
        (
            self.btn_run_pmic,
            self.btn_run_leakage,
            self.btn_fast_charge,
            self.btn_reset_gauge,
            self.btn_scan_thermal,
            self.btn_scan_panics,
            self.btn_scan_bootrom,
            self.btn_toggle_telemetry,
            self.btn_nand_lifespan,
            self.btn_speed_bench,
            self.btn_stress_test,
            self.btn_run_blackbox,
            self.btn_run_root_forensics,
            self.btn_backup_efs_row,
        ) = b_btns

        # Live Hardware Telemetry Strip (1.5s Polling)
        self.telemetry_card, _ = make_card(
            wrap, self._L(self._tr("card_telemetry")), ACCENT_CYAN, L
        )
        telem_grid = ctk.CTkFrame(self.telemetry_card, fg_color="transparent")
        telem_grid.pack(fill="x", padx=14, pady=(0, 14))
        telem_grid.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="board_telem")

        t_cols = (3, 2, 1, 0) if L["is_rtl"] else (0, 1, 2, 3)
        t_v, self.lbl_telem_volt = make_telemetry_tile(
            telem_grid,
            self._L(self._tr("ui.battery_voltage_fuel_gauge")),
            self._tr("ui.mv"),
            ACCENT_BLUE,
            L,
        )
        t_v.grid(row=0, column=t_cols[0], padx=4, sticky="nsew")

        t_c, self.lbl_telem_curr = make_telemetry_tile(
            telem_grid,
            self._L(self._tr("ui.battery_current_incl_usb_charging")),
            self._tr("ui.ma"),
            ACCENT_AMBER,
            L,
        )
        t_c.grid(row=0, column=t_cols[1], padx=4, sticky="nsew")

        t_bt, self.lbl_telem_batt_temp = make_telemetry_tile(
            telem_grid,
            self._L(self._tr("ui.battery_temp")),
            "— °C",
            ACCENT_GREEN,
            L,
        )
        t_bt.grid(row=0, column=t_cols[2], padx=4, sticky="nsew")

        t_ct, self.lbl_telem_cpu_temp = make_telemetry_tile(
            telem_grid,
            self._L(self._tr("ui.peak_cpu_temp")),
            "— °C",
            ACCENT_RED,
            L,
        )
        t_ct.grid(row=0, column=t_cols[3], padx=4, sticky="nsew")

        # PMIC Status Card
        self.pmic_card, badge = make_card(
            wrap, self._L(self._tr("card_pmic")), ACCENT_BLUE, L, show_badge=True
        )
        self.status_pills["pmic"] = badge
        self.pmic_text = make_console_box(
            self.pmic_card,
            135,
            L,
            self._L(self._tr("ui.click_analyze_pmic_charging_ic_to")),
        )

        # Fast Charge Protocol & Power Card
        self.fast_charge_card, badge = make_card(
            wrap, self._L(self._tr("card_fast_charge")), ACCENT_GREEN, L, show_badge=True
        )
        self.status_pills["fast_charge"] = badge
        self.fast_charge_text = make_console_box(
            self.fast_charge_card,
            115,
            L,
            self._L(self._tr("ui.click_diagnose_fast_charge_protocol_to")),
        )

        # NAND Flash Wear & Speed Benchmark Card
        self.nand_card, badge = make_card(
            wrap, self._L(self._tr("card_nand_wear")), ACCENT_PURPLE, L, show_badge=True
        )
        self.status_pills["nand"] = badge
        self.nand_text = make_console_box(
            self.nand_card,
            120,
            L,
            self._L(self._tr("ui.click_emmc_ufs_lifespan_wear_or")),
        )

        # 30-Second CPU & PMIC Load / Stress Test Card
        self.stress_card, badge = make_card(
            wrap, self._L(self._tr("card_stress_test")), ACCENT_RED, L, show_badge=True
        )
        self.status_pills["stress"] = badge
        self.stress_progress = ctk.CTkProgressBar(
            self.stress_card, height=10, corner_radius=5, fg_color=BORDER_SUBTLE, progress_color=ACCENT_RED
        )
        self.stress_progress.set(0)
        self.stress_progress.pack(fill="x", padx=14, pady=(2, 6))
        self.stress_lbl = ctk.CTkLabel(
            self.stress_card,
            text=self._L(self._tr("ui.ready_will_apply_a_30s_multi")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_SECONDARY,
            anchor=L["anchor"],
            justify=L["justify"],
        )
        self.stress_lbl.pack(fill="x", padx=14, pady=(0, 6))
        self.stress_text = make_console_box(self.stress_card, 115, L)

        # BootROM / Dead Phone Diagnosis Card
        self.bootrom_card, badge = make_card(
            wrap, self._L(self._tr("card_bootrom")), ACCENT_PURPLE, L, show_badge=True
        )
        self.status_pills["bootrom"] = badge
        self.bootrom_text = make_console_box(
            self.bootrom_card,
            115,
            L,
            self._L(self._tr("ui.click_dead_phone_bootrom_usb_scanner")),
        )

        # Leakage Test Live Output Card
        self.leakage_card, badge = make_card(
            wrap, self._L(self._tr("card_leakage")), ACCENT_AMBER, L, show_badge=True
        )
        self.status_pills["leakage"] = badge
        self.leakage_progress = ctk.CTkProgressBar(
            self.leakage_card, height=10, corner_radius=5, fg_color=BORDER_SUBTLE, progress_color=ACCENT_RED
        )
        self.leakage_progress.set(0)
        self.leakage_progress.pack(fill="x", padx=14, pady=(2, 6))
        self.leakage_lbl = ctk.CTkLabel(
            self.leakage_card,
            text=self._L(self._tr("ui.ready_needs_wireless_adb_and_no")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_SECONDARY,
            anchor=L["anchor"],
            justify=L["justify"],
        )
        self.leakage_lbl.pack(fill="x", padx=14, pady=(0, 6))
        self.leakage_text = make_console_box(self.leakage_card, 115, L)

        # Thermal Zones & Kernel Panic Dual Grid
        dual_grid = ctk.CTkFrame(wrap, fg_color="transparent")
        dual_grid.pack(fill="both", expand=True, pady=(0, 12))
        dual_grid.grid_columnconfigure((0, 1), weight=1, uniform="board_dual")

        col_therm = 1 if L["is_rtl"] else 0
        col_panic = 0 if L["is_rtl"] else 1

        self.thermal_card, badge = make_card(
            dual_grid, self._L(self._tr("card_thermal")), ACCENT_AMBER, L, show_badge=True, pack_card=False
        )
        self.status_pills["thermal"] = badge
        self.thermal_card.grid(
            row=0, column=col_therm, padx=(6, 0) if L["is_rtl"] else (0, 6), sticky="nsew"
        )
        self.thermal_text = make_console_box(self.thermal_card, 180, L)

        self.panic_card, badge = make_card(
            dual_grid, self._L(self._tr("card_panics")), ACCENT_RED, L, show_badge=True, pack_card=False
        )
        self.status_pills["panic"] = badge
        self.panic_card.grid(
            row=0, column=col_panic, padx=(0, 6) if L["is_rtl"] else (6, 0), sticky="nsew"
        )
        self.panic_text = make_console_box(self.panic_card, 180, L)

        # Blackbox Forensics Card
        self.blackbox_card, badge = make_card(
            wrap, self._L(self._tr("card_blackbox")), ACCENT_BLUE, L, show_badge=True
        )
        self.status_pills["blackbox"] = badge
        self.blackbox_text = make_console_box(
            self.blackbox_card,
            140,
            L,
            self._L(self._tr("ui.click_run_blackbox_forensics_to_parse")),
        )

        # Root Forensics Pro & EFS Backup Card
        self.root_forensics_card, badge = make_card(
            wrap, self._L(self._tr("card_root_forensics")), ACCENT_PURPLE, L, show_badge=True
        )
        self.status_pills["root_forensics"] = badge

        rf_btn_row = ctk.CTkFrame(self.root_forensics_card, fg_color="transparent")
        rf_btn_row.pack(fill="x", padx=14, pady=(0, 8))
        self.btn_run_rf = make_button(
            rf_btn_row, self._L(self._tr("btn_run_root_forensics")), self.run_root_forensics_async, variant="purple"
        )
        self.btn_run_rf.pack(side=L["lead"], padx=4)
        self.btn_backup_efs_card = make_button(
            rf_btn_row, self._L(self._tr("btn_backup_efs_raw")), self.run_efs_backup_async, variant="success"
        )
        self.btn_backup_efs_card.pack(side=L["lead"], padx=4)

        self.root_forensics_text = make_console_box(
            self.root_forensics_card,
            140,
            L,
            self._L(self._tr("ui.click_kernel_ramoops_forensics_root_to")),
        )

    # =========================================================================
    # TAB 3: HARDWARE, SENSORS & PERIPHERALS TESTER
    # =========================================================================
    def _build_hardware_tester_tab(self):
        L = self._layout()
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color=BG_CANVAS)
        self.tab_frames["hardware_tester"] = tab

        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        make_hero_header(
            wrap,
            self._L(self._tr("hw_title")),
            self._L(self._tr("hw_sub")),
            L,
            accent_color=ACCENT_BLUE,
        )

        # Responsive 3x3 Button Grid (Zero clipping on all widths)
        hw_specs = [
            (self._L(self._tr("btn_audit_hw")), self.run_hardware_audit_async, "primary"),
            (self._L(self._tr("ui.test_physical_keys_live")), self.run_buttons_test_async, "secondary"),
            (self._L(self._tr("btn_storage_bench")), self.run_storage_benchmark_async, "purple"),
            (self._L(self._tr("btn_test_vib")), self._trigger_vibration, "secondary"),
            (self._L(self._tr("btn_test_audio")), self._trigger_audio, "secondary"),
            (self._L(self._tr("btn_speaker_test")), self.run_speaker_tone_async, "success"),
            (self._L(self._tr("btn_lcd_color_test")), self.run_screen_color_test_async, "warning"),
            (self._L(self._tr("btn_touch_matrix")), self.run_touch_matrix_async, "cyan"),
            (self._L(self._tr("btn_companion_diag")), self.run_companion_diag_async, "purple"),
        ]
        _, h_btns = pack_button_grid(
            wrap, hw_specs, cols=3, is_rtl=L["is_rtl"], pady=(0, 8), height=36
        )
        (
            self.btn_audit_hw,
            self.btn_test_keys,
            self.btn_storage_bench,
            self.btn_test_vib,
            self.btn_test_audio,
            self.btn_speaker_test,
            self.btn_lcd_color_test,
            self.btn_touch_matrix,
            self.btn_companion_diag,
        ) = h_btns

        # Touch Overlay Switch Strip
        sw_strip = ctk.CTkFrame(
            wrap,
            fg_color=BG_CARD,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        sw_strip.pack(fill="x", pady=(0, 12))
        self.switch_pointer = ctk.CTkSwitch(
            sw_strip,
            text=self._L(self._tr("switch_pointer")),
            variable=self.pointer_location_var,
            command=self._toggle_pointer_location,
            progress_color=ACCENT_BLUE,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        )
        self.switch_pointer.pack(side=L["lead"], padx=14, pady=8)

        col_a = 1 if L["is_rtl"] else 0
        col_b = 0 if L["is_rtl"] else 1
        pad_a = (6, 0) if L["is_rtl"] else (0, 6)
        pad_b = (0, 6) if L["is_rtl"] else (6, 0)

        # Content Grid 1: Sensors & Cameras
        hw_grid = ctk.CTkFrame(wrap, fg_color="transparent")
        hw_grid.pack(fill="both", expand=True, pady=(0, 12))
        hw_grid.grid_columnconfigure((0, 1), weight=1, uniform="hw_dual_top")

        self.sensor_card, badge = make_card(
            hw_grid, self._L(self._tr("card_sensors")), ACCENT_BLUE, L, show_badge=True, pack_card=False
        )
        self.status_pills["sensors"] = badge
        self.sensor_card.grid(row=0, column=col_a, padx=pad_a, sticky="nsew")
        self.sensors_text = make_console_box(self.sensor_card, 210, L)

        self.camera_card, badge = make_card(
            hw_grid, self._L(self._tr("card_cameras")), ACCENT_CYAN, L, show_badge=True, pack_card=False
        )
        self.status_pills["cameras"] = badge
        self.camera_card.grid(row=0, column=col_b, padx=pad_b, sticky="nsew")
        self.camera_text = make_console_box(self.camera_card, 210, L)

        # Content Grid 2: Low-Level Peripherals
        periph_grid = ctk.CTkFrame(wrap, fg_color="transparent")
        periph_grid.pack(fill="both", expand=True)
        periph_grid.grid_columnconfigure((0, 1), weight=1, uniform="hw_dual_bot")

        def _grid_card(row: int, col: int, padx: tuple[int, int], pady: tuple[int, int], title_key: str, accent: str, pill_key: str | None, height: int, init_key: str | None = None):
            card, bdg = make_card(
                periph_grid,
                self._L(self._tr(title_key)),
                accent,
                L,
                show_badge=(pill_key is not None),
                pack_card=False,
            )
            if pill_key and bdg is not None:
                self.status_pills[pill_key] = bdg
            card.grid(row=row, column=col, padx=padx, pady=pady, sticky="nsew")
            init_txt = self._L(self._tr(init_key)) if init_key else ""
            box = make_console_box(card, height, L, init_txt)
            return card, box

        self.fp_card, self.fp_text = _grid_card(0, col_a, pad_a, (0, 12), "card_fingerprint", ACCENT_BLUE, "fp", 140)
        self.keys_card, self.keys_text = _grid_card(0, col_b, pad_b, (0, 12), "card_keys", ACCENT_GREEN, "keys", 140)

        self.sim_card, self.sim_text = _grid_card(1, col_a, pad_a, (0, 12), "card_sim", ACCENT_CYAN, "sim", 140)
        self.backlight_card, self.backlight_text = _grid_card(1, col_b, pad_b, (0, 12), "card_backlight", ACCENT_AMBER, "backlight", 140)

        self.wifi_card, self.wifi_text = _grid_card(2, col_a, pad_a, (0, 12), "card_wifi", ACCENT_BLUE, "wifi", 140)
        self.bt_card, self.bt_text = _grid_card(2, col_b, pad_b, (0, 12), "card_bluetooth", ACCENT_PURPLE, "bt", 140)

        self.chips_card, badge = make_card(
            periph_grid, self._L(self._tr("card_chips")), ACCENT_CYAN, L, show_badge=True, pack_card=False
        )
        self.status_pills["chips"] = badge
        self.chips_card.grid(row=3, column=0, columnspan=2, padx=0, pady=(0, 12), sticky="nsew")
        self.chips_text = make_console_box(self.chips_card, 110, L)

        self.storage_card, self.storage_text = _grid_card(
            4, col_a, pad_a, (0, 12), "card_storage_bench", ACCENT_PURPLE, "storage", 145, "ui.click_test_storage_wear_speed_to"
        )
        self.touch_card, self.touch_text = _grid_card(
            4, col_b, pad_b, (0, 12), "card_touch_matrix", ACCENT_CYAN, None, 145, "ui.click_interactive_touch_digitizer_grid_test"
        )

        self.audio_codec_card, self.audio_codec_text = _grid_card(
            5, col_a, pad_a, (0, 0), "card_audio_codec", "#f472b6", "audio_codec", 145, "ui.inspects_alsa_soundcards_audio_codec_ic"
        )
        self.panel_info_card, self.panel_info_text = _grid_card(
            5, col_b, pad_b, (0, 0), "card_panel_info", ACCENT_AMBER, "panel_info", 145, "ui.reads_the_kernel_reported_display_controller"
        )

    # =========================================================================
    # TAB 4: MALWARE HUNTER & SPYWARE AUDITOR
    # =========================================================================
    def _build_malware_hunter_tab(self):
        L = self._layout()
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color=BG_CANVAS)
        self.tab_frames["malware_hunter"] = tab

        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        make_hero_header(
            wrap,
            self._L(self._tr("malware_title")),
            self._L(self._tr("malware_sub")),
            L,
            accent_color=ACCENT_RED,
        )

        # Responsive 3x2 Button Grid
        sec_specs = [
            (self._L(self._tr("btn_run_sec")), self.run_security_audit_async, "danger"),
            (self._L(self._tr("btn_sniff_net")), self.run_network_sniff_async, "secondary"),
            (self._L(self._tr("btn_scan_bloat")), self.run_oem_bloat_scan_async, "purple"),
            (self._L(self._tr("btn_debloat_selected")), self.run_safe_debloat_all_async, "danger"),
            (self._L(self._tr("btn_restore_debloat")), self.run_restore_all_debloated_async, "success"),
            (self._L(self._tr("btn_rollback")), self._show_rollback_dialog, "secondary"),
        ]
        _, s_btns = pack_button_grid(
            wrap, sec_specs, cols=3, is_rtl=L["is_rtl"], pady=(0, 10), height=36
        )
        (
            self.btn_run_sec,
            self.btn_sniff_net,
            self.btn_scan_bloat,
            self.btn_debloat_all,
            self.btn_restore_bloat,
            self.btn_view_rollback,
        ) = s_btns

        # Scan Status Card
        prog_card = ctk.CTkFrame(
            wrap,
            fg_color=BG_CARD,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        prog_card.pack(fill="x", pady=(0, 12))
        self.sec_progress = ctk.CTkProgressBar(
            prog_card, height=10, corner_radius=5, fg_color=BORDER_SUBTLE, progress_color=ACCENT_RED
        )
        self.sec_progress.set(0)
        self.sec_progress.pack(fill="x", padx=14, pady=(12, 6))
        self.sec_status_lbl = ctk.CTkLabel(
            prog_card,
            text=self._L(self._tr("ui.scanner_ready")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_SECONDARY,
            anchor=L["anchor"],
            justify=L["justify"],
        )
        self.sec_status_lbl.pack(fill="x", padx=14, pady=(0, 10))

        # Flagged Threats Box
        self.threats_card, badge = make_card(
            wrap, self._L(self._tr("card_threats")), ACCENT_RED, L, show_badge=True
        )
        self.status_pills["user_ca"] = badge
        self.threats_text = make_console_box(self.threats_card, 210, L, pady=(0, 10))

        # Responsive Neutralization Row
        neutral_bar = ctk.CTkFrame(self.threats_card, fg_color="transparent")
        neutral_bar.pack(fill="x", padx=14, pady=(0, 14))
        neutral_bar.grid_columnconfigure(1 if L["is_rtl"] else 1, weight=1)

        self.neutral_pkg_entry = ctk.CTkEntry(
            neutral_bar, placeholder_text="com.suspicious.app", height=32
        )
        style_entry(self.neutral_pkg_entry, L)

        lbl_pkg = ctk.CTkLabel(
            neutral_bar,
            text=self._L(self._tr("target_pkg")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=TEXT_SECONDARY,
            anchor=L["anchor"],
        )
        btn_res = make_button(
            neutral_bar, self._L(self._tr("btn_restrict")), lambda: self._neutralize_selected("restrict"), variant="secondary", height=32
        )
        btn_dis = make_button(
            neutral_bar, self._L(self._tr("btn_disable")), lambda: self._neutralize_selected("disable"), variant="warning", height=32
        )
        btn_uni = make_button(
            neutral_bar, self._L(self._tr("btn_uninstall")), lambda: self._neutralize_selected("uninstall"), variant="danger", height=32
        )

        if L["is_rtl"]:
            neutral_bar.grid_columnconfigure(3, weight=1)
            btn_uni.grid(row=0, column=0, padx=3)
            btn_dis.grid(row=0, column=1, padx=3)
            btn_res.grid(row=0, column=2, padx=3)
            self.neutral_pkg_entry.grid(row=0, column=3, padx=6, sticky="ew")
            lbl_pkg.grid(row=0, column=4, padx=(6, 0))
        else:
            neutral_bar.grid_columnconfigure(1, weight=1)
            lbl_pkg.grid(row=0, column=0, padx=(0, 6))
            self.neutral_pkg_entry.grid(row=0, column=1, padx=6, sticky="ew")
            btn_res.grid(row=0, column=2, padx=3)
            btn_dis.grid(row=0, column=3, padx=3)
            btn_uni.grid(row=0, column=4, padx=3)

        # Network Sockets Box
        self.net_card, _ = make_card(wrap, self._L(self._tr("card_net")), ACCENT_CYAN, L)
        self.net_text = make_console_box(self.net_card, 160, L)

        # Safe OEM System Debloater Card (UAD-ng)
        self.bloat_card, _ = make_card(
            wrap, self._L(self._tr("card_bloatware")), ACCENT_PURPLE, L, pady=(0, 0)
        )
        self.bloat_text = make_console_box(
            self.bloat_card,
            150,
            L,
            self._L(self._tr("ui.click_scan_oem_bloatware_to_audit")),
        )

    # =========================================================================
    # TAB 5: SYSTEM OPTIMIZER
    # =========================================================================
    def _build_optimizer_tab(self):
        L = self._layout()
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color=BG_CANVAS)
        self.tab_frames["optimizer"] = tab

        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        make_hero_header(
            wrap,
            self._L(self._tr("opt_title")),
            self._L(self._tr("opt_sub")),
            L,
            accent_color=ACCENT_GREEN,
        )

        # Action & Progress Card
        act_card = ctk.CTkFrame(
            wrap,
            fg_color=BG_CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        act_card.pack(fill="x", pady=(0, 12))

        top_row = ctk.CTkFrame(act_card, fg_color="transparent")
        top_row.pack(fill="x", padx=14, pady=(12, 8))

        self.btn_run_opt = make_button(
            top_row,
            self._L(self._tr("btn_run_opt")),
            self.run_optimizer_async,
            variant="success",
            height=38,
        )
        self.btn_run_opt.pack(side=L["lead"])

        self.opt_progress = ctk.CTkProgressBar(
            act_card, height=11, corner_radius=6, fg_color=BORDER_SUBTLE, progress_color=ACCENT_GREEN
        )
        self.opt_progress.set(0)
        self.opt_progress.pack(fill="x", padx=14, pady=(2, 6))

        self.opt_status_lbl = ctk.CTkLabel(
            act_card,
            text=self._L(self._tr("ui.optimizer_idle")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_SECONDARY,
            anchor=L["anchor"],
            justify=L["justify"],
        )
        self.opt_status_lbl.pack(fill="x", padx=14, pady=(0, 12))

        # Before & After Comparison Cards
        comp_grid = ctk.CTkFrame(wrap, fg_color="transparent")
        comp_grid.pack(fill="x", pady=(0, 12))
        comp_grid.grid_columnconfigure((0, 1, 2), weight=1, uniform="opt_kpi")

        o_cols = (2, 1, 0) if L["is_rtl"] else (0, 1, 2)
        self.card_ram_gain, self.lbl_ram_gain = make_telemetry_tile(
            comp_grid, self._L(self._tr("gain_ram")), self._tr("ui.0_0_mb"), ACCENT_BLUE, L
        )
        self.card_ram_gain.grid(row=0, column=o_cols[0], padx=4, sticky="nsew")

        self.card_storage_gain, self.lbl_storage_gain = make_telemetry_tile(
            comp_grid, self._L(self._tr("gain_storage")), self._tr("ui.0_0_mb"), ACCENT_GREEN, L
        )
        self.card_storage_gain.grid(row=0, column=o_cols[1], padx=4, sticky="nsew")

        self.card_drain_gain, self.lbl_drain_gain = make_telemetry_tile(
            comp_grid, self._L(self._tr("gain_drain")), self._tr("ui.0_0_ma"), ACCENT_AMBER, L
        )
        self.card_drain_gain.grid(row=0, column=o_cols[2], padx=4, sticky="nsew")

        # Live Execution Terminal
        self.opt_log_card, badge = make_card(
            wrap, self._L(self._tr("card_opt_log")), ACCENT_GREEN, L, show_badge=True, pady=(0, 0)
        )
        self.status_pills["maint"] = badge
        self.opt_log_text = make_console_box(self.opt_log_card, 260, L)

    # =========================================================================
    # TAB 6: RESCUE & SYSTEM RECOVERY
    # =========================================================================
    def _build_rescue_tab(self):
        L = self._layout()
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color=BG_CANVAS)
        self.tab_frames["rescue_center"] = tab

        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        make_hero_header(
            wrap,
            self._L(self._tr("rescue_title")),
            self._L(self._tr("rescue_sub")),
            L,
            accent_color=ACCENT_PURPLE,
        )

        # Helper to build 3-col / N-col inline action rows inside a card without clipping
        def _card_btn_grid(card, specs, cols=3, pady=(0, 10)):
            bar = ctk.CTkFrame(card, fg_color="transparent")
            bar.pack(fill="x", padx=14, pady=pady)
            eff = max(1, min(cols, len(specs)))
            for c in range(eff):
                bar.grid_columnconfigure(c, weight=1, uniform=f"cgrid_{eff}")
            out_btns = []
            wrap_w = max(140, int(680 // eff) - 20)
            for idx, (label, cmd, variant) in enumerate(specs):
                r = idx // eff
                coff = idx % eff
                col = (eff - 1 - coff) if L["is_rtl"] else coff
                b = make_button(bar, label, cmd, variant=variant, height=34, font_size=11)
                try:
                    b._text_label.configure(wraplength=wrap_w, justify="center")
                except Exception:
                    pass
                b.grid(row=r, column=col, padx=4, pady=3, sticky="ew")
                out_btns.append(b)
            return out_btns

        # 1. Scrcpy Mirroring Card
        mirror_card, _ = make_card(wrap, self._L(self._tr("card_mirror")), ACCENT_BLUE, L)
        m_btns = _card_btn_grid(
            mirror_card,
            [
                (self._L(self._tr("btn_launch_mirror")), self._launch_scrcpy, "primary"),
                (self._L(self._tr("btn_stop_mirror")), self._stop_scrcpy, "secondary"),
                (self._L(self._tr("btn_scrcpy_otg")), self._launch_scrcpy_otg, "purple"),
            ],
            cols=3,
            pady=(0, 6),
        )
        self.btn_launch_mirror, self.btn_stop_mirror, self.btn_launch_otg = m_btns

        chk_row = ctk.CTkFrame(mirror_card, fg_color="transparent")
        chk_row.pack(fill="x", padx=14, pady=(0, 12))
        self.mirror_off_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            chk_row,
            text=self._L(self._tr("check_turn_off")),
            variable=self.mirror_off_var,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).pack(side=L["lead"], padx=4)

        # 2. Virtual On-Screen Control Pad Card
        self.vpad_card, _ = make_card(wrap, self._L(self._tr("card_vpad")), ACCENT_CYAN, L)
        vpad_row1 = ctk.CTkFrame(self.vpad_card, fg_color="transparent")
        vpad_row1.pack(fill="x", padx=14, pady=(2, 8))

        lbl_pin = ctk.CTkLabel(
            vpad_row1,
            text=self._L(self._tr("lbl_vpad_pin")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=TEXT_SECONDARY,
        )
        self.vpad_pin_entry = ctk.CTkEntry(vpad_row1, placeholder_text="1234", height=32)
        style_entry(self.vpad_pin_entry, L)
        b_pin = make_button(vpad_row1, self._L(self._tr("btn_send_pin")), self._send_virtual_pin, variant="primary", height=32)
        b_ent = make_button(vpad_row1, self._L(self._tr("btn_send_enter")), lambda: self._send_virtual_key(66), variant="secondary", height=32)
        b_swp = make_button(vpad_row1, self._L(self._tr("btn_swipe_unlock")), self._send_virtual_swipe, variant="success", height=32)

        if L["is_rtl"]:
            vpad_row1.grid_columnconfigure(3, weight=1)
            b_swp.grid(row=0, column=0, padx=3, sticky="ew")
            b_ent.grid(row=0, column=1, padx=3, sticky="ew")
            b_pin.grid(row=0, column=2, padx=3, sticky="ew")
            self.vpad_pin_entry.grid(row=0, column=3, padx=6, sticky="ew")
            lbl_pin.grid(row=0, column=4, padx=(6, 0))
        else:
            vpad_row1.grid_columnconfigure(1, weight=1)
            lbl_pin.grid(row=0, column=0, padx=(0, 6))
            self.vpad_pin_entry.grid(row=0, column=1, padx=6, sticky="ew")
            b_pin.grid(row=0, column=2, padx=3, sticky="ew")
            b_ent.grid(row=0, column=3, padx=3, sticky="ew")
            b_swp.grid(row=0, column=4, padx=3, sticky="ew")

        _card_btn_grid(
            self.vpad_card,
            [
                (self._L(self._tr("btn_key_power")), lambda: self._send_virtual_key(26), "danger"),
                (self._L(self._tr("btn_key_vol_up")), lambda: self._send_virtual_key(24), "secondary"),
                (self._L(self._tr("btn_key_vol_down")), lambda: self._send_virtual_key(25), "secondary"),
                (self._L(self._tr("btn_key_home")), lambda: self._send_virtual_key(3), "secondary"),
                (self._L(self._tr("btn_key_back")), lambda: self._send_virtual_key(4), "secondary"),
                (self._L(self._tr("btn_key_menu")), lambda: self._send_virtual_key(82), "secondary"),
            ],
            cols=3,
            pady=(0, 12),
        )

        # 3. Wireless ADB Card
        self.wireless_card, _ = make_card(wrap, self._L(self._tr("card_wireless_adb")), ACCENT_GREEN, L)

        w_row1 = ctk.CTkFrame(self.wireless_card, fg_color="transparent")
        w_row1.pack(fill="x", padx=14, pady=(2, 8))
        make_button(
            w_row1, self._L(self._tr("btn_enable_wireless")), self._enable_wireless_adb, variant="success", height=32
        ).pack(side=L["lead"], padx=4)
        self.lbl_wireless_ip = ctk.CTkLabel(
            w_row1,
            text=self._tr("ui.ip"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color="#34d399",
            fg_color=BG_TILE,
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.lbl_wireless_ip.pack(side=L["lead"], padx=8)

        w_row2 = ctk.CTkFrame(self.wireless_card, fg_color="transparent")
        w_row2.pack(fill="x", padx=14, pady=(0, 8))
        lbl_wt = ctk.CTkLabel(w_row2, text=self._L(self._tr("lbl_wireless_target")), font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=TEXT_SECONDARY)
        self.entry_wireless_addr = ctk.CTkEntry(w_row2, placeholder_text="192.168.1.100:5555", height=32)
        style_entry(self.entry_wireless_addr, L)
        b_wc = make_button(w_row2, self._L(self._tr("btn_wireless_connect")), self._connect_wireless_adb, variant="primary", height=32)
        b_wd = make_button(w_row2, self._L(self._tr("btn_disconnect_wireless")), self._disconnect_wireless_adb, variant="secondary", height=32)

        if L["is_rtl"]:
            w_row2.grid_columnconfigure(2, weight=1)
            b_wd.grid(row=0, column=0, padx=3)
            b_wc.grid(row=0, column=1, padx=3)
            self.entry_wireless_addr.grid(row=0, column=2, padx=6, sticky="ew")
            lbl_wt.grid(row=0, column=3, padx=(6, 0))
        else:
            w_row2.grid_columnconfigure(1, weight=1)
            lbl_wt.grid(row=0, column=0, padx=(0, 6))
            self.entry_wireless_addr.grid(row=0, column=1, padx=6, sticky="ew")
            b_wc.grid(row=0, column=2, padx=3)
            b_wd.grid(row=0, column=3, padx=3)

        w_row3 = ctk.CTkFrame(self.wireless_card, fg_color="transparent")
        w_row3.pack(fill="x", padx=14, pady=(0, 10))
        lbl_wp = ctk.CTkLabel(w_row3, text=self._L(self._tr("lbl_wireless_pair")), font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=TEXT_SECONDARY)
        self.entry_pair_addr = ctk.CTkEntry(w_row3, placeholder_text="192.168.1.100:37123", width=150, height=32)
        style_entry(self.entry_pair_addr, L)
        lbl_wk = ctk.CTkLabel(w_row3, text=self._L(self._tr("lbl_wireless_code")), font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=TEXT_SECONDARY)
        self.entry_pair_code = ctk.CTkEntry(w_row3, placeholder_text="123456", width=85, height=32)
        style_entry(self.entry_pair_code, L)
        b_pair = make_button(w_row3, self._L(self._tr("btn_pair_wireless")), self._pair_wireless_adb, variant="purple", height=32, width=105)

        if L["is_rtl"]:
            w_row3.grid_columnconfigure(3, weight=1)
            b_pair.grid(row=0, column=0, padx=3)
            self.entry_pair_code.grid(row=0, column=1, padx=4)
            lbl_wk.grid(row=0, column=2, padx=4)
            self.entry_pair_addr.grid(row=0, column=3, padx=6, sticky="ew")
            lbl_wp.grid(row=0, column=4, padx=(6, 0))
        else:
            w_row3.grid_columnconfigure(1, weight=1)
            lbl_wp.grid(row=0, column=0, padx=(0, 6))
            self.entry_pair_addr.grid(row=0, column=1, padx=6, sticky="ew")
            lbl_wk.grid(row=0, column=2, padx=4)
            self.entry_pair_code.grid(row=0, column=3, padx=4)
            b_pair.grid(row=0, column=4, padx=3)

        self.wireless_log_text = make_console_box(
            self.wireless_card, 85, L, self._L(self._tr("ui.to_enable_wireless_adb_connect_device"))
        )

        # 4. Setup Wizard & System Provisioning Card
        prov_card, _ = make_card(wrap, self._L(self._tr("card_provisioning")), ACCENT_BLUE, L)
        _card_btn_grid(
            prov_card,
            [
                (self._L(self._tr("btn_check_prov")), self._check_provisioning, "secondary"),
                (self._L(self._tr("btn_fix_prov")), self._fix_provisioning, "success"),
                (self._L(self._tr("btn_open_settings")), self._launch_settings, "primary"),
            ],
            cols=3,
        )
        self.prov_log_text = make_console_box(
            prov_card, 90, L, self._L(self._tr("ui.click_inspect_setup_flags_to_verify"))
        )

        # 5. Fastboot Rescue Card
        fb_card, _ = make_card(wrap, self._L(self._tr("card_fb")), ACCENT_RED, L)
        _card_btn_grid(
            fb_card,
            [
                (self._L(self._tr("btn_fb_vars")), self._read_fastboot_vars, "secondary"),
                (self._L(self._tr("btn_fix_bootloop")), self._fix_cache_bootloop, "danger"),
                (self._L(self._tr("btn_fb_reboot")), lambda: self.bridge.fastboot_reboot("", self.current_serial.get()), "secondary"),
            ],
            cols=3,
        )

        flash_bar = ctk.CTkFrame(fb_card, fg_color="transparent")
        flash_bar.pack(fill="x", padx=14, pady=(0, 10))
        lbl_part = ctk.CTkLabel(flash_bar, text=self._L(self._tr("lbl_partition")), font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=TEXT_SECONDARY)
        self.part_combo = ctk.CTkComboBox(
            flash_bar,
            values=["boot", "recovery", "vbmeta", "dtbo", "system"],
            width=110,
            height=32,
            fg_color=BG_INPUT,
            border_color=BORDER_ACCENT,
            button_color=ACCENT_BLUE,
            dropdown_fg_color=BG_CARD,
        )
        self.img_path_entry = ctk.CTkEntry(flash_bar, placeholder_text=self._tr("ui.path_to_image_img"), width=130, height=32)
        style_entry(self.img_path_entry)
        b_br = make_button(flash_bar, self._L(self._tr("btn_browse")), self._browse_image_file, variant="secondary", height=32, width=80)
        b_fl = make_button(flash_bar, self._L(self._tr("btn_flash")), self._flash_partition, variant="primary", height=32, width=90)

        if L["is_rtl"]:
            flash_bar.grid_columnconfigure(2, weight=1)
            b_fl.grid(row=0, column=0, padx=3)
            b_br.grid(row=0, column=1, padx=3)
            self.img_path_entry.grid(row=0, column=2, padx=6, sticky="ew")
            self.part_combo.grid(row=0, column=3, padx=4)
            lbl_part.grid(row=0, column=4, padx=(6, 0))
        else:
            flash_bar.grid_columnconfigure(2, weight=1)
            lbl_part.grid(row=0, column=0, padx=(0, 6))
            self.part_combo.grid(row=0, column=1, padx=4)
            self.img_path_entry.grid(row=0, column=2, padx=6, sticky="ew")
            b_br.grid(row=0, column=3, padx=3)
            b_fl.grid(row=0, column=4, padx=3)

        self.rescue_log_text = make_console_box(fb_card, 120, L)

        # 6. Emergency Data Extraction Card
        data_card, _ = make_card(wrap, self._L(self._tr("card_data")), ACCENT_GREEN, L)

        data_row = ctk.CTkFrame(data_card, fg_color="transparent")
        data_row.pack(fill="x", padx=14, pady=(0, 6))
        lbl_cat = ctk.CTkLabel(data_row, text=self._L(self._tr("lbl_category")), font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=TEXT_SECONDARY)
        # Keep raw combo option strings untransformed so _pull_emergency_data .split()[0] works safely
        self.data_cat_combo = ctk.CTkComboBox(
            data_row,
            values=[
                self._tr("ui.all_photos_all_photos_dcim"),
                self._tr("ui.full_storage_full_storage_dump"),
                self._tr("ui.whatsapp_whatsapp_media_only_chats_are"),
                self._tr("ui.downloads_downloads"),
                self._tr("ui.documents_documents"),
            ],
            height=32,
            fg_color=BG_INPUT,
            border_color=BORDER_ACCENT,
            button_color=ACCENT_BLUE,
            dropdown_fg_color=BG_CARD,
        )
        if L["is_rtl"]:
            data_row.grid_columnconfigure(0, weight=1)
            self.data_cat_combo.grid(row=0, column=0, padx=(0, 8), sticky="ew")
            lbl_cat.grid(row=0, column=1)
        else:
            data_row.grid_columnconfigure(1, weight=1)
            lbl_cat.grid(row=0, column=0, padx=(0, 8))
            self.data_cat_combo.grid(row=0, column=1, sticky="ew")

        _card_btn_grid(
            data_card,
            [
                (self._L(self._tr("btn_pull_data")), self._pull_emergency_data, "success"),
                (self._L(self._tr("ui.1_click_full_photos_pull")), self._pull_all_photos_quick, "success"),
                (self._L(self._tr("btn_one_click_backup")), self._one_click_customer_backup, "cyan"),
            ],
            cols=3,
        )
        self.data_log_text = make_console_box(
            data_card, 110, L, self._L(self._tr("ui.emergency_data_and_photo_pull_from"))
        )

        # 7. ADB Sideload OTA ROM Flasher Card
        sideload_card, _ = make_card(wrap, self._L(self._tr("card_sideload")), ACCENT_CYAN, L)

        sideload_bar = ctk.CTkFrame(sideload_card, fg_color="transparent")
        sideload_bar.pack(fill="x", padx=14, pady=(0, 8))
        self.sideload_entry = ctk.CTkEntry(
            sideload_bar, placeholder_text=self._tr("ui.path_to_ota_update_package_zip"), height=32
        )
        style_entry(self.sideload_entry)
        b_ota = make_button(sideload_bar, self._L(self._tr("btn_browse_ota")), self._browse_ota_file, variant="secondary", height=32)
        b_sl = make_button(sideload_bar, self._L(self._tr("btn_flash_sideload")), self._flash_sideload, variant="cyan", height=32)

        if L["is_rtl"]:
            sideload_bar.grid_columnconfigure(2, weight=1)
            b_sl.grid(row=0, column=0, padx=3)
            b_ota.grid(row=0, column=1, padx=3)
            self.sideload_entry.grid(row=0, column=2, padx=(6, 0), sticky="ew")
        else:
            sideload_bar.grid_columnconfigure(0, weight=1)
            self.sideload_entry.grid(row=0, column=0, padx=(0, 6), sticky="ew")
            b_ota.grid(row=0, column=1, padx=3)
            b_sl.grid(row=0, column=2, padx=3)

        self.sideload_progress = ctk.CTkProgressBar(
            sideload_card, height=10, corner_radius=5, fg_color=BORDER_SUBTLE, progress_color=ACCENT_CYAN
        )
        self.sideload_progress.set(0)
        self.sideload_progress.pack(fill="x", padx=14, pady=(2, 6))

        self.sideload_lbl = ctk.CTkLabel(
            sideload_card,
            text=self._L(self._tr("ui.ready_put_device_in_recovery_mode")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_SECONDARY,
            anchor=L["anchor"],
            justify=L["justify"],
        )
        self.sideload_lbl.pack(fill="x", padx=14, pady=(0, 6))
        self.sideload_text = make_console_box(
            sideload_card, 100, L, self._L(self._tr("ui.stream_official_ota_rom_update_zip"))
        )

        # 8. Fastboot Active Slot (A/B) & Anti-Rollback (ARB) Card
        slot_card, _ = make_card(wrap, self._L(self._tr("card_ab_slot")), ACCENT_AMBER, L)
        _card_btn_grid(
            slot_card,
            [
                (self._L(self._tr("btn_switch_slot_a")), lambda: self._switch_slot("a"), "primary"),
                (self._L(self._tr("btn_switch_slot_b")), lambda: self._switch_slot("b"), "primary"),
                (self._L(self._tr("btn_check_arb")), self._check_arb, "danger"),
            ],
            cols=3,
        )
        self.slot_log_text = make_console_box(
            slot_card, 90, L, self._L(self._tr("ui.in_fastboot_switch_between_slot_a"))
        )

        # 9. Critical Partitions & Radio Backup Card
        part_card, _ = make_card(wrap, self._L(self._tr("card_partition_backup")), ACCENT_GREEN, L)
        _card_btn_grid(
            part_card,
            [
                (self._L(self._tr("btn_backup_critical")), self._backup_critical_partitions, "success"),
                (self._L(self._tr("btn_backup_all")), self._backup_all_partitions, "cyan"),
                (self._L(self._tr("btn_open_backups")), self._open_backups_folder, "secondary"),
            ],
            cols=3,
        )
        self.part_backup_text = make_console_box(
            part_card, 110, L, self._L(self._tr("ui.dump_and_backup_critical_radio_calibration"))
        )

        # 10. Batch APK Installer & Extractor Card
        apk_card, _ = make_card(wrap, self._L(self._tr("card_batch_apk")), ACCENT_PURPLE, L, pady=(0, 0))
        _card_btn_grid(
            apk_card,
            [
                (self._L(self._tr("btn_install_batch_apk")), self._batch_install_apks, "purple"),
                (self._L(self._tr("btn_extract_apps")), self._extract_user_apps, "secondary"),
            ],
            cols=2,
        )
        self.apk_log_text = make_console_box(
            apk_card, 110, L, self._L(self._tr("ui.batch_install_multiple_apk_files_r"))
        )

    # =========================================================================
    # TAB 7: REPORTS & CERTIFICATE EXPORTER
    # =========================================================================
    def _build_reports_tab(self):
        L = self._layout()
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color=BG_CANVAS)
        self.tab_frames["reports"] = tab

        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        make_hero_header(
            wrap,
            self._L(self._tr("reports_title")),
            self._L(self._tr("reports_sub")),
            L,
            accent_color=ACCENT_BLUE,
        )

        # Customer Intake & Ticket Card
        ticket_card, _ = make_card(wrap, self._L(self._tr("card_cust_ticket")), ACCENT_BLUE, L)

        t_grid = ctk.CTkFrame(ticket_card, fg_color="transparent")
        t_grid.pack(fill="x", padx=14, pady=(0, 10))
        t_grid.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="rep_form")

        cols = (3, 2, 1, 0) if L["is_rtl"] else (0, 1, 2, 3)

        ctk.CTkLabel(
            t_grid, text=self._L(self._tr("lbl_cust_name")), font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_SECONDARY, anchor=L["anchor"]
        ).grid(row=0, column=cols[0], padx=5, pady=2, sticky="ew")
        self.cust_name_entry = ctk.CTkEntry(t_grid, placeholder_text=self._tr("ui.john_doe"), height=32)
        style_entry(self.cust_name_entry, L)
        self.cust_name_entry.grid(row=1, column=cols[0], padx=5, pady=(0, 8), sticky="ew")

        ctk.CTkLabel(
            t_grid, text=self._L(self._tr("lbl_cust_phone")), font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_SECONDARY, anchor=L["anchor"]
        ).grid(row=0, column=cols[1], padx=5, pady=2, sticky="ew")
        self.cust_phone_entry = ctk.CTkEntry(t_grid, placeholder_text=self._tr("ui.970_59_xxxxxxx"), height=32)
        style_entry(self.cust_phone_entry, L)
        self.cust_phone_entry.grid(row=1, column=cols[1], padx=5, pady=(0, 8), sticky="ew")

        ctk.CTkLabel(
            t_grid, text=self._L(self._tr("lbl_cust_fault")), font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_SECONDARY, anchor=L["anchor"]
        ).grid(row=0, column=cols[2], padx=5, pady=2, sticky="ew")
        self.cust_fault_entry = ctk.CTkEntry(t_grid, placeholder_text=self._tr("ui.screen_charging_issue"), height=32)
        style_entry(self.cust_fault_entry, L)
        self.cust_fault_entry.grid(row=1, column=cols[2], padx=5, pady=(0, 8), sticky="ew")

        ctk.CTkLabel(
            t_grid, text=self._L(self._tr("lbl_repair_cost")), font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=TEXT_SECONDARY, anchor=L["anchor"]
        ).grid(row=0, column=cols[3], padx=5, pady=2, sticky="ew")
        self.cust_cost_entry = ctk.CTkEntry(t_grid, placeholder_text="0.00", height=32)
        style_entry(self.cust_cost_entry)
        self.cust_cost_entry.grid(row=1, column=cols[3], padx=5, pady=(0, 8), sticky="ew")

        # Technician Name and Certificate Action Footer
        btn_row = ctk.CTkFrame(ticket_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=14, pady=(0, 14))

        lbl_tech = ctk.CTkLabel(
            btn_row, text=self._L(self._tr("lbl_tech_name")), font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=TEXT_SECONDARY
        )
        self.tech_name_entry = ctk.CTkEntry(btn_row, height=34)
        style_entry(self.tech_name_entry, L)
        self.tech_name_entry.insert(0, self._tr("ui.chief_hardware_technician"))

        self.btn_export_cert = make_button(
            btn_row, self._L(self._tr("btn_gen_signed_cert")), self._generate_and_open_certificate, variant="success", height=36
        )
        btn_open_fld = make_button(
            btn_row, self._L(self._tr("btn_open_folder")), lambda: webbrowser.open(REPORTS_DIR), variant="secondary", height=36
        )

        if L["is_rtl"]:
            btn_row.grid_columnconfigure(2, weight=1)
            btn_open_fld.grid(row=0, column=0, padx=4)
            self.btn_export_cert.grid(row=0, column=1, padx=4)
            self.tech_name_entry.grid(row=0, column=2, padx=8, sticky="ew")
            lbl_tech.grid(row=0, column=3, padx=(6, 0))
        else:
            btn_row.grid_columnconfigure(1, weight=1)
            lbl_tech.grid(row=0, column=0, padx=(0, 6))
            self.tech_name_entry.grid(row=0, column=1, padx=8, sticky="ew")
            self.btn_export_cert.grid(row=0, column=2, padx=4)
            btn_open_fld.grid(row=0, column=3, padx=4)

        # Past Reports List
        reports_list_card, _ = make_card(
            wrap, self._L(self._tr("card_past_reports")), ACCENT_CYAN, L, pady=(0, 0)
        )
        self.reports_text = make_console_box(reports_list_card, 300, L)

    # =========================================================================
    # DEVICE MONITORING & ASYNC LOGIC
    # =========================================================================
    def _start_device_monitor(self):
        def monitor_loop():
            while True:
                try:
                    self.refresh_devices_sync()
                except Exception as e:
                    logger.debug(f"Device monitor error: {e}")
                time.sleep(3.0)

        t = threading.Thread(target=monitor_loop, daemon=True)
        t.start()

    def refresh_devices_sync(self):
        diag = self.bridge.get_device_connection_diagnostics()
        self.last_connection_diag = diag
        devices = diag.get("all_adb", [])
        self.available_devices = devices

        def update_ui():
            options = []
            for d in devices:
                label = f"{d['serial']} ({d.get('model') or d.get('state')})"
                options.append(label)

            unauthorized_devs = diag.get("unauthorized", [])
            offline_devs = diag.get("offline", [])
            pnp_phones = diag.get("pnp_phones", [])
            authorized_devs = diag.get("authorized", [])
            fastboot_devs = diag.get("fastboot", [])

            is_ar = self.current_lang == "ar"

            # 1. State: Unauthorized phone connected (Needs RSA Approval)
            if unauthorized_devs:
                serials_str = ", ".join(d["serial"] for d in unauthorized_devs)
                if hasattr(self, "alert_card"):
                    self.alert_card.configure(fg_color="#2a1215", border_color=ACCENT_RED)
                    self.alert_title_lbl.configure(
                        text=self._L(self._tr("ui.device_is_unauthorized", serials_str=serials_str)),
                        text_color="#f87171",
                    )
                    self.alert_desc_lbl.configure(
                        text=self._L(self._tr("alert_unauthorized_desc")),
                        text_color="#fecaca",
                    )
                    self.alert_card.pack(fill="x", pady=(0, 12))
                if hasattr(self, "guide_card"):
                    self.guide_card.pack_forget()

                self.adb_status_dot.configure(fg_color=ACCENT_RED)
                self.adb_status_label.configure(
                    text=self._L(self._tr("ui.adb_unauthorized_accept_rsa_prompt", serials_str=serials_str)),
                    text_color="#f87171",
                )

            # 2. State: Physical phone detected on Windows USB PnP, but ADB debugging is OFF
            elif diag.get("has_pnp_without_adb") and pnp_phones:
                phone_names = ", ".join(p["name"] for p in pnp_phones)
                if hasattr(self, "alert_card"):
                    self.alert_card.configure(fg_color="#241807", border_color=ACCENT_AMBER)
                    self.alert_title_lbl.configure(
                        text=self._L(self._tr("ui.phone_detected_on_usb_but_usb", phone_names=phone_names)),
                        text_color="#fbbf24",
                    )
                    desc_text = self._tr("alert_pnp_desc").replace("{names}", phone_names)
                    self.alert_desc_lbl.configure(
                        text=self._L(desc_text),
                        text_color="#fef3c7",
                    )
                    self.alert_card.pack(fill="x", pady=(0, 12))
                if hasattr(self, "guide_card"):
                    self.guide_card.pack_forget()

                self.adb_status_dot.configure(fg_color=ACCENT_AMBER)
                self.adb_status_label.configure(
                    text=self._L(self._tr("ui.usb_enable_usb_debugging", phone_names=phone_names)),
                    text_color="#fbbf24",
                )

            # 3. State: Device is offline
            elif offline_devs and not authorized_devs and not fastboot_devs:
                serials_str = ", ".join(d["serial"] for d in offline_devs)
                if hasattr(self, "alert_card"):
                    self.alert_card.configure(fg_color="#28160c", border_color="#ea580c")
                    self.alert_title_lbl.configure(
                        text=self._L(self._tr("ui.device_is_offline", serials_str=serials_str)),
                        text_color="#fb923c",
                    )
                    self.alert_desc_lbl.configure(
                        text=self._L(self._tr("alert_offline_desc")),
                        text_color="#fed7aa",
                    )
                    self.alert_card.pack(fill="x", pady=(0, 12))
                if hasattr(self, "guide_card"):
                    self.guide_card.pack_forget()

                self.adb_status_dot.configure(fg_color="#ea580c")
                self.adb_status_label.configure(
                    text=self._L(self._tr("ui.adb_offline_replug_usb_cable")),
                    text_color="#fb923c",
                )

            # 4. State: Fully authorized or Fastboot device ready!
            elif authorized_devs or fastboot_devs:
                if hasattr(self, "alert_card"):
                    self.alert_card.pack_forget()
                if hasattr(self, "guide_card"):
                    self.guide_card.pack_forget()

            # 5. State: Nothing connected at all
            else:
                if hasattr(self, "alert_card"):
                    self.alert_card.pack_forget()
                if hasattr(self, "guide_card"):
                    self.guide_card.pack(fill="x", pady=(0, 12))
                self.adb_status_dot.configure(fg_color=TEXT_MUTED)
                self.adb_status_label.configure(
                    text=self._L(self._tr("adb_disconnected")), text_color=TEXT_SECONDARY
                )

            # Update Dropdown items
            if not options:
                if pnp_phones:
                    options = [self._tr("ui.usb_no_adb", name=pnp_phones[0]['name'])]
                else:
                    options = [self._tr("no_devices")]

            self.device_combo.configure(values=options)

            cur = self.current_serial.get()
            ready_serials = [d["serial"] for d in authorized_devs + fastboot_devs]

            if cur not in ready_serials and (authorized_devs or fastboot_devs):
                chosen = (authorized_devs or fastboot_devs)[0]
                self.current_serial.set(chosen["serial"])
                self.device_combo.set(f"{chosen['serial']} ({chosen.get('model') or chosen.get('state')})")
                self.adb_status_dot.configure(fg_color=ACCENT_GREEN)
                self.adb_status_label.configure(
                    text=self._L(self._tr("ui.adb", upper=chosen['state'].upper(), serial=chosen['serial'])),
                    text_color="#34d399",
                )
                self.run_profile_async()
            elif cur in ready_serials:
                cur_dev = next(d for d in devices if d["serial"] == cur)
                self.adb_status_dot.configure(fg_color=ACCENT_GREEN)
                self.adb_status_label.configure(
                    text=self._L(self._tr("ui.adb_2", upper=cur_dev['state'].upper(), cur=cur)),
                    text_color="#34d399",
                )
            elif not ready_serials:
                self.current_serial.set("")

        try:
            if self.winfo_exists():
                self.after(0, update_ui)
        except (RuntimeError, tk.TclError):
            pass

    def refresh_devices_async(self):
        threading.Thread(target=self.refresh_devices_sync, daemon=True).start()

    def _on_device_selected(self, choice: str):
        if not choice or choice == self._tr("no_devices") or choice == "No Devices Found" or "USB:" in choice:
            return
        serial = choice.split()[0]
        self.current_serial.set(serial)
        self.run_profile_async()

    def _exec_reboot(self, mode: str):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.warning"), self._tr("ui.no_target_device_selected"))
            return
        threading.Thread(target=lambda: self.bridge.reboot(mode, serial), daemon=True).start()

    # =========================================================================
    # CORE OPERATIONS (ASYNC EXECUTION)
    # =========================================================================
    def run_profile_async(self):
        serial = self.current_serial.get()
        if not serial:
            return

        def task():
            profile = self.profiler.profile_device(serial)
            self.current_profile = profile

            def update_ui():
                self._ensure_tab("dashboard")
                self.spec_labels["model"].configure(text=f"{profile.brand} {profile.model}")
                self.spec_labels["serial"].configure(text=profile.serial)
                self.spec_labels["soc"].configure(text=f"{profile.soc_chipset} ({profile.hardware})")
                self.spec_labels["cores"].configure(text=self._L(self._tr("ui.cores", cpu_cores=profile.cpu_cores, cpu_architecture=profile.cpu_architecture)))
                self.spec_labels["android"].configure(text=self._L(self._tr("ui.android_api", android_version=profile.android_version, sdk_version=profile.sdk_version)))
                self.spec_labels["build_id"].configure(text=profile.build_id[:32])
                self.spec_labels["security"].configure(text=profile.security_patch)
                self.spec_labels["kernel"].configure(text=profile.kernel_version[:36] if profile.kernel_version else "Standard")

                root_str = self._L(self._tr("ui.rooted"))
                no_root_str = self._L(self._tr("ui.no_standard_oem"))
                self.spec_labels["root"].configure(
                    text=root_str if profile.is_rooted else no_root_str,
                    text_color=ACCENT_RED if profile.is_rooted else ACCENT_GREEN,
                )

                self.spec_labels["selinux"].configure(text=profile.selinux_mode)
                self.spec_labels["display"].configure(text=f"{profile.display_resolution} @ {profile.display_refresh_rate} ({profile.display_density})")

                # Update KPI summary tiles
                if "model" in self.kpi_labels:
                    self.kpi_labels["model"].configure(text=f"{profile.brand} {profile.model}".strip() or "—")
                if "android" in self.kpi_labels:
                    self.kpi_labels["android"].configure(text=f"{profile.android_version} (API {profile.sdk_version})")
                if "root" in self.kpi_labels:
                    self.kpi_labels["root"].configure(
                        text=root_str if profile.is_rooted else no_root_str,
                        text_color=ACCENT_RED if profile.is_rooted else ACCENT_GREEN,
                    )
                if "selinux" in self.kpi_labels:
                    self.kpi_labels["selinux"].configure(text=profile.selinux_mode or "—")

                # Gauges
                if profile.ram_total_mb > 0:
                    pct = profile.ram_used_pct / 100.0
                    self.ram_bar.set(pct)
                    self.ram_title_lbl.configure(text=self._L(self._tr("ui.ram_mb_free_mb", ram_free_mb=profile.ram_free_mb, ram_total_mb=profile.ram_total_mb, ram_used_pct=profile.ram_used_pct)))

                if profile.storage_total_gb > 0:
                    pct = profile.storage_used_pct / 100.0
                    self.storage_bar.set(pct)
                    self.storage_title_lbl.configure(text=self._L(self._tr("ui.storage_gb_free_gb", storage_free_gb=profile.storage_free_gb, storage_total_gb=profile.storage_total_gb, storage_used_pct=profile.storage_used_pct)))

                self.batt_bar.set(profile.battery_level_pct / 100.0)
                self.batt_title_lbl.configure(text=self._L(self._tr("ui.battery_2", battery_level_pct=profile.battery_level_pct, battery_status=profile.battery_status)))
                self.batt_quick_lbl.configure(text=self._L(self._tr("ui.voltage_mv_temp_c_health", battery_voltage_mv=profile.battery_voltage_mv, battery_temp_c=profile.battery_temp_c, battery_health=profile.battery_health)))

            try:
                if self.winfo_exists():
                    self.after(0, update_ui)
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=task, daemon=True).start()

    def run_pmic_diag_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        def task():
            pmic = self.board_diag.analyze_pmic(serial)
            is_ar = self.current_lang == "ar"

            v_display = f"{pmic.voltage_mv:.0f} mV ({pmic.voltage_mv / 1000.0:.2f} V) [{pmic.voltage_source}]" if (pmic.voltage_mv is not None and pmic.voltage_mv > 0) else "UNKNOWN"
            c_note = f" ({pmic.current_note})" if pmic.current_note else ""
            c_display = f"{pmic.current_ma:+.0f} mA [{pmic.current_source}]{c_note}" if pmic.current_ma is not None else "UNKNOWN"
            t_display = f"{pmic.temp_c:.1f} °C [{pmic.temp_source}]" if pmic.temp_c is not None else "UNKNOWN"
            h_display = f"{pmic.true_health_pct:.1f}% ({pmic.health_verdict})" if pmic.true_health_pct is not None else pmic.health_verdict

            out_lines = [
                self._tr("ui.battery_voltage_fuel_gauge_charging_ic", serial=serial),
                self._tr("ui.battery_voltage_fuel_gauge_2", v_display=v_display),
                self._tr("ui.battery_current", c_display=c_display),
                self._tr("ui.battery_temperature", t_display=t_display),
                self._tr("ui.charging_status", status=pmic.status),
                self._tr("ui.reported_health_os", health=pmic.health),
                self._tr("ui.true_calculated_health", h_display=h_display),
                self._tr("ui.design_capacity", unknown=f'{pmic.charge_full_design_uah} uAh' if pmic.charge_full_design_uah else 'UNKNOWN'),
                self._tr("ui.charge_cycle_count", unknown=pmic.cycle_count if pmic.cycle_count is not None else 'UNKNOWN'),
            ]
            if pmic.fault_flags:
                out_lines.append(self._tr("ui.potential_fault_indicators"))
                for f in pmic.fault_flags:
                    out_lines.append(f"  [!] {f}")
            elif pmic.voltage_status == "PASS":
                out_lines.append(self._tr("ui.no_fault_indicators_in_the_values"))
            else:
                out_lines.append(f"\n[{pmic.voltage_status}]")

            if pmic.evidence:
                out_lines.append(format_evidence_for_display(pmic.evidence, is_ar=is_ar))

            def update_ui():
                self._ensure_tab("board_diag")
                self._set_box(self.pmic_text, "\n".join(out_lines))
                self._set_pill("pmic", getattr(pmic, "voltage_status", "UNKNOWN"))

            try:
                if self.winfo_exists():
                    self.after(0, update_ui)
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=task, daemon=True).start()

    def run_bootrom_scan_async(self):
        self._ensure_tab("board_diag")
        is_ar = self.current_lang == "ar"
        self._set_box(self.bootrom_text, self._tr("ui.scanning_windows_pnp_ports_for_qualcomm"))

        def task():
            diag = self.board_diag.diagnose_dead_phone_bootrom(check_cycling=True)
            self.last_bootrom_diag = diag

            lines = [
                self._tr("ui.non_booting_device_bootrom_usb_diagnosis"),
                self._tr("ui.hypothesis", summary_diagnosis=diag.summary_diagnosis),
                self._tr("ui.qualcomm_edl_9008", no=self._tr("ui.yes_detected") if diag.is_qualcomm_edl else self._tr("ui.no")),
                self._tr("ui.mediatek_brom", no=self._tr("ui.yes_detected") if diag.is_mediatek_brom else self._tr("ui.no")),
                self._tr("ui.samsung_odin_mode", no=self._tr("ui.yes_detected") if diag.is_samsung_odin else self._tr("ui.no")),
                self._tr("ui.usb_disconnect_loop", stable_no_reconnect_cycling=self._tr("ui.usb_reconnect_cycling") if diag.is_usb_cycling else self._tr("ui.stable_no_reconnect_cycling")),
                ""
            ]
            if diag.detected_ports:
                lines.append(self._tr("ui.detected_usb_interfaces"))
                for p in diag.detected_ports:
                    lines.append(f"  • {p.get('mode')}: {p.get('name')} [{p.get('status')}]")
                    lines.append(f"    {p.get('diagnosis')}")

            def upd():
                self._ensure_tab("board_diag")
                self._set_box(self.bootrom_text, "\n".join(lines))
                self._set_pill("bootrom", bootrom_pill_status(diag))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_leakage_test_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        self._ensure_tab("board_diag")
        is_ar = self.current_lang == "ar"
        self.leakage_progress.set(0)
        self._set_box(self.leakage_text, self._tr("ui.screen_off_battery_current_test_only"))

        def progress(pct, val, msg):
            def upd():
                self._ensure_tab("board_diag")
                self.leakage_progress.set(pct / 100.0)
                val_txt = f"{val} mA" if val is not None else "UNKNOWN"
                ar_msg = self._tr("ui.sample_10_battery_current", pct=int(pct/10), val_txt=val_txt)
                self.leakage_lbl.configure(text=self._L(self._m(ar_msg)))
                self._append_box(self.leakage_text, f"{ar_msg}\n")
            self.after(0, upd)

        def task():
            res = self.board_diag.run_leakage_test(
                serial=serial,
                sample_count=10,
                sample_interval=3.0,
                progress_callback=progress
            )
            self.last_leakage = res
            lines = [f"\n[{res.status}] {res.diagnosis}"]
            for cause in res.possible_causes:
                lines.append(self._tr("ui.text_2", possible_cause=self._tr("ui.possible_cause"), cause=cause))
            if res.evidence:
                lines.append(format_evidence_for_display(res.evidence, is_ar=is_ar))
            verdict = "\n".join(lines)
            def done():
                self._ensure_tab("board_diag")
                self._append_box(self.leakage_text, verdict + "\n")
                self._set_pill("leakage", res.status)
                title = self._tr("ui.battery_current_test_result")
                if res.status == "FAIL":
                    messagebox.showwarning(title, f"[{res.status}] {res.diagnosis}")
                else:
                    messagebox.showinfo(title, f"[{res.status}] {res.diagnosis}")
            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def run_thermal_scan_async(self):
        serial = self.current_serial.get()
        if not serial:
            return

        def task():
            thermal = self.board_diag.read_thermal(serial)
            zones = thermal.zones
            is_ar = self.current_lang == "ar"
            out = [self._tr("ui.thermal_zones_zones", zones=len(zones), status=thermal.status)]
            for z in zones:
                if z.limit_c is None:
                    status = self._tr("ui.unclassified_not_judged")
                elif z.is_overheated:
                    status = self._tr("ui.above_c_limit", limit_c=z.limit_c)
                else:
                    status = self._tr("ui.within_c_limit", limit_c=z.limit_c)
                out.append(f"{z.zone_id:02d} [{z.zone_type} / {z.category}]: {z.temp_c:.1f}°C — {status}")
            
            def upd():
                self._ensure_tab("board_diag")
                self._set_box(self.thermal_text, "\n".join(out))
                self._set_pill("thermal", thermal.status)
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_panic_scan_async(self):
        serial = self.current_serial.get()
        if not serial:
            return

        def task():
            panics = self.board_diag.inspect_panic_and_emmc(serial)
            is_ar = self.current_lang == "ar"
            out = [
                self._tr("ui.storage_kernel_panic_audit"),
                f"[{panics.status}] {panics.summary_verdict}",
                f"dmesg: {panics.dmesg_status} | pstore: {panics.blackbox.kernel_ramoops_status} | boot reason: {panics.blackbox.boot_reason}",
                self._tr("ui.ufs_emmc_read_write_errors", emmc_ufs_errors=len(panics.emmc_ufs_errors)),
                self._tr("ui.watchdog_resets", watchdog_resets=len(panics.watchdog_resets)),
                self._tr("ui.kernel_panics", kernel_panics=len(panics.kernel_panics)),
                self._tr("ui.thermal_shutdowns", thermal_shutdowns=len(panics.thermal_shutdowns)),
                ""
            ]
            if panics.emmc_ufs_errors:
                out.append(self._tr("ui.emmc_ufs_i_o_error_traces"))
                for err in panics.emmc_ufs_errors:
                    out.append(f"  {err}")
            if panics.kernel_panics:
                out.append(self._tr("ui.kernel_panic_traces"))
                for p in panics.kernel_panics:
                    out.append(f"  {p}")

            def upd():
                self._ensure_tab("board_diag")
                self._set_box(self.panic_text, "\n".join(out))
                self._set_pill("panic", panics.status)
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_blackbox_forensics_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        self._ensure_tab("board_diag")
        is_ar = self.current_lang == "ar"
        self._set_box(self.blackbox_text, self._tr("ui.analyzing_blackbox_forensics_dropbox_boot_reason"))

        def task():
            result = self.board_diag.run_blackbox_forensics(serial)
            self.last_blackbox = result

            cat = result.shutdown_category
            reason = result.boot_reason
            history = [h for h in result.boot_reason_history.split(",") if h.strip()] if result.boot_reason_history != "UNKNOWN" else []
            ramoops = result.kernel_ramoops_status
            diag = f"[{result.status}] {result.diagnosis}"
            events = result.dropbox_events

            lines = [
                self._tr("ui.blackbox_forensics_reboot_analysis"),
                self._tr("ui.shutdown_category", cat=cat),
                self._tr("ui.last_boot_reason", reason=reason),
                self._tr("ui.boot_records_diagnosis", diag=diag),
                self._tr("ui.kernel_log_ramoops", ramoops=ramoops),
                ""
            ]

            if history:
                lines.append(self._tr("ui.boot_reason_history"))
                for h in history[:5]:
                    lines.append(f"  • {h}")
                lines.append("")

            if events:
                lines.append(self._tr("ui.dropbox_critical_crash_events_logged", events=len(events)))
                for ev in events:
                    lines.append(f"  • {ev}")
            else:
                lines.append(self._tr("ui.no_relevant_dropbox_entries_in_the"))
            for cause in result.possible_causes:
                lines.append(self._tr("ui.text_2", possible_cause=self._tr("ui.possible_cause"), cause=cause))
            if result.evidence:
                lines.append(format_evidence_for_display(result.evidence, is_ar=is_ar))

            def upd():
                self._ensure_tab("board_diag")
                self._set_box(self.blackbox_text, "\n".join(lines))
                self._set_pill("blackbox", result.status)
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def _toggle_live_telemetry(self):
        self._ensure_tab("board_diag")
        is_ar = self.current_lang == "ar"
        if self._telemetry_running:
            self._telemetry_running = False
            self.btn_toggle_telemetry.configure(text=self._L(self._tr("ui.live_telemetry_off")), fg_color=ACCENT_SLATE)
        else:
            serial = self.current_serial.get()
            if not serial:
                messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
                return
            self._telemetry_running = True
            self.btn_toggle_telemetry.configure(text=self._L(self._tr("ui.live_telemetry_active_1_5s")), fg_color=ACCENT_GREEN)
            self._telemetry_thread = threading.Thread(target=self._poll_live_telemetry_loop, daemon=True)
            self._telemetry_thread.start()

    def _poll_live_telemetry_loop(self):
        while self._telemetry_running:
            serial = self.current_serial.get()
            if not serial:
                break
            try:
                pmic = self.board_diag.analyze_pmic(serial)
                v_val = f"{pmic.voltage_mv:.0f} mV ({pmic.voltage_mv / 1000.0:.2f} V)" if (pmic.voltage_mv is not None and pmic.voltage_mv > 0) else "UNKNOWN"
                c_val = f"{pmic.current_ma:+.0f} mA" if pmic.current_ma is not None else "UNKNOWN"
                b_temp = f"{pmic.temp_c:.1f} °C" if pmic.temp_c is not None else "UNKNOWN"

                t_map = self.board_diag.scan_thermal_map(serial)
                cpu_temps = [z.temp_c for z in t_map if "cpu" in z.zone_type.lower() or "soc" in z.zone_type.lower()]
                c_temp = f"{max(cpu_temps):.1f} °C" if cpu_temps else (f"{max([z.temp_c for z in t_map]):.1f} °C" if t_map else "UNKNOWN")

                def update_telem(v=v_val, c=c_val, bt=b_temp, ct=c_temp):
                    if hasattr(self, "lbl_telem_volt"):
                        self.lbl_telem_volt.configure(text=v)
                    if hasattr(self, "lbl_telem_curr"):
                        self.lbl_telem_curr.configure(text=c)
                    if hasattr(self, "lbl_telem_batt_temp"):
                        self.lbl_telem_batt_temp.configure(text=bt)
                    if hasattr(self, "lbl_telem_cpu_temp"):
                        self.lbl_telem_cpu_temp.configure(text=ct)

                self.after(0, update_telem)
            except Exception as e:
                logger.debug(f"Telemetry poll error: {e}")

            time.sleep(1.5)

    def run_hardware_audit_async(self):
        serial = self.current_serial.get()
        if not serial:
            return

        def task():
            report = self.hardware_tester.run_full_audit(serial)
            self.last_hw_audit = report
            is_ar = self.current_lang == "ar"

            # Sensors
            s_lines = [self._tr("ui.reported_sensors_registered", sensors=len(report.sensors))]
            for s in report.sensors:
                power_txt = f" | {s.power_ma} mA" if s.power_ma is not None else ""
                s_lines.append(f"• {s.name} ({s.vendor}) | {s.sensor_type}{power_txt}")
            if not report.sensors:
                s_lines.append(f"[{report.sensors_status}]")
            if report.missing_critical_sensors:
                s_lines.append((self._tr("ui.common_sensors_not_reported_not_necessarily")) + ", ".join(report.missing_critical_sensors))

            # Cameras & Audio
            c_lines = [self._tr("ui.camera_modules_detected", cameras=len(report.cameras))]
            for c in report.cameras:
                flash_txt = "UNKNOWN" if c.flash_available is None else ((self._tr("ui.yes")) if c.flash_available else (self._tr("ui.no_2")))
                c_lines.append(self._tr("ui.camera_flash", camera_id=c.camera_id, facing=self._tr(f"camera.facing.{c.facing}"), flash_txt=flash_txt))
            if not report.cameras:
                c_lines.append(f"[{report.cameras_status}]")

            c_lines.append(self._tr("ui.audio_subsystem"))
            c_lines.append(self._tr("ui.mode_ringer", mode=report.audio.mode, ringer_mode=report.audio.ringer_mode))

            # Fingerprint
            fp_lines = [
                self._tr("ui.verified_status", status=report.fingerprint.status),
                self._tr("ui.fingerprint_sensor_vendor", vendor=report.fingerprint.vendor),
                self._tr("ui.diagnosis", diagnosis=report.fingerprint.diagnosis)
            ]
            for cause in report.fingerprint.possible_causes:
                fp_lines.append(self._tr("ui.text_2", possible_cause=self._tr("ui.possible_cause"), cause=cause))
            if report.fingerprint.evidence:
                fp_lines.append(format_evidence_for_display(report.fingerprint.evidence, is_ar=is_ar))

            # Physical Keys
            key_lines = [
                self._tr("ui.verified_status", status=report.buttons.status),
                self._tr("ui.volume_down_vol", vol_down_status=self._tr(f"keys.state.{report.buttons.vol_down_status}")),
                self._tr("ui.volume_up_vol", vol_up_status=self._tr(f"keys.state.{report.buttons.vol_up_status}")),
                self._tr("ui.power", power_status=self._tr(f"keys.state.{report.buttons.power_status}")),
                self._tr("ui.diagnosis_2", diagnosis=report.buttons.diagnosis)
            ]
            if report.buttons.evidence:
                key_lines.append(format_evidence_for_display(report.buttons.evidence, is_ar=is_ar))

            # SIM & Baseband
            sim_lines = [
                self._tr("ui.verified_status", status=report.sim_baseband.status),
                self._tr("ui.sim_1_state", sim1_state=report.sim_baseband.sim1_state),
                self._tr("ui.sim_2_state", sim2_state=report.sim_baseband.sim2_state),
                self._tr("ui.carrier_operator", carrier_name=report.sim_baseband.carrier_name),
                self._tr("ui.network_type", network_type=report.sim_baseband.network_type),
                self._tr("ui.signal_strength", signal_strength=report.sim_baseband.signal_strength),
                self._tr("ui.baseband_version", baseband_version=report.sim_baseband.baseband_version),
                self._tr("ui.diagnosis_2", diagnosis=report.sim_baseband.diagnosis)
            ]
            for cause in report.sim_baseband.possible_causes:
                sim_lines.append(self._tr("ui.text_2", possible_cause=self._tr("ui.possible_cause"), cause=cause))
            if report.sim_baseband.evidence:
                sim_lines.append(format_evidence_for_display(report.sim_baseband.evidence, is_ar=is_ar))

            # Display Backlight
            disp_lines = [
                self._tr("ui.verified_status", status=report.display_backlight.status),
                self._tr("ui.backlight_level", brightness_now=report.display_backlight.brightness_now, max_brightness=report.display_backlight.max_brightness),
                self._tr("ui.diagnosis_2", diagnosis=report.display_backlight.diagnosis)
            ]
            for cause in report.display_backlight.possible_causes:
                disp_lines.append(self._tr("ui.text_2", possible_cause=self._tr("ui.possible_cause"), cause=cause))
            if report.display_backlight.evidence:
                disp_lines.append(format_evidence_for_display(report.display_backlight.evidence, is_ar=is_ar))

            # Wi-Fi Chipset
            wifi_lines = [
                self._tr("ui.verified_status", status=report.wifi.status),
                self._tr("ui.wi_fi_state", state=report.wifi.state),
                self._tr("ui.mac_address", mac_address=report.wifi.mac_address),
                self._tr("ui.connected_network_ssid", disconnected=report.wifi.ssid or (self._tr("ui.disconnected"))),
                self._tr("ui.signal_rssi_dbm", rssi_dbm=report.wifi.rssi_dbm) if report.wifi.rssi_dbm else (self._tr("ui.signal")),
                self._tr("ui.link_speed_and_frequency_mbps_mhz", link_speed_mbps=report.wifi.link_speed_mbps, frequency_mhz=report.wifi.frequency_mhz) if report.wifi.link_speed_mbps else (self._tr("ui.speed_freq")),
                self._tr("ui.driver_status", driver_status=self._tr(f"wifi.driver.{report.wifi.driver_status}")),
                self._tr("ui.diagnosis_2", diagnosis=report.wifi.diagnosis)
            ]
            if report.wifi.evidence:
                wifi_lines.append(format_evidence_for_display(report.wifi.evidence, is_ar=is_ar))

            # Bluetooth Controller
            bt_lines = [
                self._tr("ui.verified_status", status=report.bluetooth.status),
                self._tr("ui.bluetooth_state", state=report.bluetooth.state),
                self._tr("ui.mac_address_2", mac_address=report.bluetooth.mac_address),
                self._tr("ui.device_name", name=report.bluetooth.name),
                self._tr("ui.ble", ble_supported=self._tr(_tri_key(report.bluetooth.ble_supported))),
                self._tr("ui.diagnosis_2", diagnosis=report.bluetooth.diagnosis)
            ]
            for cause in report.bluetooth.possible_causes:
                bt_lines.append(self._tr("ui.text_2", possible_cause=self._tr("ui.possible_cause"), cause=cause))
            if report.bluetooth.evidence:
                bt_lines.append(format_evidence_for_display(report.bluetooth.evidence, is_ar=is_ar))

            # Peripheral Chips (GPS, NFC, OTG)
            chip_lines = [
                self._tr("ui.verified_status", status=report.peripherals.status),
                self._tr("ui.gps", gps_status=self._tr(f'feature.{report.peripherals.gps_status}')),
                self._tr("ui.nfc", nfc_status=self._tr(f'feature.{report.peripherals.nfc_status}')),
                self._tr("ui.usb_host_otg", usb_otg_supported=self._tr(_feature_key(report.peripherals.usb_otg_supported))),
                self._tr("ui.diagnosis_2", diagnosis=report.peripherals.diagnosis)
            ]
            if report.peripherals.evidence:
                chip_lines.append(format_evidence_for_display(report.peripherals.evidence, is_ar=is_ar))

            # Audio Codec IC (ALSA Soundcards)
            codec_lines = [
                self._tr("ui.verified_status", status=report.audio_codec.status),
                self._tr("ui.audio_codec_ic", codec_chip_name=report.audio_codec.codec_chip_name),
                self._tr("ui.alsa_sound_cards_found", cards_detected=len(report.audio_codec.cards_detected), none=', '.join(report.audio_codec.cards_detected) if report.audio_codec.cards_detected else (self._tr("ui.none"))),
                self._tr("ui.alsa_pcm_channels", pcm_devices=len(report.audio_codec.pcm_devices)),
                self._tr("ui.audioflinger_output_threads", audio_flinger_alive=self._tr(_flinger_key(report.audio_codec.audio_flinger_alive)), output_threads=report.audio_codec.output_threads),
                self._tr("ui.diagnosis_2", diagnosis=report.audio_codec.diagnosis)
            ]
            for cause in report.audio_codec.possible_causes:
                codec_lines.append(self._tr("ui.text_2", possible_cause=self._tr("ui.possible_cause"), cause=cause))
            if report.audio_codec.evidence:
                codec_lines.append(format_evidence_for_display(report.audio_codec.evidence, is_ar=is_ar))

            # Display Controller & Panel Info
            panel_lines = [
                self._tr("ui.verified_status", status=report.display_panel_info.status),
                self._tr("ui.dsi_panel_controller", panel_controller_name=report.display_panel_info.panel_controller_name),
                self._tr("ui.driver_source", driver_source=report.display_panel_info.driver_source),
                self._tr("ui.verified_boot_state", verified_boot_state=report.display_panel_info.verified_boot_state),
                self._tr("ui.vbmeta_device_state", vbmeta_device_state=report.display_panel_info.vbmeta_device_state),
                self._tr("ui.diagnosis_2", diagnosis=report.display_panel_info.diagnosis)
            ]
            if report.display_panel_info.evidence:
                panel_lines.append(format_evidence_for_display(report.display_panel_info.evidence, is_ar=is_ar))

            self.last_audio_codec = report.audio_codec
            self.last_panel_info = report.display_panel_info

            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.sensors_text, "\n".join(s_lines))
                self._set_pill("sensors", getattr(report, "sensors_status", "UNKNOWN"))

                self._set_box(self.camera_text, "\n".join(c_lines))
                self._set_pill("cameras", getattr(report, "cameras_status", "UNKNOWN"))

                self._set_box(self.fp_text, "\n".join(fp_lines))
                self._set_pill("fp", getattr(report.fingerprint, "status", "UNKNOWN"))

                self._set_box(self.keys_text, "\n".join(key_lines))
                self._set_pill("keys", getattr(report.buttons, "status", "UNKNOWN"))

                self._set_box(self.sim_text, "\n".join(sim_lines))
                self._set_pill("sim", getattr(report.sim_baseband, "status", "UNKNOWN"))

                self._set_box(self.backlight_text, "\n".join(disp_lines))
                self._set_pill("backlight", getattr(report.display_backlight, "status", "UNKNOWN"))

                if hasattr(self, "wifi_text"):
                    self._set_box(self.wifi_text, "\n".join(wifi_lines))
                    self._set_pill("wifi", getattr(report.wifi, "status", "UNKNOWN"))
                if hasattr(self, "bt_text"):
                    self._set_box(self.bt_text, "\n".join(bt_lines))
                    self._set_pill("bt", getattr(report.bluetooth, "status", "UNKNOWN"))
                if hasattr(self, "chips_text"):
                    self._set_box(self.chips_text, "\n".join(chip_lines))
                    self._set_pill("chips", getattr(report.peripherals, "status", "UNKNOWN"))
                if hasattr(self, "audio_codec_text"):
                    self._set_box(self.audio_codec_text, "\n".join(codec_lines))
                    self._set_pill("audio_codec", getattr(report.audio_codec, "status", "UNKNOWN"))
                if hasattr(self, "panel_info_text"):
                    self._set_box(self.panel_info_text, "\n".join(panel_lines))
                    self._set_pill("panel_info", getattr(report.display_panel_info, "status", "UNKNOWN"))
                self.pointer_location_var.set(report.pointer_location_active)

            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_buttons_test_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        self._ensure_tab("hardware_tester")
        is_ar = self.current_lang == "ar"
        self._set_box(self.keys_text, self._tr("ui.listening_for_physical_key_presses_press"))

        def task():
            btn_diag = self.hardware_tester.diagnose_physical_buttons(serial)
            lines = [
                self._tr("ui.physical_buttons_test_result"),
                self._tr("ui.volume_down", vol_down_status=self._tr(f"keys.state.{btn_diag.vol_down_status}")),
                self._tr("ui.volume_up", vol_up_status=self._tr(f"keys.state.{btn_diag.vol_up_status}")),
                self._tr("ui.power_2", power_status=self._tr(f"keys.state.{btn_diag.power_status}")),
                self._tr("ui.diagnosis_3", diagnosis=btn_diag.diagnosis),
                ""
            ]
            if btn_diag.captured_events:
                lines.append(self._tr("ui.captured_input_events"))
                for ev in btn_diag.captured_events:
                    lines.append(f"  {ev}")

            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.keys_text, "\n".join(lines))
                self._set_pill("keys", getattr(btn_diag, "status", "UNKNOWN"))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def _trigger_vibration(self):
        serial = self.current_serial.get()
        if serial:
            self.hardware_tester.trigger_vibrator(serial, 1000)

    def _trigger_audio(self):
        serial = self.current_serial.get()
        if serial:
            self.hardware_tester.play_audio_test(serial)

    def _toggle_pointer_location(self):
        serial = self.current_serial.get()
        if serial:
            enabled = self.pointer_location_var.get()
            self.hardware_tester.set_pointer_location(serial, enabled)

    def run_security_audit_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        self._ensure_tab("malware_hunter")
        self.sec_progress.set(0)
        self._set_box(self.threats_text, "")
        is_ar = self.current_lang == "ar"

        def progress(pct, msg):
            def upd():
                self._ensure_tab("malware_hunter")
                self.sec_progress.set(pct / 100.0)
                self.sec_status_lbl.configure(text=self._L(self._m(msg)))
            self.after(0, upd)

        def task():
            scan = self.malware_hunter.audit_security(serial, progress_callback=progress)
            self.last_sec_scan = scan

            t_lines = [
                self._tr("ui.apps_with_sensitive_permissions_for_review"),
                self._tr("ui.packages_third_party_system", total_packages_scanned=scan.total_packages_scanned, third_party_count=scan.third_party_count, system_count=scan.system_count),
                self._m(scan.summary),
                ""
            ]
            for app in scan.sensitive_apps:
                t_lines.append(f"• {app.package_name}")
                for perm, label in zip(app.sensitive_permissions, app.permission_labels):
                    t_lines.append(f"   - {label} ({perm})")
                for note in app.review_notes:
                    t_lines.append(f"   ! {note}")
                t_lines.append("")
            if not scan.sensitive_apps:
                t_lines.append(self._tr("ui.no_third_party_app_holds_a"))
            if scan.accessibility_services:
                t_lines.append((self._tr("ui.enabled_accessibility_services")) + ", ".join(scan.accessibility_services))
            if scan.device_admins:
                t_lines.append((self._tr("ui.active_device_admins")) + ", ".join(scan.device_admins))
            t_lines.append(f"\n[{scan.user_ca_status}] {scan.user_ca_diagnosis}")
            for cert in scan.user_certificates:
                t_lines.append(f"   • {cert}")
            t_lines.append(format_evidence_for_display(scan.evidence, is_ar=is_ar))

            def upd():
                self._ensure_tab("malware_hunter")
                self._set_box(self.threats_text, "\n".join(t_lines))
                self._set_pill("user_ca", getattr(scan, "user_ca_status", "UNKNOWN"))

            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_network_sniff_async(self):
        serial = self.current_serial.get()
        if not serial:
            return

        def task():
            conns = self.malware_hunter.scan_network_connections(serial)
            is_ar = self.current_lang == "ar"
            lines = [self._tr("ui.active_network_connections_sockets", conns=len(conns))]
            for c in conns:
                lines.append(f"{c.protocol:4s} {c.local_address}:{c.local_port} -> {c.remote_address}:{c.remote_port} [{c.state}] | {c.package_name}")
            if not conns:
                lines.append(self._tr("ui.no_active_external_connection_was_read"))

            def upd():
                self._ensure_tab("malware_hunter")
                self._set_box(self.net_text, "\n".join(lines))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def _neutralize_selected(self, action: str):
        serial = self.current_serial.get()
        pkg = self.neutral_pkg_entry.get().strip()
        if not serial or not pkg:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.enter_the_app_package_name_first"))
            return

        def task():
            success, msg = self.malware_hunter.neutralize_app(serial, pkg, action=action)
            def upd():
                if success:
                    messagebox.showinfo(self._tr("ui.done"), self._m(msg))
                else:
                    messagebox.showerror(self._tr("ui.error"), self._m(msg))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def _show_rollback_dialog(self):
        serial = self.current_serial.get()
        history = self.db.get_rollback_history(serial)

        dialog = ctk.CTkToplevel(self)
        dialog.title(self._tr("ui.app_rollback_history"))
        dialog.geometry("700x480")

        ctk.CTkLabel(dialog, text=self._tr("ui.removed_and_disabled_apps_that_can"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#58a6ff").pack(padx=20, pady=(15, 5))

        txt = ctk.CTkTextbox(dialog, height=300, font=ctk.CTkFont(family="Consolas", size=12))
        txt.pack(fill="both", expand=True, padx=20, pady=10)

        for h in history:
            status = self._tr("ui.restored_at", restored_at=h['restored_at']) if h.get("restored_at") else self._tr("ui.available_to_restore")
            txt.insert("end", self._tr("ui.time_status", id=h['id'], upper=h['action_type'].upper(), package_name=h['package_name'], timestamp=h['timestamp'], status=status))

        restore_bar = ctk.CTkFrame(dialog, fg_color="transparent")
        restore_bar.pack(fill="x", padx=20, pady=15)

        entry = ctk.CTkEntry(restore_bar, placeholder_text=self._tr("ui.package_name_to_restore"), width=260)
        entry.pack(side="left", padx=(0, 10))

        def restore_now():
            target_pkg = entry.get().strip()
            if not target_pkg:
                return
            def t_task():
                success, msg = self.malware_hunter.rollback_package(serial, target_pkg)
                self.after(0, lambda: messagebox.showinfo(self._tr("ui.restore"), self._m(msg)))
            threading.Thread(target=t_task, daemon=True).start()

        ctk.CTkButton(restore_bar, text=self._tr("ui.restore_app_now"), command=restore_now, fg_color="#238636").pack(side="left")

    def run_oem_bloat_scan_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        self._ensure_tab("malware_hunter")
        is_ar = self.current_lang == "ar"
        self._set_box(self.bloat_text, self._tr("ui.scanning_installed_oem_bloatware_packages_matchi"))

        def task():
            bloat_list = self.malware_hunter.scan_oem_bloatware(serial)
            self.last_bloat_scan = bloat_list

            lines = [
                self._tr("ui.uad_ng_oem_bloatware_scan_packages", bloat_list=len(bloat_list)),
                ""
            ]

            if bloat_list:
                for b in bloat_list:
                    lines.append(f"• [{b.get('oem')}] {b.get('package_name')} ({b.get('category')})")
                    lines.append(self._tr("ui.description", current_lang=render(b.get('description'), self.current_lang)))
                    lines.append(self._tr("ui.removal_safety", removal_safety=b.get('removal_safety')))
                    lines.append("")
                lines.append(self._tr("ui.to_uninstall_these_apps_for_user"))
            else:
                lines.append(self._tr("ui.clean_no_active_oem_bloatware_packages"))

            def upd():
                self._ensure_tab("malware_hunter")
                self._set_box(self.bloat_text, "\n".join(lines))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_safe_debloat_all_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        is_ar = self.current_lang == "ar"
        if not self.last_bloat_scan:
            bloat_list = self.malware_hunter.scan_oem_bloatware(serial)
            self.last_bloat_scan = bloat_list

        if not self.last_bloat_scan:
            messagebox.showinfo(self._tr("ui.nothing_found"), self._tr("ui.no_bloatware_packages_found_to_remove"))
            return

        confirm = messagebox.askyesno(
            self._tr("ui.confirm_safe_debloat"),
            self._tr("ui.safe_debloat_will_disable_oem_bloatware", last_bloat_scan=len(self.last_bloat_scan))
        )
        if not confirm:
            return

        self.bloat_text.delete("1.0", "end")
        self.bloat_text.insert("end", self._tr("ui.safely_debloating_oem_packages"))

        def task():
            success_count = 0
            for b in self.last_bloat_scan:
                pkg = b.get("package_name")
                ok, msg = self.malware_hunter.neutralize_app(serial, pkg, action="disable")
                if ok:
                    success_count += 1
                def log_one(p=pkg, s=ok):
                    self.bloat_text.insert("end", f"{'✓' if s else '✗'} {p}\n")
                    self.bloat_text.see("end")
                self.after(0, log_one)
                time.sleep(0.05)

            def done():
                res_txt = self._tr("ui.successfully_debloated_packages", success_count=success_count, last_bloat_scan=len(self.last_bloat_scan))
                self.bloat_text.insert("end", res_txt)
                messagebox.showinfo(self._tr("ui.debloat_complete"), res_txt)
            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def run_restore_all_debloated_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        is_ar = self.current_lang == "ar"
        self.bloat_text.delete("1.0", "end")
        self.bloat_text.insert("end", self._tr("ui.restoring_debloated_packages"))

        def task():
            history = self.db.get_rollback_history(serial)
            if not history:
                def none_found():
                    self.bloat_text.insert("end", self._tr("ui.no_neutralized_packages_found_in_rollback"))
                    messagebox.showinfo(self._tr("ui.notice"), self._tr("ui.no_debloated_packages_to_restore"))
                self.after(0, none_found)
                return

            restored = 0
            for item in history:
                pkg = item.get("package_name")
                ok, msg = self.malware_hunter.rollback_package(serial, pkg)
                if ok:
                    restored += 1
                def log_one(p=pkg, s=ok):
                    self.bloat_text.insert("end", self._tr("ui.text_3", failed=self._tr("ui.restored") if s else self._tr("ui.failed"), p=p))
                    self.bloat_text.see("end")
                self.after(0, log_one)
                time.sleep(0.05)

            def done():
                res_txt = self._tr("ui.successfully_restored_packages", restored=restored)
                self.bloat_text.insert("end", res_txt)
                messagebox.showinfo(self._tr("ui.restore_complete"), res_txt)
            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def run_optimizer_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        self._ensure_tab("optimizer")
        self.opt_progress.set(0)
        self._set_box(self.opt_log_text, "")

        def progress(pct, msg):
            def upd():
                self._ensure_tab("optimizer")
                self.opt_progress.set(pct / 100.0)
                self.opt_status_lbl.configure(text=self._L(self._m(msg)))
                self._append_box(self.opt_log_text, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
            self.after(0, upd)

        def task():
            res = self.optimizer.run_optimization(serial, progress_callback=progress)
            self.last_opt_result = res

            is_ar = self.current_lang == "ar"
            lines = []
            for a in res.actions:
                if a.measured:
                    lines.append(self._tr("ui.text_4", status=a.status, command=a.command, mb=_fmt(a.before, 'MB'), mb2=_fmt(a.after, 'MB'), change=self._tr("ui.change"), mb3=_fmt(a.delta, 'MB')))
                else:
                    lines.append(self._tr("ui.text_5", status=a.status, command=a.command, effect_not_measurable=self._tr("ui.effect_not_measurable")))
                lines.append(f"    {a.note}")

            def done():
                self._ensure_tab("optimizer")
                self.lbl_ram_gain.configure(text=f"{_fmt(res.before.ram_free_mb)} -> {_fmt(res.after.ram_free_mb, 'MB')}")
                self.lbl_storage_gain.configure(text=_fmt(res.storage_freed_mb, "MB"))
                self.lbl_drain_gain.configure(text="—")
                self._append_box(self.opt_log_text, "\n" + "\n".join(lines) + "\n")
                self._set_pill("maint", maintenance_pill_status(res.actions))
                messagebox.showinfo(self._tr("ui.maintenance_finished"),
                                    (self._tr("ui.measured_change_in_free_storage_after", mb=_fmt(res.storage_freed_mb, 'MB'))))
            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def _launch_scrcpy(self):
        serial = self.current_serial.get()
        turn_off = self.mirror_off_var.get()
        threading.Thread(target=lambda: self.rescue_center.launch_screen_mirror(serial, turn_screen_off=turn_off), daemon=True).start()

    def _stop_scrcpy(self):
        threading.Thread(target=self.rescue_center.stop_screen_mirror, daemon=True).start()

    def _launch_scrcpy_otg(self):
        serial = self.current_serial.get()
        is_ar = self.current_lang == "ar"
        threading.Thread(target=lambda: self.rescue_center.launch_otg_mode(serial), daemon=True).start()
        if hasattr(self, "wireless_log_text"):
            self.wireless_log_text.insert("end", self._tr("ui.otg_usb_hid_mouse_keyboard_mode"))
            self.wireless_log_text.see("end")

    def _send_virtual_pin(self):
        serial = self.current_serial.get()
        pin = self.vpad_pin_entry.get().strip()
        if not pin:
            return
        def task():
            self.rescue_center.send_text(pin, serial)
            time.sleep(0.2)
            self.rescue_center.send_keyevent(66, serial)
        threading.Thread(target=task, daemon=True).start()

    def _send_virtual_swipe(self):
        serial = self.current_serial.get()
        threading.Thread(target=lambda: self.rescue_center.send_swipe(500, 1600, 500, 400, 300, serial), daemon=True).start()

    def _send_virtual_key(self, key_code: int):
        serial = self.current_serial.get()
        threading.Thread(target=lambda: self.rescue_center.send_keyevent(key_code, serial), daemon=True).start()

    def _enable_wireless_adb(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_phone_connected_over_usb"))
            return
        is_ar = self.current_lang == "ar"
        self.wireless_log_text.delete("1.0", "end")
        self.wireless_log_text.insert("end", self._tr("ui.switching_adb_to_tcp_ip_mode"))

        def task():
            ok, msg = self.bridge.enable_wireless_adb(serial, 5555)
            ip = self.bridge.get_device_ip_address(serial)
            def done():
                self.wireless_log_text.insert("end", f"{msg}\n")
                if ip:
                    ip_port = f"{ip}:5555"
                    self.lbl_wireless_ip.configure(text=self._tr("ui.ip_2", ip_port=ip_port))
                    self.entry_wireless_addr.delete(0, "end")
                    self.entry_wireless_addr.insert(0, ip_port)
                    info_msg = (
                        self._tr("ui.wireless_adb_enabled_on_you_can", ip_port=ip_port)
                    )
                    messagebox.showinfo(self._tr("ui.wireless_adb_ready"), self._m(info_msg))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.port_5555_was_enabled_but_the"))
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _connect_wireless_adb(self):
        addr = self.entry_wireless_addr.get().strip()
        if not addr:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.enter_the_ip_address_and_port"))
            return
        is_ar = self.current_lang == "ar"
        def task():
            ok, msg = self.bridge.connect_wireless_adb(addr)
            def done():
                self.wireless_log_text.insert("end", self._tr("ui.wireless_connection", addr=addr, msg=msg))
                self.wireless_log_text.see("end")
                if ok:
                    messagebox.showinfo(self._tr("ui.connected"), self._tr("ui.connected_to_the_wireless_device", addr=addr))
                else:
                    messagebox.showerror(self._tr("ui.connection_failed"), self._m(msg))
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _disconnect_wireless_adb(self):
        addr = self.entry_wireless_addr.get().strip()
        def task():
            ok, msg = self.bridge.disconnect_wireless_adb(addr)
            def done():
                self.wireless_log_text.insert("end", self._tr("ui.wireless_disconnect", msg=msg))
                self.wireless_log_text.see("end")
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _pair_wireless_adb(self):
        addr = self.entry_pair_addr.get().strip()
        code = self.entry_pair_code.get().strip()
        if not addr or not code:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.enter_the_pairing_ip_address_and"))
            return
        def task():
            ok, msg = self.bridge.pair_wireless_adb(addr, code)
            def done():
                self.wireless_log_text.insert("end", self._tr("ui.android_11_pairing", addr=addr, msg=msg))
                self.wireless_log_text.see("end")
                if ok:
                    messagebox.showinfo(self._tr("ui.pairing_succeeded"), self._tr("ui.the_device_was_paired_wirelessly_you"))
                else:
                    messagebox.showerror(self._tr("ui.pairing_failed"), self._m(msg))
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _check_provisioning(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        def task():
            p_status = self.provisioning.get_provisioning_status(serial)
            is_ar = self.current_lang == "ar"
            lines = [
                self._tr("ui.provisioning_status"),
                self._tr("ui.device_provisioned", raw_provisioned_val=p_status.raw_provisioned_val, not_completed=self._tr("ui.yes_completed") if p_status.is_device_provisioned else self._tr("ui.not_completed")),
                self._tr("ui.user_setup_complete", raw_setup_complete_val=p_status.raw_setup_complete_val, not_completed=self._tr("ui.yes_completed") if p_status.is_user_setup_complete else self._tr("ui.not_completed")),
                self._tr("ui.result", status_summary=p_status.status_summary)
            ]
            def upd():
                self.prov_log_text.delete("1.0", "end")
                self.prov_log_text.insert("end", "\n".join(lines))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _fix_provisioning(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        def task():
            ok, msg = self.provisioning.complete_setup_wizard_flags(serial)
            def upd():
                self.prov_log_text.delete("1.0", "end")
                self.prov_log_text.insert("end", self._tr("ui.setup_wizard_flags", msg=msg))
                if ok:
                    messagebox.showinfo(self._tr("ui.skipped"), self._tr("ui.setup_complete_flags_were_set_and"))
                else:
                    messagebox.showerror(self._tr("ui.error"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _launch_settings(self):
        serial = self.current_serial.get()
        if not serial:
            return
        def task():
            ok, msg = self.provisioning.launch_settings_activity(serial)
            def upd():
                self.prov_log_text.delete("1.0", "end")
                self.prov_log_text.insert("end", f"{msg}\n")
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _read_fastboot_vars(self):
        serial = self.current_serial.get()
        def task():
            fb = self.rescue_center.read_fastboot_variables(serial)
            is_ar = self.current_lang == "ar"
            lines = [
                self._tr("ui.fastboot_device_variables"),
                self._tr("ui.product", product=fb.product),
                self._tr("ui.bootloader_version", bootloader_version=fb.bootloader_version),
                self._tr("ui.baseband_version_2", baseband_version=fb.baseband_version),
                self._tr("ui.secure_boot", disabled=self._tr("ui.enabled") if fb.is_secure else self._tr("ui.disabled")),
                self._tr("ui.bootloader_lock", locked=self._tr("ui.unlocked") if fb.is_unlocked else self._tr("ui.locked")),
                self._tr("ui.current_slot", current_slot=fb.current_slot),
                self._tr("ui.battery_voltage_bootloader", battery_voltage=fb.battery_voltage),
                self._tr("ui.cpu_abi", cpu_abi=fb.cpu_abi),
                self._tr("ui.all_raw_variables")
            ]
            for k, v in fb.raw_vars.items():
                lines.append(f"  {k}: {v}")

            def upd():
                self.rescue_log_text.delete("1.0", "end")
                self.rescue_log_text.insert("end", "\n".join(lines))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def _fix_cache_bootloop(self):
        serial = self.current_serial.get()
        is_ar = self.current_lang == "ar"

        def task():
            has_cache, check_msg = self.rescue_center.has_cache_partition(serial)
            if has_cache is not True:
                def no_cache():
                    self.rescue_log_text.delete("1.0", "end")
                    self.rescue_log_text.insert("end", f"{check_msg}\n")
                    messagebox.showinfo(self._tr("ui.erase_cache"),
                                        (self._tr("ui.this_device_has_no_cache_partition")))
                self.after(0, no_cache)
                return
            confirm = {"ok": False}
            def ask():
                confirm["ok"] = messagebox.askyesno(
                    self._tr("ui.erase_cache"),
                    (self._tr("ui.this_erases_the_cache_partition_only")))
                if confirm["ok"]:
                    threading.Thread(target=run_erase, daemon=True).start()
            def run_erase():
                ok, msg = self.rescue_center.fix_cache_bootloop(serial)
                def upd():
                    self.rescue_log_text.delete("1.0", "end")
                    self.rescue_log_text.insert("end", f"{check_msg}\n{msg}\n")
                    if ok:
                        messagebox.showinfo(self._tr("ui.erase_cache"),
                                            self._tr("ui.cache_partition_erased_reboot_to_check"))
                    else:
                        messagebox.showerror(self._tr("ui.error"), self._m(msg))
                self.after(0, upd)
            self.after(0, ask)
        threading.Thread(target=task, daemon=True).start()

    def _browse_image_file(self):
        f = filedialog.askopenfilename(filetypes=[("Android Image", "*.img"), ("All Files", "*.*")])
        if f:
            self.img_path_entry.delete(0, "end")
            self.img_path_entry.insert(0, f)

    def _flash_partition(self):
        serial = self.current_serial.get()
        part = self.part_combo.get().strip()
        path = self.img_path_entry.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.choose_a_valid_img_file_to"))
            return

        def task():
            ok, msg = self.rescue_center.flash_partition(part, path, serial)
            def upd():
                self.rescue_log_text.delete("1.0", "end")
                self.rescue_log_text.insert("end", self._tr("ui.flash", part=part, msg=msg))
                if ok:
                    messagebox.showinfo(self._tr("ui.flash_succeeded"), self._tr("ui.partition_was_flashed", part=part))
                else:
                    messagebox.showerror(self._tr("ui.flash_failed"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _pull_emergency_data(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_the_phone_first_or_connect"))
            return

        raw_cat = self.data_cat_combo.get()
        cat = raw_cat.split(" ")[0].strip().lower()
        if not cat:
            cat = "all_photos"

        desktop = os.path.join(os.path.expanduser("~"), "Desktop", f"Android_Rescue_{cat}_{int(time.time())}")
        is_ar = self.current_lang == "ar"

        self.data_log_text.delete("1.0", "end")
        self.data_log_text.insert("end", self._tr("ui.starting_data_extraction_to_desktop_folder", cat=cat))

        def task():
            def prog(txt):
                self.after(0, lambda: self.data_log_text.insert("end", f"{txt}\n"))
            ok, msg = self.rescue_center.emergency_data_pull(serial, cat, desktop, progress_callback=prog)
            def upd():
                if ok:
                    messagebox.showinfo(self._tr("ui.copy_finished"), self._tr("ui.files_were_copied_to_the_desktop", desktop=desktop))
                    try:
                        webbrowser.open(desktop)
                    except Exception:
                        pass
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.data_copy_result", msg=msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _pull_all_photos_quick(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_the_phone_first_or_connect"))
            return

        desktop = os.path.join(os.path.expanduser("~"), "Desktop", f"Android_All_Photos_{int(time.time())}")
        is_ar = self.current_lang == "ar"

        self.data_log_text.delete("1.0", "end")
        self.data_log_text.insert("end", self._tr("ui.scanning_and_pulling_all_photos_videos"))

        def task():
            def prog(txt):
                self.after(0, lambda: self.data_log_text.insert("end", f"{txt}\n"))
            ok, msg = self.rescue_center.emergency_data_pull(serial, "all_photos", desktop, progress_callback=prog)
            def upd():
                if ok:
                    messagebox.showinfo(self._tr("ui.photo_copy_finished"), self._tr("ui.all_photos_and_videos_were_copied", desktop=desktop))
                    try:
                        webbrowser.open(desktop)
                    except Exception:
                        pass
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.photo_copy_result", msg=msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _browse_ota_file(self):
        f = filedialog.askopenfilename(filetypes=[("OTA Firmware Package", "*.zip"), ("All Files", "*.*")])
        if f:
            self.sideload_entry.delete(0, "end")
            self.sideload_entry.insert(0, f)

    def _flash_sideload(self):
        serial = self.current_serial.get()
        path = self.sideload_entry.get().strip()
        is_ar = self.current_lang == "ar"
        if not path or not os.path.exists(path):
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.choose_a_valid_rom_file_zip"))
            return

        self.sideload_progress.set(0)
        self.sideload_text.delete("1.0", "end")
        self.sideload_text.insert("end", self._tr("ui.starting_adb_sideload", path=os.path.basename(path)))

        def task():
            def prog(pct, msg):
                def update():
                    self.sideload_progress.set(pct / 100.0)
                    self.sideload_lbl.configure(text=f"[{pct}%] {msg}")
                    self.sideload_text.insert("end", f"{msg}\n")
                    self.sideload_text.see("end")
                self.after(0, update)

            ok, out_msg = self.rescue_center.sideload_firmware(path, serial, progress_callback=prog)
            def done():
                if ok:
                    messagebox.showinfo(self._tr("ui.sideload_complete"), self._tr("ui.ota_firmware_package_flashed_successfully_via"))
                else:
                    messagebox.showerror(self._tr("ui.sideload_failed"), self._m(out_msg))
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _one_click_customer_backup(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first_or"))
            return

        is_ar = self.current_lang == "ar"
        self.data_log_text.delete("1.0", "end")
        self.data_log_text.insert("end", self._tr("ui.starting_1_click_customer_backup_media"))

        def task():
            def prog(txt):
                self.after(0, lambda: (self.data_log_text.insert("end", f"{txt}\n"), self.data_log_text.see("end")))
            ok, summary, b_dir = self.rescue_center.one_click_customer_backup(serial, progress_callback=prog)
            def done():
                if ok:
                    messagebox.showinfo(
                        self._tr("ui.customer_backup_complete"),
                        self._tr("ui.customer_data_backup_saved_to_desktop", b_dir=b_dir)
                    )
                    try:
                        webbrowser.open(b_dir)
                    except Exception:
                        pass
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(summary))
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def run_nand_lifespan_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_phone_first"))
            return
        self._ensure_tab("board_diag")
        is_ar = self.current_lang == "ar"
        self._set_box(self.nand_text, self._tr("ui.reading_jedec_emmc_ufs_wear_level"))

        def task():
            rep = self.board_diag.read_emmc_ufs_lifespan(serial)
            self.last_storage_life = rep
            lines = [
                self._tr("ui.emmc_ufs_nand_wear_level_lifespan"),
                self._tr("ui.storage_type", storage_type=rep.storage_type),
                self._tr("ui.life_time_estimate_a", life_time_type_a=rep.life_time_type_a, raw_hex_a=rep.raw_hex_a),
                self._tr("ui.life_time_estimate_b", life_time_type_b=rep.life_time_type_b, raw_hex_b=rep.raw_hex_b),
                self._tr("ui.reserved_blocks_pre_eol", pre_eol_info=rep.pre_eol_info, raw_pre_eol=rep.raw_pre_eol),
                self._tr("ui.status", health_verdict=rep.health_verdict),
                self._tr("ui.diagnosis_4", diagnosis=rep.diagnosis)
            ]
            def upd():
                self._ensure_tab("board_diag")
                self._set_box(self.nand_text, "\n".join(lines))
                self._set_pill("nand", getattr(rep, "status", "UNKNOWN"))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_speed_bench_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_phone_first"))
            return
        self._ensure_tab("board_diag")
        is_ar = self.current_lang == "ar"
        self._set_box(self.nand_text, self._tr("ui.running_64mb_sequential_read_write_speed"))

        def task():
            w_speed, r_speed, verdict, diag = self.board_diag.benchmark_storage_speed(serial, test_size_mb=64)
            lines = [
                self._tr("ui.storage_sequential_read_write_benchmark_64mb"),
                self._tr("ui.sequential_write_speed", s=_fmt(w_speed, 'MB/s')),
                self._tr("ui.sequential_read_speed", s=_fmt(r_speed, 'MB/s')),
                self._tr("ui.throughput_verdict", verdict=verdict),
                self._tr("ui.technical_diagnosis", diag=diag)
            ]
            def upd():
                self._ensure_tab("board_diag")
                self._set_box(self.nand_text, "\n".join(lines))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_stress_test_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_phone_first"))
            return
        self._ensure_tab("board_diag")
        is_ar = self.current_lang == "ar"
        self.stress_progress.set(0)
        self._set_box(self.stress_text, self._tr("ui.starting_30_second_cpu_pmic_stress"))

        def task():
            def prog(snap: StressTestSnapshot, msg: str):
                def update():
                    self._ensure_tab("board_diag")
                    pct = min(1.0, snap.second / 30.0)
                    self.stress_progress.set(pct)
                    self.stress_lbl.configure(text=self._L(self._m(msg)))
                    self._append_box(self.stress_text, f"{msg}\n")
                self.after(0, update)

            rep = self.board_diag.run_cpu_pmic_stress_test(serial, duration_sec=30, progress_callback=prog)
            self.last_stress_report = rep

            lines = [
                self._tr("ui.cpu_pmic_30_second_stress_test"),
                self._tr("ui.overall_verdict", verdict=rep.verdict),
                self._tr("ui.initial_battery_voltage", mv=_fmt(rep.initial_voltage_mv, 'mV')),
                self._tr("ui.minimum_voltage_under_load", mv=_fmt(rep.min_voltage_mv, 'mV')),
                self._tr("ui.voltage_drop", mv=_fmt(rep.voltage_sag_mv, 'mV')),
                self._tr("ui.temperature", temp_source=rep.temp_source, c=_fmt(rep.initial_temp_c, '°C'), c2=_fmt(rep.max_temp_c, '°C')),
                self._tr("ui.cpu_frequency_range", mhz=_fmt(rep.min_freq_mhz, 'MHz'), mhz2=_fmt(rep.max_freq_mhz, 'MHz')),
                self._tr("ui.final_diagnosis", diagnosis=rep.diagnosis)
            ]

            def done():
                self._ensure_tab("board_diag")
                self.stress_progress.set(1.0)
                self._append_box(self.stress_text, "\n".join(lines) + "\n")
                self._set_pill("stress", getattr(rep, "status", "UNKNOWN"))
                if rep.severe_sag_detected:
                    messagebox.showwarning(self._tr("ui.voltage_sag_alert"), self._m(rep.diagnosis))
                else:
                    messagebox.showinfo(self._tr("ui.stress_test_complete"), self._tr("ui.result_2", verdict=rep.verdict, diagnosis=rep.diagnosis))
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _switch_slot(self, slot: str):
        serial = self.current_serial.get()
        is_ar = self.current_lang == "ar"
        self.slot_log_text.delete("1.0", "end")
        self.slot_log_text.insert("end", self._tr("ui.switching_active_boot_slot_to_slot", upper=slot.upper()))

        def task():
            ok, msg = self.rescue_center.switch_active_slot(slot, serial)
            def upd():
                self.slot_log_text.delete("1.0", "end")
                self.slot_log_text.insert("end", self._m(msg))
                if ok:
                    messagebox.showinfo(self._tr("ui.slot_switch"), self._tr("ui.successfully_switched_to_slot", upper=slot.upper()))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _check_arb(self):
        serial = self.current_serial.get()
        is_ar = self.current_lang == "ar"
        self.slot_log_text.delete("1.0", "end")
        self.slot_log_text.insert("end", self._tr("ui.querying_anti_rollback_arb_hardware_fuse"))

        def task():
            ok, msg, arb_index = self.rescue_center.check_anti_rollback(serial)
            def upd():
                self.slot_log_text.delete("1.0", "end")
                self.slot_log_text.insert("end", self._m(msg))
                if ok:
                    messagebox.showinfo(self._tr("ui.arb_index"), self._tr("ui.anti_rollback_protection_index_arb_do", arb_index=arb_index))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _backup_critical_partitions(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_phone_first"))
            return
        is_ar = self.current_lang == "ar"
        self.part_backup_text.delete("1.0", "end")
        self.part_backup_text.insert("end", self._tr("ui.dumping_critical_radio_partitions_efs_fsg"))

        def task():
            def prog(txt):
                self.after(0, lambda: self.part_backup_text.insert("end", f"{txt}\n"))
            res = self.partition_backup.backup_critical_partitions(serial=serial, progress_callback=prog)
            def upd():
                lines = [
                    self._tr("ui.critical_partition_backup_report"),
                    self._tr("ui.success", warning_partial=self._tr("ui.yes_completed_2") if res.success else self._tr("ui.warning_partial")),
                    self._tr("ui.partitions_copied", dumped_partitions=len(res.dumped_partitions)),
                    self._tr("ui.saved_in_folder", backup_directory=res.backup_directory),
                    self._tr("ui.diagnosis_2", diagnosis=res.diagnosis)
                ]
                self.part_backup_text.insert("end", "\n".join(lines))
                if res.success:
                    messagebox.showinfo(self._tr("ui.backup_complete"), self._tr("ui.critical_partitions_saved_to", backup_directory=res.backup_directory))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(res.diagnosis))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _backup_all_partitions(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_phone_first"))
            return
        is_ar = self.current_lang == "ar"
        self.part_backup_text.delete("1.0", "end")
        self.part_backup_text.insert("end", self._tr("ui.dumping_all_discoverable_partitions_to_pc"))

        def task():
            def prog(txt):
                self.after(0, lambda: self.part_backup_text.insert("end", f"{txt}\n"))
            res = self.partition_backup.backup_all_partitions(serial=serial, progress_callback=prog)
            def upd():
                self.part_backup_text.insert("end", self._tr("ui.copy_finished_partitions_folder", dumped_partitions=len(res.dumped_partitions), backup_directory=res.backup_directory))
                messagebox.showinfo(self._tr("ui.backup_complete"), self._tr("ui.partitions_saved_in", backup_directory=res.backup_directory))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _open_backups_folder(self):
        folder = self.partition_backup.backup_base_dir
        os.makedirs(folder, exist_ok=True)
        webbrowser.open(folder)

    def _batch_install_apks(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        files = filedialog.askopenfilenames(filetypes=[("Android APK", "*.apk"), ("All Files", "*.*")])
        if not files:
            return
        is_ar = self.current_lang == "ar"
        self.apk_log_text.delete("1.0", "end")
        self.apk_log_text.insert("end", self._tr("ui.batch_installing_apk_files", files=len(files)))

        def task():
            def prog(cur, tot, f):
                self.after(0, lambda: self.apk_log_text.insert("end", self._tr("ui.installing", cur=cur, tot=tot, f=os.path.basename(f))))
            results = self.batch_apk.install_multiple_apks(serial, list(files), progress_callback=prog)
            def upd():
                successes = sum(1 for r in results if r.success)
                summary = self._tr("ui.batch_install_finished_successful", successes=successes, results=len(results))
                self.apk_log_text.insert("end", self._m(summary))
                messagebox.showinfo(self._tr("ui.install_complete"), self._m(summary))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _extract_user_apps(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        is_ar = self.current_lang == "ar"
        self.apk_log_text.delete("1.0", "end")
        self.apk_log_text.insert("end", self._tr("ui.listing_and_extracting_user_apps_to"))

        def task():
            def prog(cur, tot, pkg):
                self.after(0, lambda: self.apk_log_text.insert("end", self._tr("ui.pulling", cur=cur, tot=tot, pkg=pkg)))
            results = self.batch_apk.extract_all_user_apks(serial, progress_callback=prog)
            def upd():
                successes = sum(1 for r in results if r.success)
                summary = self._tr("ui.extracted_apps_to_desktop", successes=successes)
                self.apk_log_text.insert("end", self._m(summary))
                messagebox.showinfo(self._tr("ui.extraction_complete"), self._m(summary))
                if results and results[0].output_path:
                    webbrowser.open(os.path.dirname(results[0].output_path))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_fast_charge_diag_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        self._ensure_tab("board_diag")
        is_ar = self.current_lang == "ar"
        self._set_box(self.fast_charge_text, self._tr("ui.reading_charging_protocol_negotiation_voltage_an"))

        def task():
            diag = self.board_diag.diagnose_fast_charge(serial)
            self.last_fast_charge = diag
            lines = [
                self._tr("ui.fast_charge_protocol_power_negotiation"),
                self._tr("ui.protocol", protocol_name=diag.protocol_name),
                self._tr("ui.charger_type", charger_type=diag.charger_type),
                f"[{diag.status}]",
                self._tr("ui.usb_input_voltage_measured", mv=_fmt(diag.voltage_mv, 'mV')),
                self._tr("ui.usb_input_current_measured", ma=_fmt(diag.current_ma, 'mA')),
                self._tr("ui.input_power_measured_v_x_i", w=_fmt(diag.wattage_w, 'W')),
                self._tr("ui.reported_max_current_voltage_not_measured", ma=_fmt(diag.max_charging_current_ma, 'mA'), mv=_fmt(diag.max_charging_voltage_mv, 'mV')),
                self._tr("ui.fast_charging", is_fast_charging=self._tr(_tri_key(diag.is_fast_charging))),
                self._tr("ui.battery_3", battery_level=_fmt(diag.battery_level, '%'), charge_status=diag.charge_status),
                self._tr("ui.diagnosis_2", diagnosis=diag.diagnosis)
            ]
            def upd():
                self._ensure_tab("board_diag")
                self._set_box(self.fast_charge_text, "\n".join(lines))
                self._set_pill("fast_charge", getattr(diag, "status", "UNKNOWN"))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_reset_gauge_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        self._ensure_tab("board_diag")
        is_ar = self.current_lang == "ar"
        def task():
            ok, msg = self.board_diag.reset_battery_stats_and_gauge(serial)
            def upd():
                self._ensure_tab("board_diag")
                self._set_box(self.fast_charge_text, self._tr("ui.reset_battery_statistics", msg=msg))
                messagebox.showinfo(
                    self._tr("ui.reset_battery_statistics_2"),
                    self._tr("ui.battery_usage_statistics_reset_successfully_note")
                )
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_storage_benchmark_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        self._ensure_tab("hardware_tester")
        is_ar = self.current_lang == "ar"
        self._set_box(self.storage_text, self._tr("ui.running_emmc_ufs_health_check_read"))

        def task():
            report = self.storage_bench.run_full_storage_test(serial)
            self.last_storage_report = report
            lines = [
                self._tr("ui.storage_emmc_ufs_life_cycle_speed"),
                self._tr("ui.verified_status", status=report.status),
                self._tr("ui.storage_type_2", storage_type=report.storage_type),
                self._tr("ui.slc_wear_type_a", life_time_type_a=report.life_time_type_a),
                self._tr("ui.mlc_wear_type_b", life_time_type_b=report.life_time_type_b),
                self._tr("ui.reserved_blocks_pre_eol_2", pre_eol_info=report.pre_eol_info),
                self._tr("ui.sequential_write_speed_mb_s", write_speed_mb_s=report.write_speed_mb_s),
                self._tr("ui.sequential_read_speed_mb_s", read_speed_mb_s=report.read_speed_mb_s),
                self._tr("ui.health_verdict", health_verdict=report.health_verdict),
                self._tr("ui.diagnosis", diagnosis=report.diagnosis)
            ]
            if report.evidence:
                lines.append(format_evidence_for_display(report.evidence, is_ar=is_ar))
            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.storage_text, "\n".join(lines))
                self._set_pill("storage", getattr(report, "status", "UNKNOWN"))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_touch_matrix_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        self._ensure_tab("hardware_tester")
        is_ar = self.current_lang == "ar"
        self._set_box(self.touch_text, self._tr("ui.deploying_touch_digitizer_grid_test_to"))

        def task():
            ok, msg = self.touch_matrix.deploy_and_launch_touch_test(serial)
            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.touch_text, self._tr("ui.touch_test", msg=msg))
                if ok:
                    messagebox.showinfo(self._tr("ui.touch_test_2"), self._tr("ui.touch_test_grid_opened_on_device"))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_full_deep_scan_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        def task():
            # Step 1: Profile
            profile = self.profiler.profile_device(serial)
            self.current_profile = profile
            # Step 2: Board Diag
            bdiag = self.board_diag.run_full_diagnostics(serial)
            self.last_board_diag = bdiag
            # Step 3: Hardware Audit
            haudit = self.hardware_tester.run_full_audit(serial)
            self.last_hw_audit = haudit
            # Step 4: Security Audit
            sec = self.malware_hunter.audit_security(serial)
            self.last_sec_scan = sec

            # Save report to DB
            self.db.save_diagnostic_report(
                device_serial=serial,
                report_title="Full Hardware & Motherboard Inspection",
                overall_status=bdiag.overall_health,
                battery_health_pct=bdiag.pmic.true_health_pct,
                voltage_mv=bdiag.pmic.voltage_mv,
                current_ma=bdiag.pmic.current_ma,
                temp_c=bdiag.pmic.temp_c,
                thermal_summary=f"{len(bdiag.thermal_zones)} zones monitored",
                storage_summary=f"{profile.storage_free_gb}GB Free",
                ram_summary=f"{profile.ram_free_mb}MB Free",
                sensor_summary=f"{len(haudit.sensors)} active sensors",
                security_summary=sec.summary,
                raw_dict={
                    "profile": profile.to_dict(),
                    "board": bdiag.to_dict(),
                    "hardware": haudit.to_dict(),
                    "security": sec.to_dict()
                }
            )

            def done():
                messagebox.showinfo(self._tr("ui.full_scan_finished"), self._tr("ui.full_scan_finished_board_checks_apps", overall_health=bdiag.overall_health, sensitive_apps=len(sec.sensitive_apps), true_health_pct=bdiag.pmic.true_health_pct))
                self.run_profile_async()
            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def run_screen_color_test_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_device_first"))
            return
        self._ensure_tab("hardware_tester")
        is_ar = self.current_lang == "ar"
        self._set_box(self.touch_text, self._tr("ui.deploying_full_screen_rgbw_dead_pixel"))

        def task():
            ok, msg = self.hardware_tester.launch_screen_color_test(serial)
            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.touch_text, self._tr("ui.screen_test_status", msg=msg))
                if ok:
                    answer = messagebox.askyesno(
                        self._tr("ui.screen_color_test"),
                        self._tr("ui.interactive_color_test_is_now_running")
                    )
                    if answer:
                        self.hardware_tester.stop_screen_color_test(serial)
                        self._append_box(self.touch_text, self._tr("ui.screen_test_closed_and_touch_overlays"))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_speaker_tone_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_device_first"))
            return
        self._ensure_tab("hardware_tester")
        is_ar = self.current_lang == "ar"
        self._set_box(self.audio_codec_text, self._tr("ui.generating_pure_1000hz_sine_test_tone"))

        def task():
            ok, msg = self.hardware_tester.play_speaker_test_tone(serial, freq=1000, duration_sec=3.0)
            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.audio_codec_text, self._tr("ui.speaker_test_output", msg=msg))
                if ok:
                    messagebox.showinfo(self._tr("ui.speaker_test"), self._tr("ui.test_tone_played_successfully_on_phone"))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_companion_diag_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_device_first"))
            return
        self._ensure_tab("hardware_tester")
        is_ar = self.current_lang == "ar"
        self._set_box(self.sensors_text, self._tr("ui.checking_companion_apk_or_activating_zero"))

        def task():
            ok, msg = self.hardware_tester.install_companion_apk(serial)
            if ok:
                self.hardware_tester.run_companion_broadcast(serial, "START_DIAGNOSTICS")
                res = self.hardware_tester.read_companion_results(serial)
                diag_txt = self._tr("ui.companion_results", res=res)
            else:
                diag_txt = self._tr("ui.companion_status_zero_apk_interactive_suite", msg=msg)
            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.sensors_text, diag_txt)
                messagebox.showinfo(self._tr("ui.interactive_diag"), diag_txt)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_root_forensics_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_device_first"))
            return
        self._ensure_tab("board_diag")
        is_ar = self.current_lang == "ar"
        self._set_box(self.root_forensics_text, self._tr("ui.running_root_forensics_pro_inspecting_persistent"))

        def task():
            rep = self.board_diag.run_root_forensics_pro(serial)
            self.last_root_forensics = rep
            lines = [
                self._tr("ui.root_forensics_pro_kernel_crash_analysis"),
                self._tr("ui.root_status", not_rooted=self._tr("ui.rooted_2") + rep.root_type + ')' if rep.is_rooted else self._tr("ui.not_rooted")),
                self._tr("ui.diagnosis_5", diagnosis=rep.diagnosis),
                self._tr("ui.slc_wear_direct_sysfs", not_available_without_root=rep.nand_exact_lifetime_a or self._tr("ui.not_available_without_root")),
                self._tr("ui.mlc_wear_direct_sysfs", not_available_without_root=rep.nand_exact_lifetime_b or self._tr("ui.not_available_without_root")),
                self._tr("ui.reserved_blocks_pre_eol_3", not_available_without_root=rep.nand_exact_pre_eol or self._tr("ui.not_available_without_root")),
                self._tr("ui.nand_wear_status", nand_wear_status=rep.nand_wear_status),
                self._tr("ui.i2c_bus_hardware_errors_errors", i2c_bus_errors=len(rep.i2c_bus_errors)),
                self._tr("ui.matched_kernel_crash_signatures", matched_fault_signatures=len(rep.matched_fault_signatures))
            ]
            if rep.matched_fault_signatures:
                lines.append(self._tr("ui.matched_hardware_fault_signatures"))
                for f in rep.matched_fault_signatures:
                    # fault_signatures.json fields: id, severity, description_en / description_ar
                    name = f.get("id") or self._tr("ui.fault")
                    desc = f.get(f"description_{self.current_lang}") or f.get("description_en", "")
                    lines.append(f"  • [{f.get('severity', 'UNKNOWN')}] {name}: {desc}")
            if rep.i2c_bus_errors:
                lines.append(self._tr("ui.i2c_bus_error_logs"))
                for err in rep.i2c_bus_errors[:5]:
                    lines.append(f"  • {err}")
            if rep.evidence:
                lines.append(format_evidence_for_display(rep.evidence, is_ar=is_ar))

            def upd():
                self._ensure_tab("board_diag")
                self._set_box(self.root_forensics_text, "\n".join(lines))
                self._set_pill("root_forensics", getattr(rep, "status", "UNKNOWN"))
                if rep.is_rooted:
                    messagebox.showinfo(self._tr("ui.forensics_complete"), self._tr("ui.root_forensics_completed_successfully"))
                else:
                    messagebox.showwarning(self._tr("ui.root_required"), self._m(rep.diagnosis))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_efs_backup_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_device_first"))
            return
        self._ensure_tab("board_diag")
        is_ar = self.current_lang == "ar"
        self._set_box(self.root_forensics_text, self._tr("ui.discovering_critical_efs_nvram_modem_partitions"))

        def task():
            def prog(msg):
                self.after(0, lambda m=msg: self._append_box(self.root_forensics_text, f"{m}\n"))
            res = self.board_diag.backup_efs_nvram_partitions(serial=serial, progress_callback=prog)
            self.last_efs_backup = res
            def upd():
                self._ensure_tab("board_diag")
                lines = [
                    self._tr("ui.raw_efs_nvram_backup_report"),
                    self._tr("ui.success_2", failed_or_needs_permissions=self._tr("ui.yes_completed_3") if res.success else self._tr("ui.failed_or_needs_permissions")),
                    self._tr("ui.partitions_copied_2", backed_up_partitions=len(res.backed_up_partitions)),
                    self._tr("ui.total_backup_size_mb", total_bytes=res.total_bytes / (1024*1024)),
                    self._tr("ui.saved_in_folder_2", backup_folder=res.backup_folder),
                    self._tr("ui.message", message=res.message)
                ]
                if res.hashes:
                    lines.append(self._tr("ui.partition_sha_256_checksums"))
                    for p, h in res.hashes.items():
                        lines.append(f"  • {p}: {h[:16]}...")
                self._append_box(self.root_forensics_text, "\n".join(lines) + "\n")
                if res.success:
                    messagebox.showinfo(self._tr("ui.raw_backup_complete"), self._tr("ui.raw_partitions_successfully_dumped_to", backup_folder=res.backup_folder))
                    if res.backup_folder and os.path.exists(res.backup_folder):
                        webbrowser.open(res.backup_folder)
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(res.message))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _generate_and_open_certificate(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        tech = self.tech_name_entry.get().strip() or (self._tr("ui.chief_hardware_technician"))
        c_name = self.cust_name_entry.get().strip() if hasattr(self, "cust_name_entry") else ""
        c_phone = self.cust_phone_entry.get().strip() if hasattr(self, "cust_phone_entry") else ""
        c_fault = self.cust_fault_entry.get().strip() if hasattr(self, "cust_fault_entry") else ""
        cost_str = self.cust_cost_entry.get().strip() if hasattr(self, "cust_cost_entry") else "0"
        try:
            c_cost = float(cost_str)
        except ValueError:
            c_cost = 0.0

        ticket_id = f"TCK-{int(time.time()) % 1000000:06d}"
        try:
            self.db.create_repair_ticket(
                ticket_id=ticket_id,
                device_serial=serial,
                customer_name=c_name,
                customer_phone=c_phone,
                reported_fault=c_fault,
                repair_cost=c_cost,
                technician_name=tech,
                status="CERTIFIED"
            )
        except Exception as e:
            logger.error(f"Error creating repair ticket in DB: {e}")

        prof_dict = self.current_profile.to_dict() if self.current_profile else self.profiler.profile_device(serial).to_dict()
        board_dict = self.last_board_diag.to_dict() if self.last_board_diag else self.board_diag.run_full_diagnostics(serial).to_dict()
        hw_dict = self.last_hw_audit.to_dict() if self.last_hw_audit else self.hardware_tester.run_full_audit(serial).to_dict()
        sec_dict = self.last_sec_scan.to_dict() if self.last_sec_scan else self.malware_hunter.audit_security(serial).to_dict()
        opt_dict = self.last_opt_result.to_dict() if self.last_opt_result else {}
        storage_dict = self.last_storage_report.to_dict() if self.last_storage_report else {}
        charge_dict = self.last_fast_charge.to_dict() if self.last_fast_charge else {}
        slife_dict = self.last_storage_life.to_dict() if self.last_storage_life else {}
        stress_dict = self.last_stress_report.to_dict() if self.last_stress_report else {}
        audio_codec_dict = self.last_audio_codec.to_dict() if self.last_audio_codec else {}
        panel_info_dict = self.last_panel_info.to_dict() if self.last_panel_info else {}
        root_forensics_dict = self.last_root_forensics.to_dict() if self.last_root_forensics else {}

        report_path = DiagnosticReportExporter.generate_html_report(
            device_profile=prof_dict,
            board_diag=board_dict,
            hw_audit=hw_dict,
            sec_scan=sec_dict,
            opt_result=opt_dict,
            storage_report=storage_dict,
            fast_charge=charge_dict,
            storage_life_report=slife_dict,
            stress_report=stress_dict,
            audio_codec=audio_codec_dict,
            panel_info=panel_info_dict,
            root_forensics=root_forensics_dict,
            technician_name=tech,
            ticket_id=ticket_id,
            customer_name=c_name,
            customer_phone=c_phone,
            reported_fault=c_fault,
            repair_cost=c_cost,
            lang=self.current_lang
        )

        webbrowser.open(f"file:///{os.path.abspath(report_path)}")
        self._refresh_past_reports()

    def _refresh_past_reports(self):
        self._ensure_tab("reports")
        reports = self.db.get_diagnostic_reports(limit=25)
        is_ar = self.current_lang == "ar"
        lines = [self._tr("ui.stored_diagnostic_reports_in_database", reports=len(reports))]
        for r in reports:
            batt_pct = r.get('battery_health_pct')
            batt_str = f"{batt_pct}%" if batt_pct is not None else "UNKNOWN"
            lines.append(self._tr("ui.report_device_date_status_battery_health", id=r['id'], device_serial=r['device_serial'], timestamp=r['timestamp'], overall_status=r['overall_status'], batt_str=batt_str))
        self._set_box(self.reports_text, "\n".join(lines))
