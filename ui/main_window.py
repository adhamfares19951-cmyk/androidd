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
from core.result import Evidence, Status, CheckResult
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

        self._setup_ui()
        self._start_device_monitor()

    def _tr(self, key: str, **kwargs: Any) -> str:
        return t(key, self.current_lang, **kwargs)

    def _m(self, value: Any) -> str:
        """Render a core message (Msg), a list of them, or a plain value in the current language."""
        return render(value, self.current_lang)

    def _setup_ui(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # ------------------ SIDEBAR ------------------
        self.sidebar_frame = ctk.CTkFrame(self, width=250, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")

        # Explicit row configure to eliminate ANY gaps between buttons:
        # Rows 0-6: header, lang, device dropdown, refresh, nav_container (weight=0)
        # Row 7: expanding spacer (weight=1) -> pushes status_box to bottom
        # Row 8: status_box (weight=0)
        for r in range(7):
            self.sidebar_frame.grid_rowconfigure(r, weight=0)
        self.sidebar_frame.grid_rowconfigure(7, weight=1)
        self.sidebar_frame.grid_rowconfigure(8, weight=0)

        # App Brand
        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame,
            text=self._tr("ui.android_doctor_pro_2"),
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#58a6ff"
        )
        self.logo_label.grid(row=0, column=0, padx=15, pady=(15, 2))

        self.subtitle_label = ctk.CTkLabel(
            self.sidebar_frame,
            text=self._tr("ui.hardware_diagnostics_suite"),
            font=ctk.CTkFont(size=12),
            text_color="#8b949e"
        )
        self.subtitle_label.grid(row=1, column=0, padx=15, pady=(0, 10))

        # Language Switcher
        lang_bar = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        lang_bar.grid(row=2, column=0, padx=15, pady=(0, 15))

        self.btn_lang_ar = ctk.CTkButton(
            lang_bar,
            text=self._tr("ui.text"),
            width=80,
            height=26,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1f6feb" if self.current_lang == "ar" else "#21262d",
            command=lambda: self.switch_language("ar")
        )
        self.btn_lang_ar.pack(side="left", padx=2)

        self.btn_lang_en = ctk.CTkButton(
            lang_bar,
            text=self._tr("ui.english"),
            width=80,
            height=26,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1f6feb" if self.current_lang == "en" else "#21262d",
            command=lambda: self.switch_language("en")
        )
        self.btn_lang_en.pack(side="left", padx=2)

        # Device Selection Dropdown
        self.dev_select_label = ctk.CTkLabel(
            self.sidebar_frame,
            text=self._tr("target_device"),
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color="#c9d1d9",
            anchor="w"
        )
        self.dev_select_label.grid(row=3, column=0, padx=20, pady=(5, 2), sticky="w")

        self.device_combo = ctk.CTkComboBox(
            self.sidebar_frame,
            values=[self._tr("no_devices")],
            command=self._on_device_selected,
            width=210
        )
        self.device_combo.grid(row=4, column=0, padx=20, pady=(0, 8))

        self.btn_refresh_dev = ctk.CTkButton(
            self.sidebar_frame,
            text=self._tr("refresh_devices"),
            command=self.refresh_devices_async,
            height=28,
            fg_color="#21262d",
            hover_color="#30363d"
        )
        self.btn_refresh_dev.grid(row=5, column=0, padx=20, pady=(0, 12))

        # Navigation Buttons Container Frame
        # PACKING buttons inside nav_container ELIMINATES all spacing anomalies and gaps!
        self.nav_container = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.nav_container.grid(row=6, column=0, sticky="ew", padx=10, pady=0)

        self.nav_buttons = {}
        tab_names = [
            ("dashboard", "tab_dashboard"),
            ("board_diag", "tab_board_diag"),
            ("hardware_tester", "tab_hardware"),
            ("malware_hunter", "tab_malware"),
            ("optimizer", "tab_optimizer"),
            ("rescue_center", "tab_rescue"),
            ("reports", "tab_reports")
        ]

        for key, tr_key in tab_names:
            btn = ctk.CTkButton(
                self.nav_container,
                text=self._tr(tr_key),
                anchor="w",
                height=36,
                fg_color="transparent",
                text_color="#c9d1d9",
                hover_color="#1f2937",
                command=lambda k=key: self.select_tab(k)
            )
            btn.pack(fill="x", pady=2)
            self.nav_buttons[key] = btn

        # Expanding Spacer: absorbs leftover vertical window height to push status_box to bottom
        self.sidebar_spacer = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.sidebar_spacer.grid(row=7, column=0, sticky="nsew")

        # System Status at Bottom Sidebar
        self.status_box = ctk.CTkFrame(self.sidebar_frame, fg_color="#161b22", corner_radius=6)
        self.status_box.grid(row=8, column=0, padx=15, pady=15, sticky="ew")

        self.adb_status_label = ctk.CTkLabel(
            self.status_box,
            text=self._tr("adb_disconnected"),
            font=ctk.CTkFont(size=11),
            text_color="#8b949e",
            wraplength=210
        )
        self.adb_status_label.pack(padx=8, pady=8)

        # ------------------ MAIN CONTENT AREA ------------------
        # Row 0: tools banner (hidden unless a bundled tool is missing / does not match bin/manifest.json)
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)
        self.sidebar_frame.grid_configure(rowspan=2)
        self.tools_banner = ctk.CTkFrame(self, fg_color="#3d1d1d", corner_radius=6)
        self.tools_banner_lbl = ctk.CTkLabel(self.tools_banner, text="", text_color="#fca5a5", justify="left", wraplength=700)
        self.tools_banner_lbl.pack(side="left", padx=12, pady=8, fill="x", expand=True)
        self.btn_repair_tools = ctk.CTkButton(self.tools_banner, text=self._tr("tools.repair"), width=140,
                                              fg_color="#b91c1c", hover_color="#dc2626", command=self._repair_tools_async)
        self.btn_repair_tools.pack(side="right", padx=12, pady=8)

        self.content_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="#0d1117")
        self.content_frame.grid(row=1, column=1, sticky="nsew", padx=10, pady=10)
        self.content_frame.grid_columnconfigure(0, weight=1)
        self.content_frame.grid_rowconfigure(0, weight=1)

        # Tabs are built on first use (and in idle time), so the window opens quickly
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
        self.current_lang = lang
        set_current_language(lang)
        self.btn_lang_ar.configure(fg_color="#1f6feb" if lang == "ar" else "#21262d")
        self.btn_lang_en.configure(fg_color="#1f6feb" if lang == "en" else "#21262d")
        
        # Refresh current view
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        self.tab_frames.clear()
        self.btn_repair_tools.configure(text=self._tr("tools.repair"))
        self.after(150, self._build_next_pending_tab)

        tab_tr = {
            "dashboard": "tab_dashboard",
            "board_diag": "tab_board_diag",
            "hardware_tester": "tab_hardware",
            "malware_hunter": "tab_malware",
            "optimizer": "tab_optimizer",
            "rescue_center": "tab_rescue",
            "reports": "tab_reports"
        }
        for k, tr_key in tab_tr.items():
            if k in self.nav_buttons:
                self.nav_buttons[k].configure(text=self._tr(tr_key))

        self.dev_select_label.configure(text=self._tr("target_device"))
        self.btn_refresh_dev.configure(text=self._tr("refresh_devices"))
        self.select_tab("dashboard")
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
                    self.nav_buttons[k].configure(fg_color="#1f6feb", text_color="#ffffff")
            else:
                if frame is not None:
                    frame.grid_forget()
                if k in self.nav_buttons:
                    self.nav_buttons[k].configure(fg_color="transparent", text_color="#c9d1d9")

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
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color="#0d1117")
        self.tab_frames["dashboard"] = tab

        # Header Banner
        header = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        header.pack(fill="x", padx=15, pady=(15, 10))

        title = ctk.CTkLabel(header, text=self._tr("dash_title"), font=ctk.CTkFont(size=18, weight="bold"), text_color="#58a6ff")
        title.pack(anchor="w", padx=15, pady=(10, 2))

        sub = ctk.CTkLabel(header, text=self._tr("dash_sub"), font=ctk.CTkFont(size=12), text_color="#8b949e")
        sub.pack(anchor="w", padx=15, pady=(0, 10))

        # Dynamic Connection Warning Alert Card
        self.alert_card = ctk.CTkFrame(tab, fg_color="#2b1414", border_color="#ef4444", border_width=1, corner_radius=8)
        self.alert_title_lbl = ctk.CTkLabel(self.alert_card, text="", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f87171")
        self.alert_title_lbl.pack(anchor="w", padx=15, pady=(10, 4))
        
        self.alert_desc_lbl = ctk.CTkLabel(self.alert_card, text="", font=ctk.CTkFont(size=12), text_color="#fecaca", justify="left", anchor="w")
        self.alert_desc_lbl.pack(fill="x", padx=15, pady=(0, 10))

        alert_btns = ctk.CTkFrame(self.alert_card, fg_color="transparent")
        alert_btns.pack(fill="x", padx=15, pady=(0, 10))

        self.btn_alert_action = ctk.CTkButton(alert_btns, text=self._tr("ui.rescan_now"), height=28, fg_color="#b91c1c", hover_color="#dc2626", command=self.refresh_devices_async)
        self.btn_alert_action.pack(side="left", padx=(0, 8))

        ctk.CTkButton(alert_btns, text=self._tr("ui.restart_the_adb_server"), height=28, fg_color="#21262d", hover_color="#30363d", command=self._restart_adb_service).pack(side="left")

        # Connection Help Banner (Shown when no device is connected)
        self.guide_card = ctk.CTkFrame(tab, fg_color="#221c10", border_color="#d97706", border_width=1, corner_radius=8)
        self.guide_card.pack(fill="x", padx=15, pady=(0, 15))

        guide_top = ctk.CTkFrame(self.guide_card, fg_color="transparent")
        guide_top.pack(fill="x", padx=15, pady=(10, 5))

        ctk.CTkLabel(guide_top, text=self._tr("guide_title"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#f59e0b").pack(side="left")

        ctk.CTkButton(
            guide_top,
            text=self._tr("guide_btn_restart_adb"),
            height=26,
            fg_color="#b45309",
            hover_color="#d97706",
            command=self._restart_adb_service
        ).pack(side="right")

        ctk.CTkLabel(self.guide_card, text=self._tr("guide_step1"), font=ctk.CTkFont(size=12), text_color="#fef3c7", anchor="w").pack(fill="x", padx=18, pady=2)
        ctk.CTkLabel(self.guide_card, text=self._tr("guide_step2"), font=ctk.CTkFont(size=12), text_color="#fef3c7", anchor="w").pack(fill="x", padx=18, pady=2)
        ctk.CTkLabel(self.guide_card, text=self._tr("guide_step3"), font=ctk.CTkFont(size=12), text_color="#fef3c7", anchor="w").pack(fill="x", padx=18, pady=(2, 10))

        # Quick Actions Bar
        actions_bar = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        actions_bar.pack(fill="x", padx=15, pady=(0, 15))

        self.btn_full_scan = ctk.CTkButton(actions_bar, text=self._tr("btn_full_scan"), command=self.run_full_deep_scan_async, fg_color="#238636", hover_color="#2ea043")
        self.btn_full_scan.pack(side="left", padx=15, pady=10)

        self.btn_reboot_norm = ctk.CTkButton(actions_bar, text=self._tr("btn_reboot_sys"), command=lambda: self._exec_reboot(""), width=120, fg_color="#21262d", hover_color="#30363d")
        self.btn_reboot_norm.pack(side="left", padx=5, pady=10)

        self.btn_reboot_rec = ctk.CTkButton(actions_bar, text=self._tr("btn_reboot_rec"), command=lambda: self._exec_reboot("recovery"), width=130, fg_color="#21262d", hover_color="#30363d")
        self.btn_reboot_rec.pack(side="left", padx=5, pady=10)

        self.btn_reboot_bl = ctk.CTkButton(actions_bar, text=self._tr("btn_reboot_bl"), command=lambda: self._exec_reboot("bootloader"), width=140, fg_color="#21262d", hover_color="#30363d")
        self.btn_reboot_bl.pack(side="left", padx=5, pady=10)

        # Specs Grid Frame
        grid = ctk.CTkFrame(tab, fg_color="transparent")
        grid.pack(fill="both", expand=True, padx=15, pady=0)
        grid.grid_columnconfigure((0, 1), weight=1)

        # Specifications Card
        self.specs_card = ctk.CTkFrame(grid, fg_color="#161b22", corner_radius=8)
        self.specs_card.grid(row=0, column=0, padx=(0, 8), pady=8, sticky="nsew")

        ctk.CTkLabel(self.specs_card, text=self._tr("core_specs"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(12, 10))

        self.spec_labels = {}
        spec_fields = [
            ("model", self._tr("spec_model")),
            ("serial", self._tr("spec_serial")),
            ("soc", self._tr("spec_soc")),
            ("cores", self._tr("spec_cores")),
            ("android", self._tr("spec_android")),
            ("build_id", self._tr("spec_build_id")),
            ("security", self._tr("spec_security")),
            ("kernel", self._tr("spec_kernel")),
            ("root", self._tr("spec_root")),
            ("selinux", self._tr("spec_selinux")),
            ("display", self._tr("spec_display"))
        ]

        for key, title_str in spec_fields:
            row = ctk.CTkFrame(self.specs_card, fg_color="transparent")
            row.pack(fill="x", padx=15, pady=3)
            ctk.CTkLabel(row, text=title_str, font=ctk.CTkFont(size=12), text_color="#8b949e").pack(side="left")
            val_lbl = ctk.CTkLabel(row, text="—", font=ctk.CTkFont(size=12, weight="bold"), text_color="#f0f6fc")
            val_lbl.pack(side="right")
            self.spec_labels[key] = val_lbl

        # Resources & Real-Time Gauges Card
        self.gauges_card = ctk.CTkFrame(grid, fg_color="#161b22", corner_radius=8)
        self.gauges_card.grid(row=0, column=1, padx=(8, 0), pady=8, sticky="nsew")

        ctk.CTkLabel(self.gauges_card, text=self._tr("live_gauges"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(12, 10))

        # RAM Gauge
        self.ram_title_lbl = ctk.CTkLabel(self.gauges_card, text=self._tr("ui.ram"), font=ctk.CTkFont(size=12), text_color="#c9d1d9")
        self.ram_title_lbl.pack(anchor="w", padx=15, pady=(5, 2))
        self.ram_bar = ctk.CTkProgressBar(self.gauges_card, height=12, progress_color="#1f6feb")
        self.ram_bar.set(0)
        self.ram_bar.pack(fill="x", padx=15, pady=(0, 15))

        # Storage Gauge
        self.storage_title_lbl = ctk.CTkLabel(self.gauges_card, text=self._tr("ui.internal_storage"), font=ctk.CTkFont(size=12), text_color="#c9d1d9")
        self.storage_title_lbl.pack(anchor="w", padx=15, pady=(5, 2))
        self.storage_bar = ctk.CTkProgressBar(self.gauges_card, height=12, progress_color="#238636")
        self.storage_bar.set(0)
        self.storage_bar.pack(fill="x", padx=15, pady=(0, 15))

        # Battery Gauge
        self.batt_title_lbl = ctk.CTkLabel(self.gauges_card, text=self._tr("ui.battery"), font=ctk.CTkFont(size=12), text_color="#c9d1d9")
        self.batt_title_lbl.pack(anchor="w", padx=15, pady=(5, 2))
        self.batt_bar = ctk.CTkProgressBar(self.gauges_card, height=12, progress_color="#e3b341")
        self.batt_bar.set(0)
        self.batt_bar.pack(fill="x", padx=15, pady=(0, 15))

        self.batt_quick_lbl = ctk.CTkLabel(self.gauges_card, text=self._tr("ui.voltage_temp_health"), font=ctk.CTkFont(size=12), text_color="#8b949e")
        self.batt_quick_lbl.pack(anchor="w", padx=15, pady=(0, 10))

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
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color="#0d1117")
        self.tab_frames["board_diag"] = tab

        # Header
        header = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        header.pack(fill="x", padx=15, pady=(15, 10))
        ctk.CTkLabel(header, text=self._tr("board_title"), font=ctk.CTkFont(size=18, weight="bold"), text_color="#58a6ff").pack(anchor="w", padx=15, pady=(10, 2))
        ctk.CTkLabel(header, text=self._tr("board_sub"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(anchor="w", padx=15, pady=(0, 10))

        # Action Buttons
        btn_bar = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        btn_bar.pack(fill="x", padx=15, pady=(0, 15))

        btn_row1 = ctk.CTkFrame(btn_bar, fg_color="transparent")
        btn_row1.pack(fill="x", padx=10, pady=(8, 4))

        btn_row2 = ctk.CTkFrame(btn_bar, fg_color="transparent")
        btn_row2.pack(fill="x", padx=10, pady=(0, 8))

        # Row 1 Buttons
        self.btn_run_pmic = ctk.CTkButton(btn_row1, text=self._tr("btn_pmic"), command=self.run_pmic_diag_async, fg_color="#1f6feb", hover_color="#388bfd")
        self.btn_run_pmic.pack(side="left", padx=4)

        self.btn_run_leakage = ctk.CTkButton(btn_row1, text=self._tr("btn_leakage"), command=self.run_leakage_test_async, fg_color="#da3633", hover_color="#f85149")
        self.btn_run_leakage.pack(side="left", padx=4)

        self.btn_scan_thermal = ctk.CTkButton(btn_row1, text=self._tr("btn_thermals"), command=self.run_thermal_scan_async, fg_color="#21262d", hover_color="#30363d")
        self.btn_scan_thermal.pack(side="left", padx=4)

        self.btn_scan_panics = ctk.CTkButton(btn_row1, text=self._tr("btn_panics"), command=self.run_panic_scan_async, fg_color="#21262d", hover_color="#30363d")
        self.btn_scan_panics.pack(side="left", padx=4)

        self.btn_scan_bootrom = ctk.CTkButton(btn_row1, text=self._tr("btn_scan_bootrom"), command=self.run_bootrom_scan_async, fg_color="#7c3aed", hover_color="#6d28d9")
        self.btn_scan_bootrom.pack(side="left", padx=4)

        self.btn_fast_charge = ctk.CTkButton(btn_row1, text=self._tr("btn_fast_charge"), command=self.run_fast_charge_diag_async, fg_color="#059669", hover_color="#10b981")
        self.btn_fast_charge.pack(side="left", padx=4)

        self.btn_reset_gauge = ctk.CTkButton(btn_row1, text=self._tr("btn_reset_gauge"), command=self.run_reset_gauge_async, fg_color="#d97706", hover_color="#f59e0b")
        self.btn_reset_gauge.pack(side="left", padx=4)

        # Row 2 Buttons
        self.btn_nand_lifespan = ctk.CTkButton(btn_row2, text=self._tr("btn_nand_lifespan"), command=self.run_nand_lifespan_async, fg_color="#6366f1", hover_color="#4f46e5")
        self.btn_nand_lifespan.pack(side="left", padx=4)

        self.btn_speed_bench = ctk.CTkButton(btn_row2, text=self._tr("btn_speed_bench"), command=self.run_speed_bench_async, fg_color="#0891b2", hover_color="#0e7490")
        self.btn_speed_bench.pack(side="left", padx=4)

        self.btn_stress_test = ctk.CTkButton(btn_row2, text=self._tr("btn_stress_test"), command=self.run_stress_test_async, fg_color="#dc2626", hover_color="#b91c1c")
        self.btn_stress_test.pack(side="left", padx=4)

        self.btn_run_blackbox = ctk.CTkButton(btn_row2, text=self._tr("btn_run_blackbox"), command=self.run_blackbox_forensics_async, fg_color="#4338ca", hover_color="#3730a3")
        self.btn_run_blackbox.pack(side="left", padx=4)

        self.btn_run_root_forensics = ctk.CTkButton(btn_row2, text=self._tr("btn_run_root_forensics"), command=self.run_root_forensics_async, fg_color="#7c3aed", hover_color="#6d28d9")
        self.btn_run_root_forensics.pack(side="left", padx=4)

        self.btn_backup_efs_row = ctk.CTkButton(btn_row2, text=self._tr("btn_backup_efs_raw"), command=self.run_efs_backup_async, fg_color="#059669", hover_color="#10b981")
        self.btn_backup_efs_row.pack(side="left", padx=4)

        self.btn_toggle_telemetry = ctk.CTkButton(btn_row2, text=self._tr("btn_live_telemetry"), command=self._toggle_live_telemetry, fg_color="#0e7490", hover_color="#0891b2")
        self.btn_toggle_telemetry.pack(side="left", padx=4)

        # Live Hardware Telemetry Strip (1.5s Polling)
        self.telemetry_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.telemetry_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.telemetry_card, text=self._tr("card_telemetry"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8").pack(anchor="w", padx=15, pady=(10, 5))

        telem_grid = ctk.CTkFrame(self.telemetry_card, fg_color="transparent")
        telem_grid.pack(fill="x", padx=15, pady=(0, 15))
        telem_grid.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # Voltage
        box_v = ctk.CTkFrame(telem_grid, fg_color="#0d1117", corner_radius=6)
        box_v.grid(row=0, column=0, padx=4, sticky="nsew")
        ctk.CTkLabel(box_v, text=self._tr("ui.battery_voltage_fuel_gauge"), font=ctk.CTkFont(size=11), text_color="#8b949e").pack(padx=8, pady=(8, 2))
        self.lbl_telem_volt = ctk.CTkLabel(box_v, text=self._tr("ui.mv"), font=ctk.CTkFont(size=16, weight="bold"), text_color="#58a6ff")
        self.lbl_telem_volt.pack(padx=8, pady=(0, 8))

        # Current Draw
        box_c = ctk.CTkFrame(telem_grid, fg_color="#0d1117", corner_radius=6)
        box_c.grid(row=0, column=1, padx=4, sticky="nsew")
        ctk.CTkLabel(box_c, text=self._tr("ui.battery_current_incl_usb_charging"), font=ctk.CTkFont(size=11), text_color="#8b949e").pack(padx=8, pady=(8, 2))
        self.lbl_telem_curr = ctk.CTkLabel(box_c, text=self._tr("ui.ma"), font=ctk.CTkFont(size=16, weight="bold"), text_color="#f59e0b")
        self.lbl_telem_curr.pack(padx=8, pady=(0, 8))

        # Battery Temp
        box_bt = ctk.CTkFrame(telem_grid, fg_color="#0d1117", corner_radius=6)
        box_bt.grid(row=0, column=2, padx=4, sticky="nsew")
        ctk.CTkLabel(box_bt, text=self._tr("ui.battery_temp"), font=ctk.CTkFont(size=11), text_color="#8b949e").pack(padx=8, pady=(8, 2))
        self.lbl_telem_batt_temp = ctk.CTkLabel(box_bt, text="— °C", font=ctk.CTkFont(size=16, weight="bold"), text_color="#10b981")
        self.lbl_telem_batt_temp.pack(padx=8, pady=(0, 8))

        # CPU Temp
        box_ct = ctk.CTkFrame(telem_grid, fg_color="#0d1117", corner_radius=6)
        box_ct.grid(row=0, column=3, padx=4, sticky="nsew")
        ctk.CTkLabel(box_ct, text=self._tr("ui.peak_cpu_temp"), font=ctk.CTkFont(size=11), text_color="#8b949e").pack(padx=8, pady=(8, 2))
        self.lbl_telem_cpu_temp = ctk.CTkLabel(box_ct, text="— °C", font=ctk.CTkFont(size=16, weight="bold"), text_color="#f87171")
        self.lbl_telem_cpu_temp.pack(padx=8, pady=(0, 8))

        # PMIC Status Card
        self.pmic_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.pmic_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.pmic_card, text=self._tr("card_pmic"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.pmic_text = ctk.CTkTextbox(self.pmic_card, height=130, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.pmic_text.pack(fill="x", padx=15, pady=(0, 15))
        self.pmic_text.insert("end", self._tr("ui.click_analyze_pmic_charging_ic_to"))

        # Fast Charge Protocol & Power Card
        self.fast_charge_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.fast_charge_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.fast_charge_card, text=self._tr("card_fast_charge"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#34d399").pack(anchor="w", padx=15, pady=(10, 5))

        self.fast_charge_text = ctk.CTkTextbox(self.fast_charge_card, height=110, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.fast_charge_text.pack(fill="x", padx=15, pady=(0, 15))
        self.fast_charge_text.insert("end", self._tr("ui.click_diagnose_fast_charge_protocol_to"))

        # NAND Flash Wear & Speed Benchmark Card
        self.nand_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.nand_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.nand_card, text=self._tr("card_nand_wear"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#818cf8").pack(anchor="w", padx=15, pady=(10, 5))

        self.nand_text = ctk.CTkTextbox(self.nand_card, height=115, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.nand_text.pack(fill="x", padx=15, pady=(0, 15))
        self.nand_text.insert("end", self._tr("ui.click_emmc_ufs_lifespan_wear_or"))

        # 30-Second CPU & PMIC Load / Stress Test Card
        self.stress_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.stress_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.stress_card, text=self._tr("card_stress_test"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#f87171").pack(anchor="w", padx=15, pady=(10, 5))

        self.stress_progress = ctk.CTkProgressBar(self.stress_card, height=10, progress_color="#dc2626")
        self.stress_progress.set(0)
        self.stress_progress.pack(fill="x", padx=15, pady=(5, 8))

        self.stress_lbl = ctk.CTkLabel(self.stress_card, text=self._tr("ui.ready_will_apply_a_30s_multi"), font=ctk.CTkFont(size=12), text_color="#8b949e")
        self.stress_lbl.pack(anchor="w", padx=15, pady=(0, 5))

        self.stress_text = ctk.CTkTextbox(self.stress_card, height=115, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.stress_text.pack(fill="x", padx=15, pady=(0, 15))

        # BootROM / Dead Phone Diagnosis Card
        self.bootrom_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.bootrom_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.bootrom_card, text=self._tr("card_bootrom"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#a78bfa").pack(anchor="w", padx=15, pady=(10, 5))

        self.bootrom_text = ctk.CTkTextbox(self.bootrom_card, height=110, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.bootrom_text.pack(fill="x", padx=15, pady=(0, 15))
        self.bootrom_text.insert("end", self._tr("ui.click_dead_phone_bootrom_usb_scanner"))

        # Leakage Test Live Output Card
        self.leakage_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.leakage_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.leakage_card, text=self._tr("card_leakage"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.leakage_progress = ctk.CTkProgressBar(self.leakage_card, height=10, progress_color="#da3633")
        self.leakage_progress.set(0)
        self.leakage_progress.pack(fill="x", padx=15, pady=(5, 8))

        self.leakage_lbl = ctk.CTkLabel(self.leakage_card, text=self._tr("ui.ready_needs_wireless_adb_and_no"), font=ctk.CTkFont(size=12), text_color="#8b949e")
        self.leakage_lbl.pack(anchor="w", padx=15, pady=(0, 5))

        self.leakage_text = ctk.CTkTextbox(self.leakage_card, height=110, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.leakage_text.pack(fill="x", padx=15, pady=(0, 15))

        # Thermal Zones & Kernel Panic Dual Grid
        dual_grid = ctk.CTkFrame(tab, fg_color="transparent")
        dual_grid.pack(fill="both", expand=True, padx=15, pady=0)
        dual_grid.grid_columnconfigure((0, 1), weight=1)

        # Thermal Zones List
        self.thermal_card = ctk.CTkFrame(dual_grid, fg_color="#161b22", corner_radius=8)
        self.thermal_card.grid(row=0, column=0, padx=(0, 8), pady=0, sticky="nsew")
        ctk.CTkLabel(self.thermal_card, text=self._tr("card_thermal"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.thermal_text = ctk.CTkTextbox(self.thermal_card, height=180, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.thermal_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Kernel & Panic Logs
        self.panic_card = ctk.CTkFrame(dual_grid, fg_color="#161b22", corner_radius=8)
        self.panic_card.grid(row=0, column=1, padx=(8, 0), pady=0, sticky="nsew")
        ctk.CTkLabel(self.panic_card, text=self._tr("card_panics"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.panic_text = ctk.CTkTextbox(self.panic_card, height=180, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.panic_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Blackbox Forensics Card
        self.blackbox_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.blackbox_card.pack(fill="x", padx=15, pady=(15, 15))
        ctk.CTkLabel(self.blackbox_card, text=self._tr("card_blackbox"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#a5b4fc").pack(anchor="w", padx=15, pady=(10, 5))

        self.blackbox_text = ctk.CTkTextbox(self.blackbox_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.blackbox_text.pack(fill="x", padx=15, pady=(0, 15))
        self.blackbox_text.insert("end", self._tr("ui.click_run_blackbox_forensics_to_parse"))

        # Root Forensics Pro & EFS Backup Card
        self.root_forensics_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.root_forensics_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.root_forensics_card, text=self._tr("card_root_forensics"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#d2a8ff").pack(anchor="w", padx=15, pady=(10, 5))

        rf_btn_row = ctk.CTkFrame(self.root_forensics_card, fg_color="transparent")
        rf_btn_row.pack(fill="x", padx=15, pady=(0, 8))
        self.btn_run_rf = ctk.CTkButton(rf_btn_row, text=self._tr("btn_run_root_forensics"), command=self.run_root_forensics_async, fg_color="#7c3aed", hover_color="#6d28d9")
        self.btn_run_rf.pack(side="left", padx=(0, 8))
        self.btn_backup_efs_card = ctk.CTkButton(rf_btn_row, text=self._tr("btn_backup_efs_raw"), command=self.run_efs_backup_async, fg_color="#059669", hover_color="#10b981")
        self.btn_backup_efs_card.pack(side="left", padx=0)

        self.root_forensics_text = ctk.CTkTextbox(self.root_forensics_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.root_forensics_text.pack(fill="x", padx=15, pady=(0, 15))
        self.root_forensics_text.insert("end", self._tr("ui.click_kernel_ramoops_forensics_root_to"))

    # =========================================================================
    # TAB 3: HARDWARE, SENSORS & PERIPHERALS TESTER
    # =========================================================================
    def _build_hardware_tester_tab(self):
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color="#0d1117")
        self.tab_frames["hardware_tester"] = tab

        # Header
        header = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        header.pack(fill="x", padx=15, pady=(15, 10))
        ctk.CTkLabel(header, text=self._tr("hw_title"), font=ctk.CTkFont(size=18, weight="bold"), text_color="#58a6ff").pack(anchor="w", padx=15, pady=(10, 2))
        ctk.CTkLabel(header, text=self._tr("hw_sub"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(anchor="w", padx=15, pady=(0, 10))

        # Action Buttons
        btn_bar = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        btn_bar.pack(fill="x", padx=15, pady=(0, 15))

        btn_row1 = ctk.CTkFrame(btn_bar, fg_color="transparent")
        btn_row1.pack(fill="x", padx=10, pady=(8, 4))

        btn_row2 = ctk.CTkFrame(btn_bar, fg_color="transparent")
        btn_row2.pack(fill="x", padx=10, pady=(0, 8))

        self.btn_audit_hw = ctk.CTkButton(btn_row1, text=self._tr("btn_audit_hw"), command=self.run_hardware_audit_async, fg_color="#1f6feb", hover_color="#388bfd")
        self.btn_audit_hw.pack(side="left", padx=4)

        self.btn_test_keys = ctk.CTkButton(btn_row1, text=self._tr("ui.test_physical_keys_live"), command=self.run_buttons_test_async, fg_color="#21262d", hover_color="#30363d")
        self.btn_test_keys.pack(side="left", padx=4)

        self.btn_test_vib = ctk.CTkButton(btn_row1, text=self._tr("btn_test_vib"), command=self._trigger_vibration, fg_color="#21262d", hover_color="#30363d")
        self.btn_test_vib.pack(side="left", padx=4)

        self.btn_test_audio = ctk.CTkButton(btn_row1, text=self._tr("btn_test_audio"), command=self._trigger_audio, fg_color="#21262d", hover_color="#30363d")
        self.btn_test_audio.pack(side="left", padx=4)

        self.btn_storage_bench = ctk.CTkButton(btn_row1, text=self._tr("btn_storage_bench"), command=self.run_storage_benchmark_async, fg_color="#7c3aed", hover_color="#6d28d9")
        self.btn_storage_bench.pack(side="left", padx=4)

        # Row 2: LCD Color Test, Loudspeaker Sine Sweep, Companion Diag, Touch Matrix Grid, Pointer Overlay
        self.btn_lcd_color_test = ctk.CTkButton(btn_row2, text=self._tr("btn_lcd_color_test"), command=self.run_screen_color_test_async, fg_color="#ea580c", hover_color="#c2410c")
        self.btn_lcd_color_test.pack(side="left", padx=4)

        self.btn_speaker_test = ctk.CTkButton(btn_row2, text=self._tr("btn_speaker_test"), command=self.run_speaker_tone_async, fg_color="#10b981", hover_color="#059669")
        self.btn_speaker_test.pack(side="left", padx=4)

        self.btn_companion_diag = ctk.CTkButton(btn_row2, text=self._tr("btn_companion_diag"), command=self.run_companion_diag_async, fg_color="#6366f1", hover_color="#4f46e5")
        self.btn_companion_diag.pack(side="left", padx=4)

        self.btn_touch_matrix = ctk.CTkButton(btn_row2, text=self._tr("btn_touch_matrix"), command=self.run_touch_matrix_async, fg_color="#0891b2", hover_color="#06b6d4")
        self.btn_touch_matrix.pack(side="left", padx=4)

        self.switch_pointer = ctk.CTkSwitch(
            btn_row2,
            text=self._tr("switch_pointer"),
            variable=self.pointer_location_var,
            command=self._toggle_pointer_location
        )
        self.switch_pointer.pack(side="left", padx=10)

        # Content Grid 1: Sensors & Cameras
        hw_grid = ctk.CTkFrame(tab, fg_color="transparent")
        hw_grid.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        hw_grid.grid_columnconfigure((0, 1), weight=1)

        # Sensors List Card
        self.sensor_card = ctk.CTkFrame(hw_grid, fg_color="#161b22", corner_radius=8)
        self.sensor_card.grid(row=0, column=0, padx=(0, 8), pady=0, sticky="nsew")
        ctk.CTkLabel(self.sensor_card, text=self._tr("card_sensors"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.sensors_text = ctk.CTkTextbox(self.sensor_card, height=220, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.sensors_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Cameras & Audio Card
        self.camera_card = ctk.CTkFrame(hw_grid, fg_color="#161b22", corner_radius=8)
        self.camera_card.grid(row=0, column=1, padx=(8, 0), pady=0, sticky="nsew")
        ctk.CTkLabel(self.camera_card, text=self._tr("card_cameras"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.camera_text = ctk.CTkTextbox(self.camera_card, height=220, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.camera_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Content Grid 2: Low-Level Peripherals (Fingerprint, Keys, SIM, Display Backlight)
        periph_grid = ctk.CTkFrame(tab, fg_color="transparent")
        periph_grid.pack(fill="both", expand=True, padx=15, pady=0)
        periph_grid.grid_columnconfigure((0, 1), weight=1)

        # Fingerprint Sensor Card
        self.fp_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.fp_card.grid(row=0, column=0, padx=(0, 8), pady=(0, 15), sticky="nsew")
        ctk.CTkLabel(self.fp_card, text=self._tr("card_fingerprint"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.fp_text = ctk.CTkTextbox(self.fp_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.fp_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Physical Keys Continuity Card
        self.keys_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.keys_card.grid(row=0, column=1, padx=(8, 0), pady=(0, 15), sticky="nsew")
        ctk.CTkLabel(self.keys_card, text=self._tr("card_keys"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.keys_text = ctk.CTkTextbox(self.keys_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.keys_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # SIM Card Tray & Baseband Card
        self.sim_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.sim_card.grid(row=1, column=0, padx=(0, 8), pady=0, sticky="nsew")
        ctk.CTkLabel(self.sim_card, text=self._tr("card_sim"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.sim_text = ctk.CTkTextbox(self.sim_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.sim_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Display Panel & Backlight IC Card
        self.backlight_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.backlight_card.grid(row=1, column=1, padx=(8, 0), pady=0, sticky="nsew")
        ctk.CTkLabel(self.backlight_card, text=self._tr("card_backlight"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.backlight_text = ctk.CTkTextbox(self.backlight_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.backlight_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Wi-Fi Chipset Card
        self.wifi_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.wifi_card.grid(row=2, column=0, padx=(0, 8), pady=(15, 15), sticky="nsew")
        ctk.CTkLabel(self.wifi_card, text=self._tr("card_wifi"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.wifi_text = ctk.CTkTextbox(self.wifi_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.wifi_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Bluetooth Card
        self.bt_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.bt_card.grid(row=2, column=1, padx=(8, 0), pady=(15, 15), sticky="nsew")
        ctk.CTkLabel(self.bt_card, text=self._tr("card_bluetooth"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.bt_text = ctk.CTkTextbox(self.bt_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.bt_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Additional Peripherals (GPS, NFC, OTG)
        self.chips_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.chips_card.grid(row=3, column=0, columnspan=2, padx=0, pady=(0, 15), sticky="nsew")
        ctk.CTkLabel(self.chips_card, text=self._tr("card_chips"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.chips_text = ctk.CTkTextbox(self.chips_card, height=105, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.chips_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Storage Benchmark & Wear-out Card (eMMC / UFS)
        self.storage_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.storage_card.grid(row=4, column=0, padx=(0, 8), pady=0, sticky="nsew")
        ctk.CTkLabel(self.storage_card, text=self._tr("card_storage_bench"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#a78bfa").pack(anchor="w", padx=15, pady=(10, 5))

        self.storage_text = ctk.CTkTextbox(self.storage_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.storage_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.storage_text.insert("end", self._tr("ui.click_test_storage_wear_speed_to"))

        # Touch Matrix Digitizer Card
        self.touch_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.touch_card.grid(row=4, column=1, padx=(8, 0), pady=0, sticky="nsew")
        ctk.CTkLabel(self.touch_card, text=self._tr("card_touch_matrix"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8").pack(anchor="w", padx=15, pady=(10, 5))

        self.touch_text = ctk.CTkTextbox(self.touch_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.touch_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.touch_text.insert("end", self._tr("ui.click_interactive_touch_digitizer_grid_test"))

        # Audio Codec IC & ALSA Soundcards Card
        self.audio_codec_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.audio_codec_card.grid(row=5, column=0, padx=(0, 8), pady=(15, 0), sticky="nsew")
        ctk.CTkLabel(self.audio_codec_card, text=self._tr("card_audio_codec"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#f472b6").pack(anchor="w", padx=15, pady=(10, 5))

        self.audio_codec_text = ctk.CTkTextbox(self.audio_codec_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.audio_codec_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.audio_codec_text.insert("end", self._tr("ui.inspects_alsa_soundcards_audio_codec_ic"))

        # Display panel info & verified boot state card
        self.panel_info_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.panel_info_card.grid(row=5, column=1, padx=(8, 0), pady=(15, 0), sticky="nsew")
        ctk.CTkLabel(self.panel_info_card, text=self._tr("card_panel_info"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#fbbf24").pack(anchor="w", padx=15, pady=(10, 5))

        self.panel_info_text = ctk.CTkTextbox(self.panel_info_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.panel_info_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.panel_info_text.insert("end", self._tr("ui.reads_the_kernel_reported_display_controller"))

    # =========================================================================
    # TAB 4: MALWARE HUNTER & SPYWARE AUDITOR
    # =========================================================================
    def _build_malware_hunter_tab(self):
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color="#0d1117")
        self.tab_frames["malware_hunter"] = tab

        header = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        header.pack(fill="x", padx=15, pady=(15, 10))
        ctk.CTkLabel(header, text=self._tr("malware_title"), font=ctk.CTkFont(size=18, weight="bold"), text_color="#58a6ff").pack(anchor="w", padx=15, pady=(10, 2))
        ctk.CTkLabel(header, text=self._tr("malware_sub"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(anchor="w", padx=15, pady=(0, 10))

        # Action Buttons
        btn_bar = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        btn_bar.pack(fill="x", padx=15, pady=(0, 15))

        self.btn_run_sec = ctk.CTkButton(btn_bar, text=self._tr("btn_run_sec"), command=self.run_security_audit_async, fg_color="#da3633", hover_color="#f85149")
        self.btn_run_sec.pack(side="left", padx=15, pady=10)

        self.btn_sniff_net = ctk.CTkButton(btn_bar, text=self._tr("btn_sniff_net"), command=self.run_network_sniff_async, fg_color="#21262d", hover_color="#30363d")
        self.btn_sniff_net.pack(side="left", padx=5, pady=10)

        self.btn_scan_bloat = ctk.CTkButton(btn_bar, text=self._tr("btn_scan_bloat"), command=self.run_oem_bloat_scan_async, fg_color="#7c3aed", hover_color="#6d28d9")
        self.btn_scan_bloat.pack(side="left", padx=5, pady=10)

        self.btn_debloat_all = ctk.CTkButton(btn_bar, text=self._tr("btn_debloat_selected"), command=self.run_safe_debloat_all_async, fg_color="#b91c1c", hover_color="#991b1b")
        self.btn_debloat_all.pack(side="left", padx=5, pady=10)

        self.btn_restore_bloat = ctk.CTkButton(btn_bar, text=self._tr("btn_restore_debloat"), command=self.run_restore_all_debloated_async, fg_color="#059669", hover_color="#047857")
        self.btn_restore_bloat.pack(side="left", padx=5, pady=10)

        self.btn_view_rollback = ctk.CTkButton(btn_bar, text=self._tr("btn_rollback"), command=self._show_rollback_dialog, fg_color="#21262d", hover_color="#30363d")
        self.btn_view_rollback.pack(side="left", padx=5, pady=10)

        # Scan Status Bar
        self.sec_progress = ctk.CTkProgressBar(tab, height=10, progress_color="#da3633")
        self.sec_progress.set(0)
        self.sec_progress.pack(fill="x", padx=15, pady=(0, 5))

        self.sec_status_lbl = ctk.CTkLabel(tab, text=self._tr("ui.scanner_ready"), font=ctk.CTkFont(size=12), text_color="#8b949e")
        self.sec_status_lbl.pack(anchor="w", padx=15, pady=(0, 15))

        # Flagged Threats Box
        self.threats_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.threats_card.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        ctk.CTkLabel(self.threats_card, text=self._tr("card_threats"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.threats_text = ctk.CTkTextbox(self.threats_card, height=220, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.threats_text.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        # Package Neutralization Row
        neutral_bar = ctk.CTkFrame(self.threats_card, fg_color="transparent")
        neutral_bar.pack(fill="x", padx=15, pady=(0, 15))

        ctk.CTkLabel(neutral_bar, text=self._tr("target_pkg"), font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 8))
        self.neutral_pkg_entry = ctk.CTkEntry(neutral_bar, placeholder_text="com.suspicious.app", width=260)
        self.neutral_pkg_entry.pack(side="left", padx=(0, 10))

        ctk.CTkButton(neutral_bar, text=self._tr("btn_restrict"), command=lambda: self._neutralize_selected("restrict"), fg_color="#21262d", hover_color="#30363d").pack(side="left", padx=4)
        ctk.CTkButton(neutral_bar, text=self._tr("btn_disable"), command=lambda: self._neutralize_selected("disable"), fg_color="#d97706", hover_color="#b45309").pack(side="left", padx=4)
        ctk.CTkButton(neutral_bar, text=self._tr("btn_uninstall"), command=lambda: self._neutralize_selected("uninstall"), fg_color="#da3633", hover_color="#b91c1c").pack(side="left", padx=4)

        # Network Sockets Box
        self.net_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.net_card.pack(fill="both", expand=True, padx=15, pady=0)
        ctk.CTkLabel(self.net_card, text=self._tr("card_net"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.net_text = ctk.CTkTextbox(self.net_card, height=160, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.net_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

        # Safe OEM System Debloater Card (UAD-ng)
        self.bloat_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.bloat_card.pack(fill="both", expand=True, padx=15, pady=(15, 15))
        ctk.CTkLabel(self.bloat_card, text=self._tr("card_bloatware"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#c084fc").pack(anchor="w", padx=15, pady=(10, 5))

        self.bloat_text = ctk.CTkTextbox(self.bloat_card, height=150, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.bloat_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.bloat_text.insert("end", self._tr("ui.click_scan_oem_bloatware_to_audit"))

    # =========================================================================
    # TAB 5: SYSTEM OPTIMIZER
    # =========================================================================
    def _build_optimizer_tab(self):
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color="#0d1117")
        self.tab_frames["optimizer"] = tab

        header = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        header.pack(fill="x", padx=15, pady=(15, 10))
        ctk.CTkLabel(header, text=self._tr("opt_title"), font=ctk.CTkFont(size=18, weight="bold"), text_color="#58a6ff").pack(anchor="w", padx=15, pady=(10, 2))
        ctk.CTkLabel(header, text=self._tr("opt_sub"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(anchor="w", padx=15, pady=(0, 10))

        # Optimizer Action Bar
        btn_bar = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        btn_bar.pack(fill="x", padx=15, pady=(0, 15))

        self.btn_run_opt = ctk.CTkButton(btn_bar, text=self._tr("btn_run_opt"), command=self.run_optimizer_async, fg_color="#238636", hover_color="#2ea043", font=ctk.CTkFont(weight="bold"))
        self.btn_run_opt.pack(side="left", padx=15, pady=10)

        # Progress bar
        self.opt_progress = ctk.CTkProgressBar(tab, height=12, progress_color="#238636")
        self.opt_progress.set(0)
        self.opt_progress.pack(fill="x", padx=15, pady=(0, 5))

        self.opt_status_lbl = ctk.CTkLabel(tab, text=self._tr("ui.optimizer_idle"), font=ctk.CTkFont(size=12), text_color="#8b949e")
        self.opt_status_lbl.pack(anchor="w", padx=15, pady=(0, 15))

        # Before & After Comparison Cards
        comp_grid = ctk.CTkFrame(tab, fg_color="transparent")
        comp_grid.pack(fill="x", padx=15, pady=0)
        comp_grid.grid_columnconfigure((0, 1, 2), weight=1)

        # RAM Freed Card
        self.card_ram_gain = ctk.CTkFrame(comp_grid, fg_color="#161b22", corner_radius=8)
        self.card_ram_gain.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        ctk.CTkLabel(self.card_ram_gain, text=self._tr("gain_ram"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(padx=10, pady=(10, 2))
        self.lbl_ram_gain = ctk.CTkLabel(self.card_ram_gain, text=self._tr("ui.0_0_mb"), font=ctk.CTkFont(size=22, weight="bold"), text_color="#58a6ff")
        self.lbl_ram_gain.pack(padx=10, pady=(0, 10))

        # Storage Cleaned Card
        self.card_storage_gain = ctk.CTkFrame(comp_grid, fg_color="#161b22", corner_radius=8)
        self.card_storage_gain.grid(row=0, column=1, padx=6, sticky="nsew")
        ctk.CTkLabel(self.card_storage_gain, text=self._tr("gain_storage"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(padx=10, pady=(10, 2))
        self.lbl_storage_gain = ctk.CTkLabel(self.card_storage_gain, text=self._tr("ui.0_0_mb"), font=ctk.CTkFont(size=22, weight="bold"), text_color="#238636")
        self.lbl_storage_gain.pack(padx=10, pady=(0, 10))

        # Battery drain card (not measured)
        self.card_drain_gain = ctk.CTkFrame(comp_grid, fg_color="#161b22", corner_radius=8)
        self.card_drain_gain.grid(row=0, column=2, padx=(6, 0), sticky="nsew")
        ctk.CTkLabel(self.card_drain_gain, text=self._tr("gain_drain"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(padx=10, pady=(10, 2))
        self.lbl_drain_gain = ctk.CTkLabel(self.card_drain_gain, text=self._tr("ui.0_0_ma"), font=ctk.CTkFont(size=22, weight="bold"), text_color="#f59e0b")
        self.lbl_drain_gain.pack(padx=10, pady=(0, 10))

        # Live Execution Terminal
        self.opt_log_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.opt_log_card.pack(fill="both", expand=True, padx=15, pady=15)
        ctk.CTkLabel(self.opt_log_card, text=self._tr("card_opt_log"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.opt_log_text = ctk.CTkTextbox(self.opt_log_card, height=260, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.opt_log_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

    # =========================================================================
    # TAB 6: RESCUE & SYSTEM RECOVERY
    # =========================================================================
    def _build_rescue_tab(self):
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color="#0d1117")
        self.tab_frames["rescue_center"] = tab

        header = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        header.pack(fill="x", padx=15, pady=(15, 10))
        ctk.CTkLabel(header, text=self._tr("rescue_title"), font=ctk.CTkFont(size=18, weight="bold"), text_color="#58a6ff").pack(anchor="w", padx=15, pady=(10, 2))
        ctk.CTkLabel(header, text=self._tr("rescue_sub"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(anchor="w", padx=15, pady=(0, 10))

        # Scrcpy Mirroring Card
        mirror_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        mirror_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(mirror_card, text=self._tr("card_mirror"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        mirror_btns = ctk.CTkFrame(mirror_card, fg_color="transparent")
        mirror_btns.pack(fill="x", padx=15, pady=(0, 15))

        self.btn_launch_mirror = ctk.CTkButton(mirror_btns, text=self._tr("btn_launch_mirror"), command=self._launch_scrcpy, fg_color="#1f6feb", hover_color="#388bfd")
        self.btn_launch_mirror.pack(side="left", padx=(0, 8))

        self.btn_stop_mirror = ctk.CTkButton(mirror_btns, text=self._tr("btn_stop_mirror"), command=self._stop_scrcpy, fg_color="#21262d", hover_color="#30363d")
        self.btn_stop_mirror.pack(side="left", padx=8)

        self.btn_launch_otg = ctk.CTkButton(mirror_btns, text=self._tr("btn_scrcpy_otg"), command=self._launch_scrcpy_otg, fg_color="#9333ea", hover_color="#7e22ce")
        self.btn_launch_otg.pack(side="left", padx=8)

        self.mirror_off_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(mirror_btns, text=self._tr("check_turn_off"), variable=self.mirror_off_var).pack(side="left", padx=15)

        # Virtual On-Screen Control Pad Card
        self.vpad_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.vpad_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.vpad_card, text=self._tr("card_vpad"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8").pack(anchor="w", padx=15, pady=(10, 5))

        # PIN / Password row
        vpad_row1 = ctk.CTkFrame(self.vpad_card, fg_color="transparent")
        vpad_row1.pack(fill="x", padx=15, pady=(5, 8))
        ctk.CTkLabel(vpad_row1, text=self._tr("lbl_vpad_pin"), font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 8))
        self.vpad_pin_entry = ctk.CTkEntry(vpad_row1, placeholder_text="1234", width=140)
        self.vpad_pin_entry.pack(side="left", padx=(0, 8))
        ctk.CTkButton(vpad_row1, text=self._tr("btn_send_pin"), command=self._send_virtual_pin, fg_color="#2563eb", hover_color="#1d4ed8", width=110).pack(side="left", padx=4)
        ctk.CTkButton(vpad_row1, text=self._tr("btn_send_enter"), command=lambda: self._send_virtual_key(66), fg_color="#21262d", hover_color="#30363d", width=80).pack(side="left", padx=4)
        ctk.CTkButton(vpad_row1, text=self._tr("btn_swipe_unlock"), command=self._send_virtual_swipe, fg_color="#059669", hover_color="#047857", width=140).pack(side="left", padx=8)

        # Hardware Keys Simulation row
        vpad_row2 = ctk.CTkFrame(self.vpad_card, fg_color="transparent")
        vpad_row2.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkButton(vpad_row2, text=self._tr("btn_key_power"), command=lambda: self._send_virtual_key(26), fg_color="#dc2626", hover_color="#b91c1c", width=90).pack(side="left", padx=4)
        ctk.CTkButton(vpad_row2, text=self._tr("btn_key_vol_up"), command=lambda: self._send_virtual_key(24), fg_color="#21262d", hover_color="#30363d", width=90).pack(side="left", padx=4)
        ctk.CTkButton(vpad_row2, text=self._tr("btn_key_vol_down"), command=lambda: self._send_virtual_key(25), fg_color="#21262d", hover_color="#30363d", width=90).pack(side="left", padx=4)
        ctk.CTkButton(vpad_row2, text=self._tr("btn_key_home"), command=lambda: self._send_virtual_key(3), fg_color="#21262d", hover_color="#30363d", width=80).pack(side="left", padx=4)
        ctk.CTkButton(vpad_row2, text=self._tr("btn_key_back"), command=lambda: self._send_virtual_key(4), fg_color="#21262d", hover_color="#30363d", width=80).pack(side="left", padx=4)
        ctk.CTkButton(vpad_row2, text=self._tr("btn_key_menu"), command=lambda: self._send_virtual_key(82), fg_color="#21262d", hover_color="#30363d", width=80).pack(side="left", padx=4)

        # Wireless ADB Card
        self.wireless_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.wireless_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.wireless_card, text=self._tr("card_wireless_adb"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#10b981").pack(anchor="w", padx=15, pady=(10, 5))

        # Row 1: Enable TCP/IP over USB
        w_row1 = ctk.CTkFrame(self.wireless_card, fg_color="transparent")
        w_row1.pack(fill="x", padx=15, pady=(5, 8))
        ctk.CTkButton(w_row1, text=self._tr("btn_enable_wireless"), command=self._enable_wireless_adb, fg_color="#059669", hover_color="#047857").pack(side="left", padx=(0, 10))
        self.lbl_wireless_ip = ctk.CTkLabel(w_row1, text=self._tr("ui.ip"), font=ctk.CTkFont(size=12, weight="bold"), text_color="#34d399")
        self.lbl_wireless_ip.pack(side="left", padx=10)

        # Row 2: Connect / Disconnect
        w_row2 = ctk.CTkFrame(self.wireless_card, fg_color="transparent")
        w_row2.pack(fill="x", padx=15, pady=(0, 8))
        ctk.CTkLabel(w_row2, text=self._tr("lbl_wireless_target"), font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 8))
        self.entry_wireless_addr = ctk.CTkEntry(w_row2, placeholder_text="192.168.1.100:5555", width=200)
        self.entry_wireless_addr.pack(side="left", padx=(0, 8))
        ctk.CTkButton(w_row2, text=self._tr("btn_wireless_connect"), command=self._connect_wireless_adb, fg_color="#2563eb", hover_color="#1d4ed8", width=90).pack(side="left", padx=4)
        ctk.CTkButton(w_row2, text=self._tr("btn_disconnect_wireless"), command=self._disconnect_wireless_adb, fg_color="#21262d", hover_color="#30363d", width=120).pack(side="left", padx=4)

        # Row 3: Android 11+ Pairing
        w_row3 = ctk.CTkFrame(self.wireless_card, fg_color="transparent")
        w_row3.pack(fill="x", padx=15, pady=(0, 10))
        ctk.CTkLabel(w_row3, text=self._tr("lbl_wireless_pair"), font=ctk.CTkFont(size=12)).pack(side="left", padx=(0, 8))
        self.entry_pair_addr = ctk.CTkEntry(w_row3, placeholder_text="192.168.1.100:37123", width=180)
        self.entry_pair_addr.pack(side="left", padx=(0, 8))
        ctk.CTkLabel(w_row3, text=self._tr("lbl_wireless_code"), font=ctk.CTkFont(size=12)).pack(side="left", padx=(8, 4))
        self.entry_pair_code = ctk.CTkEntry(w_row3, placeholder_text="123456", width=100)
        self.entry_pair_code.pack(side="left", padx=(0, 8))
        ctk.CTkButton(w_row3, text=self._tr("btn_pair_wireless"), command=self._pair_wireless_adb, fg_color="#7c3aed", hover_color="#6d28d9", width=120).pack(side="left", padx=4)

        self.wireless_log_text = ctk.CTkTextbox(self.wireless_card, height=80, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.wireless_log_text.pack(fill="x", padx=15, pady=(0, 15))
        self.wireless_log_text.insert("end", self._tr("ui.to_enable_wireless_adb_connect_device"))

        # Setup Wizard & System Provisioning Card
        prov_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        prov_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(prov_card, text=self._tr("card_provisioning"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        prov_btns = ctk.CTkFrame(prov_card, fg_color="transparent")
        prov_btns.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkButton(prov_btns, text=self._tr("btn_check_prov"), command=self._check_provisioning, fg_color="#21262d", hover_color="#30363d").pack(side="left", padx=(0, 8))
        ctk.CTkButton(prov_btns, text=self._tr("btn_fix_prov"), command=self._fix_provisioning, fg_color="#238636", hover_color="#2ea043").pack(side="left", padx=8)
        ctk.CTkButton(prov_btns, text=self._tr("btn_open_settings"), command=self._launch_settings, fg_color="#1f6feb", hover_color="#388bfd").pack(side="left", padx=8)

        self.prov_log_text = ctk.CTkTextbox(prov_card, height=90, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.prov_log_text.pack(fill="x", padx=15, pady=(0, 15))
        self.prov_log_text.insert("end", self._tr("ui.click_inspect_setup_flags_to_verify"))

        # Fastboot Rescue Card
        fb_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        fb_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(fb_card, text=self._tr("card_fb"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        fb_btns = ctk.CTkFrame(fb_card, fg_color="transparent")
        fb_btns.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkButton(fb_btns, text=self._tr("btn_fb_vars"), command=self._read_fastboot_vars, fg_color="#21262d", hover_color="#30363d").pack(side="left", padx=(0, 8))
        ctk.CTkButton(fb_btns, text=self._tr("btn_fix_bootloop"), command=self._fix_cache_bootloop, fg_color="#da3633", hover_color="#f85149").pack(side="left", padx=8)
        ctk.CTkButton(fb_btns, text=self._tr("btn_fb_reboot"), command=lambda: self.bridge.fastboot_reboot("", self.current_serial.get()), fg_color="#21262d", hover_color="#30363d").pack(side="left", padx=8)

        # Flash Partition Row
        flash_bar = ctk.CTkFrame(fb_card, fg_color="transparent")
        flash_bar.pack(fill="x", padx=15, pady=(5, 15))

        ctk.CTkLabel(flash_bar, text=self._tr("lbl_partition")).pack(side="left", padx=(0, 6))
        self.part_combo = ctk.CTkComboBox(flash_bar, values=["boot", "recovery", "vbmeta", "dtbo", "system"], width=120)
        self.part_combo.pack(side="left", padx=(0, 10))

        self.img_path_entry = ctk.CTkEntry(flash_bar, placeholder_text=self._tr("ui.path_to_image_img"), width=260)
        self.img_path_entry.pack(side="left", padx=(0, 6))

        ctk.CTkButton(flash_bar, text=self._tr("btn_browse"), width=80, command=self._browse_image_file, fg_color="#21262d", hover_color="#30363d").pack(side="left", padx=(0, 10))
        ctk.CTkButton(flash_bar, text=self._tr("btn_flash"), command=self._flash_partition, fg_color="#1f6feb", hover_color="#388bfd").pack(side="left")

        self.rescue_log_text = ctk.CTkTextbox(fb_card, height=120, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.rescue_log_text.pack(fill="x", padx=15, pady=(0, 15))

        # Emergency Data Extraction Card
        data_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        data_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(data_card, text=self._tr("card_data"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        data_bar = ctk.CTkFrame(data_card, fg_color="transparent")
        data_bar.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkLabel(data_bar, text=self._tr("lbl_category")).pack(side="left", padx=(0, 6))
        self.data_cat_combo = ctk.CTkComboBox(
            data_bar,
            values=[self._tr("ui.all_photos_all_photos_dcim"), self._tr("ui.full_storage_full_storage_dump"), self._tr("ui.whatsapp_whatsapp_media_only_chats_are"), self._tr("ui.downloads_downloads"), self._tr("ui.documents_documents")],
            width=240
        )
        self.data_cat_combo.pack(side="left", padx=(0, 10))

        ctk.CTkButton(data_bar, text=self._tr("btn_pull_data"), command=self._pull_emergency_data, fg_color="#238636", hover_color="#2ea043").pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            data_bar,
            text=self._tr("ui.1_click_full_photos_pull"),
            command=self._pull_all_photos_quick,
            fg_color="#059669",
            hover_color="#047857"
        ).pack(side="left")

        ctk.CTkButton(
            data_bar,
            text=self._tr("btn_one_click_backup"),
            command=self._one_click_customer_backup,
            fg_color="#10b981",
            hover_color="#059669"
        ).pack(side="left", padx=8)

        self.data_log_text = ctk.CTkTextbox(data_card, height=110, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.data_log_text.pack(fill="x", padx=15, pady=(0, 15))
        self.data_log_text.insert("end", self._tr("ui.emergency_data_and_photo_pull_from"))

        # ADB Sideload OTA ROM Flasher Card
        sideload_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        sideload_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(sideload_card, text=self._tr("card_sideload"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8").pack(anchor="w", padx=15, pady=(10, 5))

        sideload_bar = ctk.CTkFrame(sideload_card, fg_color="transparent")
        sideload_bar.pack(fill="x", padx=15, pady=(0, 10))

        self.sideload_entry = ctk.CTkEntry(sideload_bar, placeholder_text=self._tr("ui.path_to_ota_update_package_zip"), width=380)
        self.sideload_entry.pack(side="left", padx=(0, 8))

        ctk.CTkButton(sideload_bar, text=self._tr("btn_browse_ota"), width=120, command=self._browse_ota_file, fg_color="#21262d", hover_color="#30363d").pack(side="left", padx=(0, 8))
        ctk.CTkButton(sideload_bar, text=self._tr("btn_flash_sideload"), command=self._flash_sideload, fg_color="#0284c7", hover_color="#0369a1").pack(side="left")

        self.sideload_progress = ctk.CTkProgressBar(sideload_card, height=10, progress_color="#0284c7")
        self.sideload_progress.set(0)
        self.sideload_progress.pack(fill="x", padx=15, pady=(5, 5))

        self.sideload_lbl = ctk.CTkLabel(sideload_card, text=self._tr("ui.ready_put_device_in_recovery_mode"), font=ctk.CTkFont(size=12), text_color="#8b949e")
        self.sideload_lbl.pack(anchor="w", padx=15, pady=(0, 5))

        self.sideload_text = ctk.CTkTextbox(sideload_card, height=100, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.sideload_text.pack(fill="x", padx=15, pady=(0, 15))
        self.sideload_text.insert("end", self._tr("ui.stream_official_ota_rom_update_zip"))

        # Fastboot Active Slot (A/B) & Anti-Rollback (ARB) Card
        slot_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        slot_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(slot_card, text=self._tr("card_ab_slot"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#f59e0b").pack(anchor="w", padx=15, pady=(10, 5))

        slot_bar = ctk.CTkFrame(slot_card, fg_color="transparent")
        slot_bar.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkButton(slot_bar, text=self._tr("btn_switch_slot_a"), command=lambda: self._switch_slot("a"), fg_color="#2563eb", hover_color="#1d4ed8").pack(side="left", padx=(0, 8))
        ctk.CTkButton(slot_bar, text=self._tr("btn_switch_slot_b"), command=lambda: self._switch_slot("b"), fg_color="#2563eb", hover_color="#1d4ed8").pack(side="left", padx=8)
        ctk.CTkButton(slot_bar, text=self._tr("btn_check_arb"), command=self._check_arb, fg_color="#dc2626", hover_color="#b91c1c").pack(side="left", padx=8)

        self.slot_log_text = ctk.CTkTextbox(slot_card, height=90, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.slot_log_text.pack(fill="x", padx=15, pady=(0, 15))
        self.slot_log_text.insert("end", self._tr("ui.in_fastboot_switch_between_slot_a"))

        # Critical Partitions & Radio Backup Card
        part_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        part_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(part_card, text=self._tr("card_partition_backup"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#10b981").pack(anchor="w", padx=15, pady=(10, 5))

        part_bar = ctk.CTkFrame(part_card, fg_color="transparent")
        part_bar.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkButton(part_bar, text=self._tr("btn_backup_critical"), command=self._backup_critical_partitions, fg_color="#059669", hover_color="#047857").pack(side="left", padx=(0, 8))
        ctk.CTkButton(part_bar, text=self._tr("btn_backup_all"), command=self._backup_all_partitions, fg_color="#047857", hover_color="#065f46").pack(side="left", padx=8)
        ctk.CTkButton(part_bar, text=self._tr("btn_open_backups"), command=self._open_backups_folder, fg_color="#21262d", hover_color="#30363d").pack(side="left", padx=8)

        self.part_backup_text = ctk.CTkTextbox(part_card, height=110, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.part_backup_text.pack(fill="x", padx=15, pady=(0, 15))
        self.part_backup_text.insert("end", self._tr("ui.dump_and_backup_critical_radio_calibration"))

        # Batch APK Installer & Extractor Card
        apk_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        apk_card.pack(fill="x", padx=15, pady=0)
        ctk.CTkLabel(apk_card, text=self._tr("card_batch_apk"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#8b5cf6").pack(anchor="w", padx=15, pady=(10, 5))

        apk_bar = ctk.CTkFrame(apk_card, fg_color="transparent")
        apk_bar.pack(fill="x", padx=15, pady=(0, 10))

        ctk.CTkButton(apk_bar, text=self._tr("btn_install_batch_apk"), command=self._batch_install_apks, fg_color="#7c3aed", hover_color="#6d28d9").pack(side="left", padx=(0, 8))
        ctk.CTkButton(apk_bar, text=self._tr("btn_extract_apps"), command=self._extract_user_apps, fg_color="#6d28d9", hover_color="#5b21b6").pack(side="left", padx=8)

        self.apk_log_text = ctk.CTkTextbox(apk_card, height=110, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.apk_log_text.pack(fill="x", padx=15, pady=(0, 15))
        self.apk_log_text.insert("end", self._tr("ui.batch_install_multiple_apk_files_r"))

    # =========================================================================
    # TAB 7: REPORTS & CERTIFICATE EXPORTER
    # =========================================================================
    def _build_reports_tab(self):
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color="#0d1117")
        self.tab_frames["reports"] = tab

        header = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        header.pack(fill="x", padx=15, pady=(15, 10))
        ctk.CTkLabel(header, text=self._tr("reports_title"), font=ctk.CTkFont(size=18, weight="bold"), text_color="#58a6ff").pack(anchor="w", padx=15, pady=(10, 2))
        ctk.CTkLabel(header, text=self._tr("reports_sub"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(anchor="w", padx=15, pady=(0, 10))

        # Customer Intake & Ticket Card
        ticket_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        ticket_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(ticket_card, text=self._tr("card_cust_ticket"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 8))

        t_grid = ctk.CTkFrame(ticket_card, fg_color="transparent")
        t_grid.pack(fill="x", padx=15, pady=(0, 12))
        t_grid.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # Field 1: Customer Name
        ctk.CTkLabel(t_grid, text=self._tr("lbl_cust_name"), font=ctk.CTkFont(size=12), text_color="#8b949e").grid(row=0, column=0, padx=6, pady=2, sticky="w")
        self.cust_name_entry = ctk.CTkEntry(t_grid, placeholder_text=self._tr("ui.john_doe"))
        self.cust_name_entry.grid(row=1, column=0, padx=6, pady=(0, 8), sticky="ew")

        # Field 2: Customer Phone
        ctk.CTkLabel(t_grid, text=self._tr("lbl_cust_phone"), font=ctk.CTkFont(size=12), text_color="#8b949e").grid(row=0, column=1, padx=6, pady=2, sticky="w")
        self.cust_phone_entry = ctk.CTkEntry(t_grid, placeholder_text=self._tr("ui.970_59_xxxxxxx"))
        self.cust_phone_entry.grid(row=1, column=1, padx=6, pady=(0, 8), sticky="ew")

        # Field 3: Reported Fault
        ctk.CTkLabel(t_grid, text=self._tr("lbl_cust_fault"), font=ctk.CTkFont(size=12), text_color="#8b949e").grid(row=0, column=2, padx=6, pady=2, sticky="w")
        self.cust_fault_entry = ctk.CTkEntry(t_grid, placeholder_text=self._tr("ui.screen_charging_issue"))
        self.cust_fault_entry.grid(row=1, column=2, padx=6, pady=(0, 8), sticky="ew")

        # Field 4: Estimated Repair Cost
        ctk.CTkLabel(t_grid, text=self._tr("lbl_repair_cost"), font=ctk.CTkFont(size=12), text_color="#8b949e").grid(row=0, column=3, padx=6, pady=2, sticky="w")
        self.cust_cost_entry = ctk.CTkEntry(t_grid, placeholder_text="0.00")
        self.cust_cost_entry.grid(row=1, column=3, padx=6, pady=(0, 8), sticky="ew")

        # Technician Name and Certificate Buttons
        btn_row = ctk.CTkFrame(ticket_card, fg_color="transparent")
        btn_row.pack(fill="x", padx=15, pady=(0, 15))

        ctk.CTkLabel(btn_row, text=self._tr("lbl_tech_name"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(side="left", padx=(0, 8))
        self.tech_name_entry = ctk.CTkEntry(btn_row, width=220)
        self.tech_name_entry.insert(0, self._tr("ui.chief_hardware_technician"))
        self.tech_name_entry.pack(side="left", padx=(0, 15))

        self.btn_export_cert = ctk.CTkButton(btn_row, text=self._tr("btn_gen_signed_cert"), command=self._generate_and_open_certificate, fg_color="#238636", hover_color="#2ea043")
        self.btn_export_cert.pack(side="left", padx=(0, 10))

        ctk.CTkButton(btn_row, text=self._tr("btn_open_folder"), command=lambda: webbrowser.open(REPORTS_DIR), fg_color="#21262d", hover_color="#30363d").pack(side="left")

        # Past Reports List
        reports_list_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        reports_list_card.pack(fill="both", expand=True, padx=15, pady=0)
        ctk.CTkLabel(reports_list_card, text=self._tr("card_past_reports"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.reports_text = ctk.CTkTextbox(reports_list_card, height=300, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.reports_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))

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
                    self.alert_card.configure(fg_color="#331414", border_color="#ef4444")
                    self.alert_title_lbl.configure(
                        text=self._tr("ui.device_is_unauthorized", serials_str=serials_str),
                        text_color="#f87171"
                    )
                    self.alert_desc_lbl.configure(
                        text=self._tr("alert_unauthorized_desc"),
                        text_color="#fecaca"
                    )
                    self.alert_card.pack(fill="x", padx=15, pady=(0, 15))
                if hasattr(self, "guide_card"):
                    self.guide_card.pack_forget()

                self.adb_status_label.configure(
                    text=self._tr("ui.adb_unauthorized_accept_rsa_prompt", serials_str=serials_str),
                    text_color="#ef4444"
                )

            # 2. State: Physical phone detected on Windows USB PnP, but ADB debugging is OFF
            elif diag.get("has_pnp_without_adb") and pnp_phones:
                phone_names = ", ".join(p["name"] for p in pnp_phones)
                if hasattr(self, "alert_card"):
                    self.alert_card.configure(fg_color="#2e1a05", border_color="#f59e0b")
                    self.alert_title_lbl.configure(
                        text=self._tr("ui.phone_detected_on_usb_but_usb", phone_names=phone_names),
                        text_color="#fbbf24"
                    )
                    desc_text = self._tr("alert_pnp_desc").replace("{names}", phone_names)
                    self.alert_desc_lbl.configure(
                        text=desc_text,
                        text_color="#fef3c7"
                    )
                    self.alert_card.pack(fill="x", padx=15, pady=(0, 15))
                if hasattr(self, "guide_card"):
                    self.guide_card.pack_forget()

                self.adb_status_label.configure(
                    text=self._tr("ui.usb_enable_usb_debugging", phone_names=phone_names),
                    text_color="#f59e0b"
                )

            # 3. State: Device is offline
            elif offline_devs and not authorized_devs and not fastboot_devs:
                serials_str = ", ".join(d["serial"] for d in offline_devs)
                if hasattr(self, "alert_card"):
                    self.alert_card.configure(fg_color="#2b1a10", border_color="#ea580c")
                    self.alert_title_lbl.configure(
                        text=self._tr("ui.device_is_offline", serials_str=serials_str),
                        text_color="#fb923c"
                    )
                    self.alert_desc_lbl.configure(
                        text=self._tr("alert_offline_desc"),
                        text_color="#fed7aa"
                    )
                    self.alert_card.pack(fill="x", padx=15, pady=(0, 15))
                if hasattr(self, "guide_card"):
                    self.guide_card.pack_forget()

                self.adb_status_label.configure(
                    text=self._tr("ui.adb_offline_replug_usb_cable"),
                    text_color="#ea580c"
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
                    self.guide_card.pack(fill="x", padx=15, pady=(0, 15))
                self.adb_status_label.configure(text=self._tr("adb_disconnected"), text_color="#8b949e")

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
                self.adb_status_label.configure(text=self._tr("ui.adb", upper=chosen['state'].upper(), serial=chosen['serial']), text_color="#238636")
                self.run_profile_async()
            elif cur in ready_serials:
                cur_dev = next(d for d in devices if d["serial"] == cur)
                self.adb_status_label.configure(text=self._tr("ui.adb_2", upper=cur_dev['state'].upper(), cur=cur), text_color="#238636")
            elif not ready_serials:
                self.current_serial.set("")

        self.after(0, update_ui)

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
                self.spec_labels["model"].configure(text=f"{profile.brand} {profile.model}")
                self.spec_labels["serial"].configure(text=profile.serial)
                self.spec_labels["soc"].configure(text=f"{profile.soc_chipset} ({profile.hardware})")
                self.spec_labels["cores"].configure(text=self._tr("ui.cores", cpu_cores=profile.cpu_cores, cpu_architecture=profile.cpu_architecture))
                self.spec_labels["android"].configure(text=self._tr("ui.android_api", android_version=profile.android_version, sdk_version=profile.sdk_version))
                self.spec_labels["build_id"].configure(text=profile.build_id[:32])
                self.spec_labels["security"].configure(text=profile.security_patch)
                self.spec_labels["kernel"].configure(text=profile.kernel_version[:36] if profile.kernel_version else "Standard")
                
                root_str = self._tr("ui.rooted")
                no_root_str = self._tr("ui.no_standard_oem")
                self.spec_labels["root"].configure(text=root_str if profile.is_rooted else no_root_str, text_color="#da3633" if profile.is_rooted else "#238636")
                
                self.spec_labels["selinux"].configure(text=profile.selinux_mode)
                self.spec_labels["display"].configure(text=f"{profile.display_resolution} @ {profile.display_refresh_rate} ({profile.display_density})")

                # Gauges
                if profile.ram_total_mb > 0:
                    pct = profile.ram_used_pct / 100.0
                    self.ram_bar.set(pct)
                    self.ram_title_lbl.configure(text=self._tr("ui.ram_mb_free_mb", ram_free_mb=profile.ram_free_mb, ram_total_mb=profile.ram_total_mb, ram_used_pct=profile.ram_used_pct))

                if profile.storage_total_gb > 0:
                    pct = profile.storage_used_pct / 100.0
                    self.storage_bar.set(pct)
                    self.storage_title_lbl.configure(text=self._tr("ui.storage_gb_free_gb", storage_free_gb=profile.storage_free_gb, storage_total_gb=profile.storage_total_gb, storage_used_pct=profile.storage_used_pct))

                self.batt_bar.set(profile.battery_level_pct / 100.0)
                self.batt_title_lbl.configure(text=self._tr("ui.battery_2", battery_level_pct=profile.battery_level_pct, battery_status=profile.battery_status))
                self.batt_quick_lbl.configure(text=self._tr("ui.voltage_mv_temp_c_health", battery_voltage_mv=profile.battery_voltage_mv, battery_temp_c=profile.battery_temp_c, battery_health=profile.battery_health))

            self.after(0, update_ui)

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
                self.pmic_text.delete("1.0", "end")
                self.pmic_text.insert("end", "\n".join(out_lines))

            self.after(0, update_ui)

        threading.Thread(target=task, daemon=True).start()

    def run_bootrom_scan_async(self):
        is_ar = self.current_lang == "ar"
        self.bootrom_text.delete("1.0", "end")
        self.bootrom_text.insert("end", self._tr("ui.scanning_windows_pnp_ports_for_qualcomm"))

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
                self.bootrom_text.delete("1.0", "end")
                self.bootrom_text.insert("end", "\n".join(lines))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_leakage_test_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        is_ar = self.current_lang == "ar"
        self.leakage_progress.set(0)
        self.leakage_text.delete("1.0", "end")
        self.leakage_text.insert("end", self._tr("ui.screen_off_battery_current_test_only"))

        def progress(pct, val, msg):
            def upd():
                self.leakage_progress.set(pct / 100.0)
                val_txt = f"{val} mA" if val is not None else "UNKNOWN"
                ar_msg = self._tr("ui.sample_10_battery_current", pct=int(pct/10), val_txt=val_txt)
                self.leakage_lbl.configure(text=self._m(ar_msg))
                self.leakage_text.insert("end", f"{ar_msg}\n")
                self.leakage_text.see("end")
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
                self.leakage_text.insert("end", verdict + "\n")
                self.leakage_text.see("end")
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
                self.thermal_text.delete("1.0", "end")
                self.thermal_text.insert("end", "\n".join(out))
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
                self.panic_text.delete("1.0", "end")
                self.panic_text.insert("end", "\n".join(out))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_blackbox_forensics_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        is_ar = self.current_lang == "ar"
        self.blackbox_text.delete("1.0", "end")
        self.blackbox_text.insert("end", self._tr("ui.analyzing_blackbox_forensics_dropbox_boot_reason"))

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
                self.blackbox_text.delete("1.0", "end")
                self.blackbox_text.insert("end", "\n".join(lines))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def _toggle_live_telemetry(self):
        is_ar = self.current_lang == "ar"
        if self._telemetry_running:
            self._telemetry_running = False
            self.btn_toggle_telemetry.configure(text=self._tr("ui.live_telemetry_off"), fg_color="#21262d")
        else:
            serial = self.current_serial.get()
            if not serial:
                messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
                return
            self._telemetry_running = True
            self.btn_toggle_telemetry.configure(text=self._tr("ui.live_telemetry_active_1_5s"), fg_color="#059669")
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
                self.sensors_text.delete("1.0", "end")
                self.sensors_text.insert("end", "\n".join(s_lines))
                self.camera_text.delete("1.0", "end")
                self.camera_text.insert("end", "\n".join(c_lines))
                self.fp_text.delete("1.0", "end")
                self.fp_text.insert("end", "\n".join(fp_lines))
                self.keys_text.delete("1.0", "end")
                self.keys_text.insert("end", "\n".join(key_lines))
                self.sim_text.delete("1.0", "end")
                self.sim_text.insert("end", "\n".join(sim_lines))
                self.backlight_text.delete("1.0", "end")
                self.backlight_text.insert("end", "\n".join(disp_lines))
                if hasattr(self, "wifi_text"):
                    self.wifi_text.delete("1.0", "end")
                    self.wifi_text.insert("end", "\n".join(wifi_lines))
                if hasattr(self, "bt_text"):
                    self.bt_text.delete("1.0", "end")
                    self.bt_text.insert("end", "\n".join(bt_lines))
                if hasattr(self, "chips_text"):
                    self.chips_text.delete("1.0", "end")
                    self.chips_text.insert("end", "\n".join(chip_lines))
                if hasattr(self, "audio_codec_text"):
                    self.audio_codec_text.delete("1.0", "end")
                    self.audio_codec_text.insert("end", "\n".join(codec_lines))
                if hasattr(self, "panel_info_text"):
                    self.panel_info_text.delete("1.0", "end")
                    self.panel_info_text.insert("end", "\n".join(panel_lines))
                self.pointer_location_var.set(report.pointer_location_active)

            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_buttons_test_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        is_ar = self.current_lang == "ar"
        self.keys_text.delete("1.0", "end")
        self.keys_text.insert("end", self._tr("ui.listening_for_physical_key_presses_press"))

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
                self.keys_text.delete("1.0", "end")
                self.keys_text.insert("end", "\n".join(lines))
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

        self.sec_progress.set(0)
        self.threats_text.delete("1.0", "end")
        is_ar = self.current_lang == "ar"

        def progress(pct, msg):
            def upd():
                self.sec_progress.set(pct / 100.0)
                self.sec_status_lbl.configure(text=self._m(msg))
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
                self.threats_text.delete("1.0", "end")
                self.threats_text.insert("end", "\n".join(t_lines))

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
                self.net_text.delete("1.0", "end")
                self.net_text.insert("end", "\n".join(lines))
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

        is_ar = self.current_lang == "ar"
        self.bloat_text.delete("1.0", "end")
        self.bloat_text.insert("end", self._tr("ui.scanning_installed_oem_bloatware_packages_matchi"))

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
                self.bloat_text.delete("1.0", "end")
                self.bloat_text.insert("end", "\n".join(lines))
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

        self.opt_progress.set(0)
        self.opt_log_text.delete("1.0", "end")

        def progress(pct, msg):
            def upd():
                self.opt_progress.set(pct / 100.0)
                self.opt_status_lbl.configure(text=self._m(msg))
                self.opt_log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
                self.opt_log_text.see("end")
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
                self.lbl_ram_gain.configure(text=f"{_fmt(res.before.ram_free_mb)} -> {_fmt(res.after.ram_free_mb, 'MB')}")
                self.lbl_storage_gain.configure(text=_fmt(res.storage_freed_mb, "MB"))
                self.lbl_drain_gain.configure(text="—")
                self.opt_log_text.insert("end", "\n" + "\n".join(lines) + "\n")
                self.opt_log_text.see("end")
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
        is_ar = self.current_lang == "ar"
        self.nand_text.delete("1.0", "end")
        self.nand_text.insert("end", self._tr("ui.reading_jedec_emmc_ufs_wear_level"))

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
                self.nand_text.delete("1.0", "end")
                self.nand_text.insert("end", "\n".join(lines))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_speed_bench_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_phone_first"))
            return
        is_ar = self.current_lang == "ar"
        self.nand_text.delete("1.0", "end")
        self.nand_text.insert("end", self._tr("ui.running_64mb_sequential_read_write_speed"))

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
                self.nand_text.delete("1.0", "end")
                self.nand_text.insert("end", "\n".join(lines))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_stress_test_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_phone_first"))
            return
        is_ar = self.current_lang == "ar"
        self.stress_progress.set(0)
        self.stress_text.delete("1.0", "end")
        self.stress_text.insert("end", self._tr("ui.starting_30_second_cpu_pmic_stress"))

        def task():
            def prog(snap: StressTestSnapshot, msg: str):
                def update():
                    pct = min(1.0, snap.second / 30.0)
                    self.stress_progress.set(pct)
                    self.stress_lbl.configure(text=self._m(msg))
                    self.stress_text.insert("end", f"{msg}\n")
                    self.stress_text.see("end")
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
                self.stress_progress.set(1.0)
                self.stress_text.insert("end", "\n".join(lines))
                self.stress_text.see("end")
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
        is_ar = self.current_lang == "ar"
        self.fast_charge_text.delete("1.0", "end")
        self.fast_charge_text.insert("end", self._tr("ui.reading_charging_protocol_negotiation_voltage_an"))

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
                self.fast_charge_text.delete("1.0", "end")
                self.fast_charge_text.insert("end", "\n".join(lines))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_reset_gauge_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        is_ar = self.current_lang == "ar"
        def task():
            ok, msg = self.board_diag.reset_battery_stats_and_gauge(serial)
            def upd():
                self.fast_charge_text.delete("1.0", "end")
                self.fast_charge_text.insert("end", self._tr("ui.reset_battery_statistics", msg=msg))
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
        is_ar = self.current_lang == "ar"
        self.storage_text.delete("1.0", "end")
        self.storage_text.insert("end", self._tr("ui.running_emmc_ufs_health_check_read"))

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
                self.storage_text.delete("1.0", "end")
                self.storage_text.insert("end", "\n".join(lines))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_touch_matrix_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        is_ar = self.current_lang == "ar"
        self.touch_text.delete("1.0", "end")
        self.touch_text.insert("end", self._tr("ui.deploying_touch_digitizer_grid_test_to"))

        def task():
            ok, msg = self.touch_matrix.deploy_and_launch_touch_test(serial)
            def upd():
                self.touch_text.delete("1.0", "end")
                self.touch_text.insert("end", self._tr("ui.touch_test", msg=msg))
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
        is_ar = self.current_lang == "ar"
        self.touch_text.delete("1.0", "end")
        self.touch_text.insert("end", self._tr("ui.deploying_full_screen_rgbw_dead_pixel"))

        def task():
            ok, msg = self.hardware_tester.launch_screen_color_test(serial)
            def upd():
                self.touch_text.delete("1.0", "end")
                self.touch_text.insert("end", self._tr("ui.screen_test_status", msg=msg))
                if ok:
                    answer = messagebox.askyesno(
                        self._tr("ui.screen_color_test"),
                        self._tr("ui.interactive_color_test_is_now_running")
                    )
                    if answer:
                        self.hardware_tester.stop_screen_color_test(serial)
                        self.touch_text.insert("end", self._tr("ui.screen_test_closed_and_touch_overlays"))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_speaker_tone_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_device_first"))
            return
        is_ar = self.current_lang == "ar"
        self.audio_codec_text.delete("1.0", "end")
        self.audio_codec_text.insert("end", self._tr("ui.generating_pure_1000hz_sine_test_tone"))

        def task():
            ok, msg = self.hardware_tester.play_speaker_test_tone(serial, freq=1000, duration_sec=3.0)
            def upd():
                self.audio_codec_text.delete("1.0", "end")
                self.audio_codec_text.insert("end", self._tr("ui.speaker_test_output", msg=msg))
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
        is_ar = self.current_lang == "ar"
        self.sensors_text.delete("1.0", "end")
        self.sensors_text.insert("end", self._tr("ui.checking_companion_apk_or_activating_zero"))

        def task():
            ok, msg = self.hardware_tester.install_companion_apk(serial)
            if ok:
                self.hardware_tester.run_companion_broadcast(serial, "START_DIAGNOSTICS")
                res = self.hardware_tester.read_companion_results(serial)
                diag_txt = self._tr("ui.companion_results", res=res)
            else:
                diag_txt = self._tr("ui.companion_status_zero_apk_interactive_suite", msg=msg)
            def upd():
                self.sensors_text.delete("1.0", "end")
                self.sensors_text.insert("end", diag_txt)
                messagebox.showinfo(self._tr("ui.interactive_diag"), diag_txt)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_root_forensics_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_device_first"))
            return
        is_ar = self.current_lang == "ar"
        self.root_forensics_text.delete("1.0", "end")
        self.root_forensics_text.insert("end", self._tr("ui.running_root_forensics_pro_inspecting_persistent"))

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
                self.root_forensics_text.delete("1.0", "end")
                self.root_forensics_text.insert("end", "\n".join(lines))
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
        is_ar = self.current_lang == "ar"
        self.root_forensics_text.delete("1.0", "end")
        self.root_forensics_text.insert("end", self._tr("ui.discovering_critical_efs_nvram_modem_partitions"))

        def task():
            def prog(msg):
                self.after(0, lambda: self.root_forensics_text.insert("end", f"{msg}\n"))
            res = self.board_diag.backup_efs_nvram_partitions(serial=serial, progress_callback=prog)
            self.last_efs_backup = res
            def upd():
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
                self.root_forensics_text.insert("end", "\n".join(lines))
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
        reports = self.db.get_diagnostic_reports(limit=25)
        is_ar = self.current_lang == "ar"
        lines = [self._tr("ui.stored_diagnostic_reports_in_database", reports=len(reports))]
        for r in reports:
            batt_pct = r.get('battery_health_pct')
            batt_str = f"{batt_pct}%" if batt_pct is not None else "UNKNOWN"
            lines.append(self._tr("ui.report_device_date_status_battery_health", id=r['id'], device_serial=r['device_serial'], timestamp=r['timestamp'], overall_status=r['overall_status'], batt_str=batt_str))
        self.reports_text.delete("1.0", "end")
        self.reports_text.insert("end", "\n".join(lines))
