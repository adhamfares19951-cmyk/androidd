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

from setup_bins import verify_binaries, ensure_all_binaries, get_adb_path, get_fastboot_path, get_scrcpy_path
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
from ui.i18n import t

logger = logging.getLogger("MainWindow")
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")
ctk.ThemeManager.theme["CTkFont"]["family"] = "Segoe UI"


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

    header = "\n[الأدلة التقنية المباشرة / Evidence]:" if is_ar else "\n[Technical Evidence]:"
    lines = [header]
    for ev in evidence_list:
        src = getattr(ev, "source", None) or (ev.get("source") if isinstance(ev, dict) else "")
        exc = getattr(ev, "raw_excerpt", None) or (ev.get("raw_excerpt") if isinstance(ev, dict) else "")
        code = getattr(ev, "exit_code", 0) if hasattr(ev, "exit_code") else (ev.get("exit_code") if isinstance(ev, dict) else 0)
        code_str = f" [exit {code}]" if code is not None and code != 0 else ""
        lines.append(f"  • Source: {src}{code_str}")
        if exc:
            for eline in str(exc).strip().splitlines():
                lines.append(f"    {eline}")
    return "\n".join(lines)


class AndroidDoctorApp(ctk.CTk):
    def __init__(self, lang: str = "ar"):
        super().__init__()

        self.current_lang = lang
        self.title("Android Doctor Pro — أندرويد دكتور برو")
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

    def _tr(self, key: str) -> str:
        return t(key, self.current_lang)

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
            text="ANDROID DOCTOR PRO",
            font=ctk.CTkFont(size=17, weight="bold"),
            text_color="#58a6ff"
        )
        self.logo_label.grid(row=0, column=0, padx=15, pady=(15, 2))

        self.subtitle_label = ctk.CTkLabel(
            self.sidebar_frame,
            text="فحص العتاد واللوحة الأم والصيانة" if self.current_lang == "ar" else "Hardware Diagnostics Suite",
            font=ctk.CTkFont(size=12),
            text_color="#8b949e"
        )
        self.subtitle_label.grid(row=1, column=0, padx=15, pady=(0, 10))

        # Language Switcher
        lang_bar = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        lang_bar.grid(row=2, column=0, padx=15, pady=(0, 15))

        self.btn_lang_ar = ctk.CTkButton(
            lang_bar,
            text="عربي",
            width=80,
            height=26,
            font=ctk.CTkFont(size=12, weight="bold"),
            fg_color="#1f6feb" if self.current_lang == "ar" else "#21262d",
            command=lambda: self.switch_language("ar")
        )
        self.btn_lang_ar.pack(side="left", padx=2)

        self.btn_lang_en = ctk.CTkButton(
            lang_bar,
            text="English",
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
        self.content_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="#0d1117")
        self.content_frame.grid(row=0, column=1, sticky="nsew", padx=10, pady=10)
        self.content_frame.grid_columnconfigure(0, weight=1)
        self.content_frame.grid_rowconfigure(0, weight=1)

        # Tabs Container
        self.tab_frames = {}
        self._build_dashboard_tab()
        self._build_board_diag_tab()
        self._build_hardware_tester_tab()
        self._build_malware_hunter_tab()
        self._build_optimizer_tab()
        self._build_rescue_tab()
        self._build_reports_tab()

        # Select initial tab
        self.select_tab("dashboard")
        self.after(300, self.refresh_devices_async)
        self.after(5000, self._auto_poll_devices)

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
        self.btn_lang_ar.configure(fg_color="#1f6feb" if lang == "ar" else "#21262d")
        self.btn_lang_en.configure(fg_color="#1f6feb" if lang == "en" else "#21262d")
        
        # Refresh current view
        for widget in self.content_frame.winfo_children():
            widget.destroy()
        self.tab_frames.clear()

        self._build_dashboard_tab()
        self._build_board_diag_tab()
        self._build_hardware_tester_tab()
        self._build_malware_hunter_tab()
        self._build_optimizer_tab()
        self._build_rescue_tab()
        self._build_reports_tab()

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

    def select_tab(self, key: str):
        for k, frame in self.tab_frames.items():
            if k == key:
                frame.grid(row=0, column=0, sticky="nsew")
                if k in self.nav_buttons:
                    self.nav_buttons[k].configure(fg_color="#1f6feb", text_color="#ffffff")
            else:
                frame.grid_forget()
                if k in self.nav_buttons:
                    self.nav_buttons[k].configure(fg_color="transparent", text_color="#c9d1d9")

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

        self.btn_alert_action = ctk.CTkButton(alert_btns, text="إعادة الفحص الآن", height=28, fg_color="#b91c1c", hover_color="#dc2626", command=self.refresh_devices_async)
        self.btn_alert_action.pack(side="left", padx=(0, 8))

        ctk.CTkButton(alert_btns, text="إعادة تشغيل خادم الاتصال", height=28, fg_color="#21262d", hover_color="#30363d", command=self._restart_adb_service).pack(side="left")

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
        self.ram_title_lbl = ctk.CTkLabel(self.gauges_card, text="RAM: —", font=ctk.CTkFont(size=12), text_color="#c9d1d9")
        self.ram_title_lbl.pack(anchor="w", padx=15, pady=(5, 2))
        self.ram_bar = ctk.CTkProgressBar(self.gauges_card, height=12, progress_color="#1f6feb")
        self.ram_bar.set(0)
        self.ram_bar.pack(fill="x", padx=15, pady=(0, 15))

        # Storage Gauge
        self.storage_title_lbl = ctk.CTkLabel(self.gauges_card, text="Internal Storage: —", font=ctk.CTkFont(size=12), text_color="#c9d1d9")
        self.storage_title_lbl.pack(anchor="w", padx=15, pady=(5, 2))
        self.storage_bar = ctk.CTkProgressBar(self.gauges_card, height=12, progress_color="#238636")
        self.storage_bar.set(0)
        self.storage_bar.pack(fill="x", padx=15, pady=(0, 15))

        # Battery Gauge
        self.batt_title_lbl = ctk.CTkLabel(self.gauges_card, text="Battery: —", font=ctk.CTkFont(size=12), text_color="#c9d1d9")
        self.batt_title_lbl.pack(anchor="w", padx=15, pady=(5, 2))
        self.batt_bar = ctk.CTkProgressBar(self.gauges_card, height=12, progress_color="#e3b341")
        self.batt_bar.set(0)
        self.batt_bar.pack(fill="x", padx=15, pady=(0, 15))

        self.batt_quick_lbl = ctk.CTkLabel(self.gauges_card, text="Voltage: — | Temp: — | Health: —", font=ctk.CTkFont(size=12), text_color="#8b949e")
        self.batt_quick_lbl.pack(anchor="w", padx=15, pady=(0, 10))

    def _restart_adb_service(self):
        def task():
            ok, msg = self.bridge.restart_server()
            def done():
                if ok:
                    messagebox.showinfo("ADB Server", "تمت إعادة تشغيل خادم الاتصال بنجاح. جاري الفحص..." if self.current_lang == "ar" else "ADB server restarted successfully.")
                else:
                    messagebox.showwarning("ADB Notice", f"تمت إعادة التشغيل: {msg}" if self.current_lang == "ar" else f"ADB result: {msg}")
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
        ctk.CTkLabel(box_v, text="جهد البطارية (مقياس الوقود)" if self.current_lang == "ar" else "Battery voltage (fuel gauge)", font=ctk.CTkFont(size=11), text_color="#8b949e").pack(padx=8, pady=(8, 2))
        self.lbl_telem_volt = ctk.CTkLabel(box_v, text="— mV", font=ctk.CTkFont(size=16, weight="bold"), text_color="#58a6ff")
        self.lbl_telem_volt.pack(padx=8, pady=(0, 8))

        # Current Draw
        box_c = ctk.CTkFrame(telem_grid, fg_color="#0d1117", corner_radius=6)
        box_c.grid(row=0, column=1, padx=4, sticky="nsew")
        ctk.CTkLabel(box_c, text="تيار البطارية (يشمل تيار الشحن)" if self.current_lang == "ar" else "Battery current (incl. USB charging)", font=ctk.CTkFont(size=11), text_color="#8b949e").pack(padx=8, pady=(8, 2))
        self.lbl_telem_curr = ctk.CTkLabel(box_c, text="— mA", font=ctk.CTkFont(size=16, weight="bold"), text_color="#f59e0b")
        self.lbl_telem_curr.pack(padx=8, pady=(0, 8))

        # Battery Temp
        box_bt = ctk.CTkFrame(telem_grid, fg_color="#0d1117", corner_radius=6)
        box_bt.grid(row=0, column=2, padx=4, sticky="nsew")
        ctk.CTkLabel(box_bt, text="حرارة البطارية" if self.current_lang == "ar" else "Battery Temp", font=ctk.CTkFont(size=11), text_color="#8b949e").pack(padx=8, pady=(8, 2))
        self.lbl_telem_batt_temp = ctk.CTkLabel(box_bt, text="— °C", font=ctk.CTkFont(size=16, weight="bold"), text_color="#10b981")
        self.lbl_telem_batt_temp.pack(padx=8, pady=(0, 8))

        # CPU Temp
        box_ct = ctk.CTkFrame(telem_grid, fg_color="#0d1117", corner_radius=6)
        box_ct.grid(row=0, column=3, padx=4, sticky="nsew")
        ctk.CTkLabel(box_ct, text="ذروة حرارة المعالج" if self.current_lang == "ar" else "Peak CPU Temp", font=ctk.CTkFont(size=11), text_color="#8b949e").pack(padx=8, pady=(8, 2))
        self.lbl_telem_cpu_temp = ctk.CTkLabel(box_ct, text="— °C", font=ctk.CTkFont(size=16, weight="bold"), text_color="#f87171")
        self.lbl_telem_cpu_temp.pack(padx=8, pady=(0, 8))

        # PMIC Status Card
        self.pmic_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.pmic_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.pmic_card, text=self._tr("card_pmic"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.pmic_text = ctk.CTkTextbox(self.pmic_card, height=130, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.pmic_text.pack(fill="x", padx=15, pady=(0, 15))
        self.pmic_text.insert("end", "اضغط على 'فحص آيسي الشحن (PMIC)' لقراءة الفولت الحقيقي والأمبير...\n" if self.current_lang == "ar" else "Click 'Analyze PMIC & Charging IC' to inspect live hardware values...\n")

        # Fast Charge Protocol & Power Card
        self.fast_charge_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.fast_charge_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.fast_charge_card, text=self._tr("card_fast_charge"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#34d399").pack(anchor="w", padx=15, pady=(10, 5))

        self.fast_charge_text = ctk.CTkTextbox(self.fast_charge_card, height=110, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.fast_charge_text.pack(fill="x", padx=15, pady=(0, 15))
        self.fast_charge_text.insert("end", "انقر فوق 'فحص بروتوكول الشحن السريع' لقياس قدرة الواط الحقيقية وبروتوكول الشاحن (USB-PD / QC)...\n" if self.current_lang == "ar" else "Click 'Diagnose Fast Charge Protocol' to measure live wattage and fast charging protocol...\n")

        # NAND Flash Wear & Speed Benchmark Card
        self.nand_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.nand_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.nand_card, text=self._tr("card_nand_wear"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#818cf8").pack(anchor="w", padx=15, pady=(10, 5))

        self.nand_text = ctk.CTkTextbox(self.nand_card, height=115, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.nand_text.pack(fill="x", padx=15, pady=(0, 15))
        self.nand_text.insert("end", "انقر فوق 'عمر واستهلاك خلايا الذاكرة' أو 'اختبار سرعة الذاكرة (64MB)' لفحص كفاءة شريحة الـ eMMC/UFS...\n" if self.current_lang == "ar" else "Click 'eMMC/UFS Lifespan & Wear' or 'Storage Speed Benchmark (64MB)' to evaluate internal storage IC health...\n")

        # 30-Second CPU & PMIC Load / Stress Test Card
        self.stress_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.stress_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.stress_card, text=self._tr("card_stress_test"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#f87171").pack(anchor="w", padx=15, pady=(10, 5))

        self.stress_progress = ctk.CTkProgressBar(self.stress_card, height=10, progress_color="#dc2626")
        self.stress_progress.set(0)
        self.stress_progress.pack(fill="x", padx=15, pady=(5, 8))

        self.stress_lbl = ctk.CTkLabel(self.stress_card, text="جاهز للاختبار (سيتم تشغيل حمل معالجة لمدة 30 ثانية لمراقبة هبوط الفولت والحرارة)" if self.current_lang == "ar" else "Ready (Will apply a 30s multi-core load to monitor voltage sag and thermal throttling)", font=ctk.CTkFont(size=12), text_color="#8b949e")
        self.stress_lbl.pack(anchor="w", padx=15, pady=(0, 5))

        self.stress_text = ctk.CTkTextbox(self.stress_card, height=115, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.stress_text.pack(fill="x", padx=15, pady=(0, 15))

        # BootROM / Dead Phone Diagnosis Card
        self.bootrom_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.bootrom_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.bootrom_card, text=self._tr("card_bootrom"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#a78bfa").pack(anchor="w", padx=15, pady=(10, 5))

        self.bootrom_text = ctk.CTkTextbox(self.bootrom_card, height=110, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.bootrom_text.pack(fill="x", padx=15, pady=(0, 15))
        self.bootrom_text.insert("end", "انقر فوق 'فحص منافذ BootROM والأجهزة الميتة' لفحص منافذ كوالكوم EDL 9008 وميديا تيك BROM ودورة ريستارت الـ PMIC...\n" if self.current_lang == "ar" else "Click 'Dead Phone / BootROM USB Scanner' to inspect Qualcomm EDL 9008, MediaTek BROM, and PMIC cycling...\n")

        # Leakage Test Live Output Card
        self.leakage_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        self.leakage_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(self.leakage_card, text=self._tr("card_leakage"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#79c0ff").pack(anchor="w", padx=15, pady=(10, 5))

        self.leakage_progress = ctk.CTkProgressBar(self.leakage_card, height=10, progress_color="#da3633")
        self.leakage_progress.set(0)
        self.leakage_progress.pack(fill="x", padx=15, pady=(5, 8))

        self.leakage_lbl = ctk.CTkLabel(self.leakage_card, text="جاهز للاختبار (سيتم إطفاء الشاشة مؤقتاً لعزل سحب المازربورد)", font=ctk.CTkFont(size=12), text_color="#8b949e")
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
        self.blackbox_text.insert("end", "انقر فوق 'تشخيص أسباب إعادة التشغيل المفاجئ' لفحص سجلات DropBox والـ Kernel Panics وأسباب انطفاء المعالج...\n" if self.current_lang == "ar" else "Click 'Run Blackbox Forensics' to parse DropBox logs, reboot history, and kernel ramoops crash reasons...\n")

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
        self.root_forensics_text.insert("end", "انقر فوق 'التحليل الجنائي للكيرنل (Root)' لفحص سجلات الانهيار المحفوظة في Ramoops وناقل I2C وعمر خلايا الذاكرة عبر su...\n" if self.current_lang == "ar" else "Click 'Kernel & Ramoops Forensics (Root)' to inspect persistent ramoops crash logs, I2C bus faults, and exact wear via su...\n")

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

        self.btn_test_keys = ctk.CTkButton(btn_row1, text="فحص الأزرار الحية (5 ثوانٍ)" if self.current_lang == "ar" else "Test Physical Keys Live", command=self.run_buttons_test_async, fg_color="#21262d", hover_color="#30363d")
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
        self.storage_text.insert("end", "انقر فوق 'فحص عمر وسرعة الذاكرة' لاختبار سرعة القراءة والكتابة واستهلاك الخلايا (Wear Out)...\n" if self.current_lang == "ar" else "Click 'Test Storage Wear & Speed' to benchmark read/write speeds and JEDEC life time...\n")

        # Touch Matrix Digitizer Card
        self.touch_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.touch_card.grid(row=4, column=1, padx=(8, 0), pady=0, sticky="nsew")
        ctk.CTkLabel(self.touch_card, text=self._tr("card_touch_matrix"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8").pack(anchor="w", padx=15, pady=(10, 5))

        self.touch_text = ctk.CTkTextbox(self.touch_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.touch_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.touch_text.insert("end", "انقر فوق 'اختبار شبكة اللمس' لنشر شبكة فحص الديجيتيزر على شاشة الهاتف بالكامل...\n" if self.current_lang == "ar" else "Click 'Interactive Touch Digitizer Grid Test' to deploy the full-screen touch grid on the phone...\n")

        # Audio Codec IC & ALSA Soundcards Card
        self.audio_codec_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.audio_codec_card.grid(row=5, column=0, padx=(0, 8), pady=(15, 0), sticky="nsew")
        ctk.CTkLabel(self.audio_codec_card, text=self._tr("card_audio_codec"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#f472b6").pack(anchor="w", padx=15, pady=(10, 5))

        self.audio_codec_text = ctk.CTkTextbox(self.audio_codec_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.audio_codec_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.audio_codec_text.insert("end", "فحص بطاقة الصوت ALSA ومتحكم الصوت الداخلي (WCD/ALC/MTK) وحالة AudioFlinger...\n" if self.current_lang == "ar" else "Inspects ALSA soundcards, Audio Codec IC chip, and AudioFlinger HAL state...\n")

        # Display panel info & verified boot state card
        self.panel_info_card = ctk.CTkFrame(periph_grid, fg_color="#161b22", corner_radius=8)
        self.panel_info_card.grid(row=5, column=1, padx=(8, 0), pady=(15, 0), sticky="nsew")
        ctk.CTkLabel(self.panel_info_card, text=self._tr("card_panel_info"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#fbbf24").pack(anchor="w", padx=15, pady=(10, 5))

        self.panel_info_text = ctk.CTkTextbox(self.panel_info_card, height=140, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.panel_info_text.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        self.panel_info_text.insert("end", "استخراج اسم شريحة تعريف شاشة العرض (DSI Controller) وحالة حماية البوت لودر (VerifiedBootState)...\n" if self.current_lang == "ar" else "Reads the kernel-reported display controller name and VerifiedBootState / vbmeta state...\n")

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

        self.sec_status_lbl = ctk.CTkLabel(tab, text="جاهز للفحص" if self.current_lang == "ar" else "Scanner Ready", font=ctk.CTkFont(size=12), text_color="#8b949e")
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
        self.bloat_text.insert("end", "انقر فوق 'فحص تطبيقات النظام الزائدة' للتعرف التلقائي على تطبيقات المصنع الدعائية (Xiaomi / Samsung / Oppo) وفق قاعدة UAD-ng...\n" if self.current_lang == "ar" else "Click 'Scan OEM Bloatware' to audit manufacturer pre-installed bloatware packages using UAD-ng database...\n")

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

        self.opt_status_lbl = ctk.CTkLabel(tab, text="جاهز للتنظيف والصيانة" if self.current_lang == "ar" else "Optimizer Idle", font=ctk.CTkFont(size=12), text_color="#8b949e")
        self.opt_status_lbl.pack(anchor="w", padx=15, pady=(0, 15))

        # Before & After Comparison Cards
        comp_grid = ctk.CTkFrame(tab, fg_color="transparent")
        comp_grid.pack(fill="x", padx=15, pady=0)
        comp_grid.grid_columnconfigure((0, 1, 2), weight=1)

        # RAM Freed Card
        self.card_ram_gain = ctk.CTkFrame(comp_grid, fg_color="#161b22", corner_radius=8)
        self.card_ram_gain.grid(row=0, column=0, padx=(0, 6), sticky="nsew")
        ctk.CTkLabel(self.card_ram_gain, text=self._tr("gain_ram"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(padx=10, pady=(10, 2))
        self.lbl_ram_gain = ctk.CTkLabel(self.card_ram_gain, text="+0.0 MB", font=ctk.CTkFont(size=22, weight="bold"), text_color="#58a6ff")
        self.lbl_ram_gain.pack(padx=10, pady=(0, 10))

        # Storage Cleaned Card
        self.card_storage_gain = ctk.CTkFrame(comp_grid, fg_color="#161b22", corner_radius=8)
        self.card_storage_gain.grid(row=0, column=1, padx=6, sticky="nsew")
        ctk.CTkLabel(self.card_storage_gain, text=self._tr("gain_storage"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(padx=10, pady=(10, 2))
        self.lbl_storage_gain = ctk.CTkLabel(self.card_storage_gain, text="+0.0 MB", font=ctk.CTkFont(size=22, weight="bold"), text_color="#238636")
        self.lbl_storage_gain.pack(padx=10, pady=(0, 10))

        # Standby Current Reduced Card
        self.card_drain_gain = ctk.CTkFrame(comp_grid, fg_color="#161b22", corner_radius=8)
        self.card_drain_gain.grid(row=0, column=2, padx=(6, 0), sticky="nsew")
        ctk.CTkLabel(self.card_drain_gain, text=self._tr("gain_drain"), font=ctk.CTkFont(size=12), text_color="#8b949e").pack(padx=10, pady=(10, 2))
        self.lbl_drain_gain = ctk.CTkLabel(self.card_drain_gain, text="-0.0 mA", font=ctk.CTkFont(size=22, weight="bold"), text_color="#f59e0b")
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
        self.lbl_wireless_ip = ctk.CTkLabel(w_row1, text="IP: —", font=ctk.CTkFont(size=12, weight="bold"), text_color="#34d399")
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
        self.wireless_log_text.insert("end", "لتفعيل ADB اللاسلكي: وصل الهاتف بالـ USB أولاً واضغط 'تفعيل وضع ADB اللاسلكي'، ثم يمكنك فصل السلك!\n" if self.current_lang == "ar" else "To enable Wireless ADB: Connect device via USB first, click 'Enable Wireless ADB', then you can unplug the cable!\n")

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
        self.prov_log_text.insert("end", "انقر فوق 'فحص حالة معالج التهيئة' لقراءة إعدادات device_provisioned و user_setup_complete..." if self.current_lang == "ar" else "Click 'Inspect Setup Flags' to verify device_provisioned and user_setup_complete...")

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

        self.img_path_entry = ctk.CTkEntry(flash_bar, placeholder_text="Path to image (.img)...", width=260)
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
            values=[
                "all_photos (جميع الصور والفيديوهات)",
                "full_storage (سحب كل ملفات الهاتف)",
                "whatsapp (ميديا ومحادثات واتساب)",
                "downloads (التنزيلات)",
                "documents (المستندات)"
            ] if self.current_lang == "ar" else [
                "all_photos (All Photos & DCIM)",
                "full_storage (Full Storage Dump)",
                "whatsapp (WhatsApp Media)",
                "downloads (Downloads)",
                "documents (Documents)"
            ],
            width=240
        )
        self.data_cat_combo.pack(side="left", padx=(0, 10))

        ctk.CTkButton(data_bar, text=self._tr("btn_pull_data"), command=self._pull_emergency_data, fg_color="#238636", hover_color="#2ea043").pack(side="left", padx=(0, 8))

        ctk.CTkButton(
            data_bar,
            text="سحب كل الصور فوراً (1-Click)" if self.current_lang == "ar" else "1-Click Full Photos Pull",
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
        self.data_log_text.insert("end", "سحب ملفات وصور الشاشات المكسورة مباشرة إلى سطح المكتب عبر كابل USB...\n" if self.current_lang == "ar" else "Emergency data and photo pull from broken/dark screen devices...\n")

        # ADB Sideload OTA ROM Flasher Card
        sideload_card = ctk.CTkFrame(tab, fg_color="#161b22", corner_radius=8)
        sideload_card.pack(fill="x", padx=15, pady=(0, 15))
        ctk.CTkLabel(sideload_card, text=self._tr("card_sideload"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#38bdf8").pack(anchor="w", padx=15, pady=(10, 5))

        sideload_bar = ctk.CTkFrame(sideload_card, fg_color="transparent")
        sideload_bar.pack(fill="x", padx=15, pady=(0, 10))

        self.sideload_entry = ctk.CTkEntry(sideload_bar, placeholder_text="Path to OTA update package (.zip)...", width=380)
        self.sideload_entry.pack(side="left", padx=(0, 8))

        ctk.CTkButton(sideload_bar, text=self._tr("btn_browse_ota"), width=120, command=self._browse_ota_file, fg_color="#21262d", hover_color="#30363d").pack(side="left", padx=(0, 8))
        ctk.CTkButton(sideload_bar, text=self._tr("btn_flash_sideload"), command=self._flash_sideload, fg_color="#0284c7", hover_color="#0369a1").pack(side="left")

        self.sideload_progress = ctk.CTkProgressBar(sideload_card, height=10, progress_color="#0284c7")
        self.sideload_progress.set(0)
        self.sideload_progress.pack(fill="x", padx=15, pady=(5, 5))

        self.sideload_lbl = ctk.CTkLabel(sideload_card, text="جاهز للتفليش (ادخل الهاتف في وضع Recovery واضغط Apply Update from ADB)" if self.current_lang == "ar" else "Ready (Put device in Recovery Mode > Apply update from ADB)", font=ctk.CTkFont(size=12), text_color="#8b949e")
        self.sideload_lbl.pack(anchor="w", padx=15, pady=(0, 5))

        self.sideload_text = ctk.CTkTextbox(sideload_card, height=100, font=ctk.CTkFont(family="Consolas", size=12), fg_color="#010409", text_color="#c9d1d9")
        self.sideload_text.pack(fill="x", padx=15, pady=(0, 15))
        self.sideload_text.insert("end", "تثبيت تحديثات الروم الرسمي بصيغة ZIP من خلال وضع الريكفري الرسمي عبر بروتوكول ADB Sideload...\n" if self.current_lang == "ar" else "Stream official OTA/ROM update zip packages directly via ADB Sideload protocol...\n")

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
        self.slot_log_text.insert("end", "في وضع الفاست بوت: بدّل بين سلوت A وسلوت B لحل التعليق على الشعار، أو افحص مؤشر حماية الرجوع (ARB)...\n" if self.current_lang == "ar" else "In Fastboot: Switch between Slot A & B to unbrick OTA loops, or check Anti-Rollback fuse index...\n")

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
        self.part_backup_text.insert("end", "سحب احتياطي حقيقي لملفات الشبكة ومعايرة المودم (EFS / FSG / Modem / Persist) وحفظها على الكمبيوتر...\n" if self.current_lang == "ar" else "Dump and backup critical radio calibration partitions (EFS / FSG / Modem / Persist) to PC...\n")

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
        self.apk_log_text.insert("end", "تثبيت حزم APK متعددة دفعة واحدة (-r -d -g) أو سحب تطبيقات المستخدم المثبتة إلى مجلد سطح المكتب...\n" if self.current_lang == "ar" else "Batch install multiple APK files (-r -d -g) or pull installed user apps to PC...\n")

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
        self.cust_name_entry = ctk.CTkEntry(t_grid, placeholder_text="أحمد محمد" if self.current_lang == "ar" else "John Doe")
        self.cust_name_entry.grid(row=1, column=0, padx=6, pady=(0, 8), sticky="ew")

        # Field 2: Customer Phone
        ctk.CTkLabel(t_grid, text=self._tr("lbl_cust_phone"), font=ctk.CTkFont(size=12), text_color="#8b949e").grid(row=0, column=1, padx=6, pady=2, sticky="w")
        self.cust_phone_entry = ctk.CTkEntry(t_grid, placeholder_text="+970 59-xxxxxxx")
        self.cust_phone_entry.grid(row=1, column=1, padx=6, pady=(0, 8), sticky="ew")

        # Field 3: Reported Fault
        ctk.CTkLabel(t_grid, text=self._tr("lbl_cust_fault"), font=ctk.CTkFont(size=12), text_color="#8b949e").grid(row=0, column=2, padx=6, pady=2, sticky="w")
        self.cust_fault_entry = ctk.CTkEntry(t_grid, placeholder_text="شاشة مكسورة / فحص شامل" if self.current_lang == "ar" else "Screen / Charging issue")
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
        self.tech_name_entry.insert(0, "كبير مهندسي الصيانة" if self.current_lang == "ar" else "Chief Hardware Technician")
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
                        text=f"⚠️ هاتف متصل غير مصرح (Unauthorized): {serials_str}" if is_ar else f"⚠️ Device is UNAUTHORIZED: {serials_str}",
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
                    text=f"ADB: غير مصرح ({serials_str}) | افتح شاشة الهاتف واضغط موافق" if is_ar else f"ADB: Unauthorized ({serials_str}) | Accept RSA Prompt",
                    text_color="#ef4444"
                )

            # 2. State: Physical phone detected on Windows USB PnP, but ADB debugging is OFF
            elif diag.get("has_pnp_without_adb") and pnp_phones:
                phone_names = ", ".join(p["name"] for p in pnp_phones)
                if hasattr(self, "alert_card"):
                    self.alert_card.configure(fg_color="#2e1a05", border_color="#f59e0b")
                    self.alert_title_lbl.configure(
                        text=f"⚠️ تم رصد هاتف متصل عبر USB ({phone_names}) ولكن تصحيح USB مغلق!" if is_ar else f"⚠️ Phone detected on USB ({phone_names}), but USB Debugging is OFF!",
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
                    text=f"USB: {phone_names} | فعّل خيارات المطور وتصحيح USB" if is_ar else f"USB: {phone_names} | Enable USB Debugging",
                    text_color="#f59e0b"
                )

            # 3. State: Device is offline
            elif offline_devs and not authorized_devs and not fastboot_devs:
                serials_str = ", ".join(d["serial"] for d in offline_devs)
                if hasattr(self, "alert_card"):
                    self.alert_card.configure(fg_color="#2b1a10", border_color="#ea580c")
                    self.alert_title_lbl.configure(
                        text=f"⚠️ هاتف متصل بوضع غير متصل (Offline): {serials_str}" if is_ar else f"⚠️ Device is OFFLINE: {serials_str}",
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
                    text=f"ADB: غير متصل (Offline) | أعد توصيل كابل USB" if is_ar else f"ADB: Offline | Replug USB cable",
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
                    options = [f"USB: {pnp_phones[0]['name']} (تصحيح USB غير مفعل)" if is_ar else f"USB: {pnp_phones[0]['name']} (No ADB)"]
                else:
                    options = [self._tr("no_devices")]

            self.device_combo.configure(values=options)

            cur = self.current_serial.get()
            ready_serials = [d["serial"] for d in authorized_devs + fastboot_devs]

            if cur not in ready_serials and (authorized_devs or fastboot_devs):
                chosen = (authorized_devs or fastboot_devs)[0]
                self.current_serial.set(chosen["serial"])
                self.device_combo.set(f"{chosen['serial']} ({chosen.get('model') or chosen.get('state')})")
                self.adb_status_label.configure(text=f"ADB: {chosen['state'].upper()} | {chosen['serial']}", text_color="#238636")
                self.run_profile_async()
            elif cur in ready_serials:
                cur_dev = next(d for d in devices if d["serial"] == cur)
                self.adb_status_label.configure(text=f"ADB: {cur_dev['state'].upper()} | {cur}", text_color="#238636")
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
            messagebox.showwarning("تنبيه" if self.current_lang == "ar" else "Warning", "الرجاء توصيل واختيار هاتف مصرح أولاً." if self.current_lang == "ar" else "No target device selected.")
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
                self.spec_labels["cores"].configure(text=f"{profile.cpu_cores} Cores ({profile.cpu_architecture})")
                self.spec_labels["android"].configure(text=f"Android {profile.android_version} (API {profile.sdk_version})")
                self.spec_labels["build_id"].configure(text=profile.build_id[:32])
                self.spec_labels["security"].configure(text=profile.security_patch)
                self.spec_labels["kernel"].configure(text=profile.kernel_version[:36] if profile.kernel_version else "Standard")
                
                root_str = "يوجد روت (معدل)" if self.current_lang == "ar" else "ROOTED"
                no_root_str = "لا يوجد روت (نظام أصلي)" if self.current_lang == "ar" else "NO (Standard OEM)"
                self.spec_labels["root"].configure(text=root_str if profile.is_rooted else no_root_str, text_color="#da3633" if profile.is_rooted else "#238636")
                
                self.spec_labels["selinux"].configure(text=profile.selinux_mode)
                self.spec_labels["display"].configure(text=f"{profile.display_resolution} @ {profile.display_refresh_rate} ({profile.display_density})")

                # Gauges
                if profile.ram_total_mb > 0:
                    pct = profile.ram_used_pct / 100.0
                    self.ram_bar.set(pct)
                    self.ram_title_lbl.configure(text=f"الرام: {profile.ram_free_mb:.0f} MB متاح من {profile.ram_total_mb:.0f} MB ({profile.ram_used_pct}%)" if self.current_lang == "ar" else f"RAM: {profile.ram_free_mb:.0f} MB Free / {profile.ram_total_mb:.0f} MB ({profile.ram_used_pct}%)")

                if profile.storage_total_gb > 0:
                    pct = profile.storage_used_pct / 100.0
                    self.storage_bar.set(pct)
                    self.storage_title_lbl.configure(text=f"الذاكرة: {profile.storage_free_gb:.1f} GB فارغ من {profile.storage_total_gb:.1f} GB ({profile.storage_used_pct}%)" if self.current_lang == "ar" else f"Storage: {profile.storage_free_gb:.1f} GB Free / {profile.storage_total_gb:.1f} GB ({profile.storage_used_pct}%)")

                self.batt_bar.set(profile.battery_level_pct / 100.0)
                self.batt_title_lbl.configure(text=f"البطارية: {profile.battery_level_pct}% ({profile.battery_status})" if self.current_lang == "ar" else f"Battery: {profile.battery_level_pct}% ({profile.battery_status})")
                self.batt_quick_lbl.configure(text=f"الجهد: {profile.battery_voltage_mv} mV | الحرارة: {profile.battery_temp_c} °C | الكفاءة: {profile.battery_health}" if self.current_lang == "ar" else f"Voltage: {profile.battery_voltage_mv} mV | Temp: {profile.battery_temp_c} °C | Health: {profile.battery_health}")

            self.after(0, update_ui)

        threading.Thread(target=task, daemon=True).start()

    def run_pmic_diag_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
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
                f"=== فحص بطارية ومقياس الوقود وآيسي الشحن (الجهاز: {serial}) ===" if is_ar else f"=== BATTERY VOLTAGE (FUEL GAUGE) & CHARGING IC (Device: {serial}) ===",
                f"جهد البطارية (مقياس الوقود):    {v_display}" if is_ar else f"Battery voltage (fuel gauge): {v_display}",
                f"تيار البطارية:                  {c_display}" if is_ar else f"Battery current:              {c_display}",
                f"حرارة البطارية الحالية:         {t_display}" if is_ar else f"Battery temperature:          {t_display}",
                f"حالة الشحن:                     {pmic.status}" if is_ar else f"Charging Status:               {pmic.status}",
                f"صحة البطارية المعلنة بالنظام:  {pmic.health}" if is_ar else f"Reported Health (OS):         {pmic.health}",
                f"صحة الخلية المحسوبة:          {h_display}" if is_ar else f"True Calculated Health:       {h_display}",
                f"السعة التصميمية للخلية:         {f'{pmic.charge_full_design_uah} uAh' if pmic.charge_full_design_uah else 'UNKNOWN'}" if is_ar else f"Design Capacity:               {f'{pmic.charge_full_design_uah} uAh' if pmic.charge_full_design_uah else 'UNKNOWN'}",
                f"عدد دورات الشحن السابقة:        {pmic.cycle_count if pmic.cycle_count is not None else 'UNKNOWN'}" if is_ar else f"Charge Cycle Count:            {pmic.cycle_count if pmic.cycle_count is not None else 'UNKNOWN'}",
            ]
            if pmic.fault_flags:
                out_lines.append("\n⚠️ مؤشرات أعطال محتملة تم رصدها:" if is_ar else "\n⚠️ POTENTIAL FAULT INDICATORS:")
                for f in pmic.fault_flags:
                    out_lines.append(f"  [!] {f}")
            elif pmic.voltage_status == "PASS":
                out_lines.append("\nلا توجد مؤشرات أعطال في القيم التي تمت قراءتها." if is_ar else "\nNo fault indicators in the values that were read.")
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
        self.bootrom_text.insert("end", "جاري فحص منافذ USB PnP للكشف عن أجهزة Qualcomm EDL و MediaTek BROM وريستارت الباور...\n" if is_ar else "Scanning Windows PnP ports for Qualcomm EDL, MediaTek BROM, and PMIC reset loops...\n")

        def task():
            diag = self.board_diag.diagnose_dead_phone_bootrom(check_cycling=True)
            self.last_bootrom_diag = diag

            lines = [
                "=== تقرير فحص الأجهزة الميتة ومنافذ الطوارئ (BootROM) ===" if is_ar else "=== NON-BOOTING DEVICE & BOOTROM USB DIAGNOSIS ===",
                f"التشخيص النهائي: {diag.summary_diagnosis}",
                f"Qualcomm EDL 9008:    {'نعم (تم الرصد)' if diag.is_qualcomm_edl else 'لا'}" if is_ar else f"Qualcomm EDL 9008:    {'YES (Detected)' if diag.is_qualcomm_edl else 'No'}",
                f"MediaTek BROM:        {'نعم (تم الرصد)' if diag.is_mediatek_brom else 'لا'}" if is_ar else f"MediaTek BROM:        {'YES (Detected)' if diag.is_mediatek_brom else 'No'}",
                f"Samsung Odin Mode:    {'نعم (تم الرصد)' if diag.is_samsung_odin else 'لا'}" if is_ar else f"Samsung Odin Mode:    {'YES (Detected)' if diag.is_samsung_odin else 'No'}",
                f"دورة ريستارت الباور:   {'⚠️ ريستارت مستمر (PMIC Cycling)' if diag.is_usb_cycling else 'مستقر (لا يوجد ريستارت)'}" if is_ar else f"USB Disconnect Loop:  {'WARNING (PMIC Cycling)' if diag.is_usb_cycling else 'Stable (No Cycling)'}",
                ""
            ]
            if diag.detected_ports:
                lines.append("المنافذ المكتشفة:" if is_ar else "Detected USB Interfaces:")
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
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return

        is_ar = self.current_lang == "ar"
        self.leakage_progress.set(0)
        self.leakage_text.delete("1.0", "end")
        self.leakage_text.insert("end", "جاري بدء اختبار تيار البطارية وفحص تسريب اللوحة الأم...\nسيتم إطفاء الشاشة مؤقتاً لقياس تيار البطارية (يشمل تيار الشحن في حال التوصيل عبر USB)...\n" if is_ar else "Starting Motherboard Battery Current Leakage Test...\nTurning screen off to measure battery current (includes charging current while USB is connected)...\n")

        def progress(pct, val, msg):
            def upd():
                self.leakage_progress.set(pct / 100.0)
                ar_msg = f"عينة {int(pct/10)}/10: تيار البطارية = {val} mA" if is_ar else f"Sample {int(pct/10)}/10: Battery current = {val} mA"
                self.leakage_lbl.configure(text=ar_msg)
                self.leakage_text.insert("end", f"{ar_msg}\n")
                self.leakage_text.see("end")
            self.after(0, upd)

        def task():
            is_faulty, avg_leak, max_leak, samples, verdict = self.board_diag.run_leakage_test(
                serial=serial,
                sample_count=10,
                sample_interval=3.0,
                progress_callback=progress
            )
            def done():
                res_v = f"\n{verdict}\n"
                self.leakage_text.insert("end", res_v)
                self.leakage_text.see("end")
                if is_faulty:
                    messagebox.showerror("تنبيه تيار البطارية / شورت اللوحة" if is_ar else "Battery Current / Leakage Alert", verdict)
                else:
                    messagebox.showinfo("نتيجة فحص التيار" if is_ar else "Battery Current Test Result", verdict)
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
            out = [f"=== خريطة الحرارة ({len(zones)} منطقة) [{thermal.status}] ===" if is_ar else f"=== THERMAL ZONES ({len(zones)} zones) [{thermal.status}] ==="]
            for z in zones:
                status = ("مرتفعة (>45°C) ⚠️" if is_ar else "HOT (>45°C) ⚠️") if z.is_overheated else ("أقل من 45°C" if is_ar else "below 45°C")
                out.append(f"منطقة {z.zone_id:02d} [{z.zone_type}]: {z.temp_c:.1f}°C — {status}")
            
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
                "=== فحص سجلات انهيار النظام والذاكرة ===" if is_ar else "=== STORAGE & KERNEL PANIC AUDIT ===",
                f"[{panics.status}] {panics.summary_verdict}",
                f"dmesg: {panics.dmesg_status} | pstore: {panics.blackbox.kernel_ramoops_status} | boot reason: {panics.blackbox.boot_reason}",
                f"أخطاء قراءة وكتابة ذاكرة UFS/eMMC: {len(panics.emmc_ufs_errors)}",
                f"إعادة تشغيل الحماية (Watchdog):     {len(panics.watchdog_resets)}",
                f"انهيارات الكيرنل (Kernel Panic):     {len(panics.kernel_panics)}",
                f"إطفاء الطوارئ بسبب الحرارة:          {len(panics.thermal_shutdowns)}",
                ""
            ]
            if panics.emmc_ufs_errors:
                out.append("أخطاء الذاكرة التخزينية:" if is_ar else "eMMC / UFS I/O Error Traces:")
                for err in panics.emmc_ufs_errors:
                    out.append(f"  {err}")
            if panics.kernel_panics:
                out.append("\nسجلات الكيرنل:" if is_ar else "\nKernel Panic Traces:")
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
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return

        is_ar = self.current_lang == "ar"
        self.blackbox_text.delete("1.0", "end")
        self.blackbox_text.insert("end", "جاري تحليل الصندوق الأسود (DropBox / Boot Reason / Watchdog / Kernel Panics)...\n" if is_ar else "Analyzing Blackbox Forensics (DropBox / Boot Reason / Watchdog / Kernel Panics)...\n")

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
                "=== تحليل الصندوق الأسود وأسباب الانطفاء المفاجئ ===" if is_ar else "=== BLACKBOX FORENSICS & REBOOT ANALYSIS ===",
                f"تصنيف الانهيار:        {cat}",
                f"سبب الإقلاع الأخير:    {reason}",
                f"تشخيص الصندوق الأسود: {diag}",
                f"سجل الكيرنل (Ramoops): {ramoops}",
                ""
            ]

            if history:
                lines.append("سجل أسباب الإقلاع السابقة:" if is_ar else "Boot Reason History:")
                for h in history[:5]:
                    lines.append(f"  • {h}")
                lines.append("")

            if events:
                lines.append(f"أحداث DropBox الحرجة المسجلة ({len(events)} حدث):" if is_ar else f"DropBox Critical Crash Events ({len(events)} logged):")
                for ev in events:
                    lines.append(f"  • {ev}")
            else:
                lines.append("لا توجد إدخالات DropBox ذات صلة في القائمة المقروءة." if is_ar else "No relevant DropBox entries in the list that was read.")
            for cause in result.possible_causes:
                lines.append(f"  - {'سبب محتمل' if is_ar else 'Possible cause'}: {cause}")
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
            self.btn_toggle_telemetry.configure(text="المراقبة اللحظية: معطلة" if is_ar else "Live Telemetry: Off", fg_color="#21262d")
        else:
            serial = self.current_serial.get()
            if not serial:
                messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
                return
            self._telemetry_running = True
            self.btn_toggle_telemetry.configure(text="المراقبة اللحظية: نشطة (1.5s)" if is_ar else "Live Telemetry: Active (1.5s)", fg_color="#059669")
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
            s_lines = [f"=== المستشعرات المسجلة (حسب نظام Sensor HAL) ({len(report.sensors)} مستشعر) ===" if is_ar else f"=== REPORTED SENSORS ({len(report.sensors)} Registered) ==="]
            for s in report.sensors:
                power_txt = f" | {s.power_ma} mA" if s.power_ma is not None else ""
                s_lines.append(f"• {s.name} ({s.vendor}) | {s.sensor_type}{power_txt}")
            if not report.sensors:
                s_lines.append(f"[{report.sensors_status}]")
            if report.missing_critical_sensors:
                s_lines.append(("\nحساسات شائعة غير مسجلة في هذا الجهاز (ليس عطلاً بالضرورة): " if is_ar else "\nCommon sensors not reported (not necessarily a fault): ") + ", ".join(report.missing_critical_sensors))

            # Cameras & Audio
            c_lines = [f"=== كاميرات الهاتف ({len(report.cameras)} عدسة) ===" if is_ar else f"=== CAMERA MODULES ({len(report.cameras)} Detected) ==="]
            for c in report.cameras:
                flash_txt = "UNKNOWN" if c.flash_available is None else (("متوفر" if is_ar else "yes") if c.flash_available else ("غير متوفر" if is_ar else "no"))
                c_lines.append(f"• Camera {c.camera_id}: {c.facing} (flash: {flash_txt})")
            if not report.cameras:
                c_lines.append(f"[{report.cameras_status}]")

            c_lines.append("\n=== نظام الصوت ومكبر السماعة ===" if is_ar else "\n=== AUDIO SUBSYSTEM ===")
            c_lines.append(f"الوضع: {report.audio.mode} | الرنين: {report.audio.ringer_mode}")

            # Fingerprint
            fp_lines = [
                f"الحالة المعتمدة: [{report.fingerprint.status}]" if is_ar else f"Verified Status: [{report.fingerprint.status}]",
                f"الشركة المصنعة لآيسي البصمة: {report.fingerprint.vendor}",
                f"التشخيص الفني: {report.fingerprint.diagnosis}"
            ]
            for cause in report.fingerprint.possible_causes:
                fp_lines.append(f"  - {'سبب محتمل' if is_ar else 'Possible cause'}: {cause}")
            if report.fingerprint.evidence:
                fp_lines.append(format_evidence_for_display(report.fingerprint.evidence, is_ar=is_ar))

            # Physical Keys
            key_lines = [
                f"الحالة المعتمدة: [{report.buttons.status}]" if is_ar else f"Verified Status: [{report.buttons.status}]",
                f"زر خفض الصوت (Vol-): {report.buttons.vol_down_status}",
                f"زر رفع الصوت (Vol+): {report.buttons.vol_up_status}",
                f"زر التشغيل (Power):  {report.buttons.power_status}",
                f"التشخيص: {report.buttons.diagnosis}"
            ]
            if report.buttons.evidence:
                key_lines.append(format_evidence_for_display(report.buttons.evidence, is_ar=is_ar))

            # SIM & Baseband
            sim_lines = [
                f"الحالة المعتمدة: [{report.sim_baseband.status}]" if is_ar else f"Verified Status: [{report.sim_baseband.status}]",
                f"حالة الشريحة الأولى (SIM 1): {report.sim_baseband.sim1_state}" if is_ar else f"SIM 1 State: {report.sim_baseband.sim1_state}",
                f"حالة الشريحة الثانية (SIM 2): {report.sim_baseband.sim2_state}" if is_ar else f"SIM 2 State: {report.sim_baseband.sim2_state}",
                f"شركة الاتصال (Operator): {report.sim_baseband.carrier_name}" if is_ar else f"Carrier Operator: {report.sim_baseband.carrier_name}",
                f"نوع شبكة البيانات (Network): {report.sim_baseband.network_type}" if is_ar else f"Network Type: {report.sim_baseband.network_type}",
                f"قوة الإشارة (Signal): {report.sim_baseband.signal_strength}" if is_ar else f"Signal Strength: {report.sim_baseband.signal_strength}",
                f"إصدار معالج المودم (Baseband): {report.sim_baseband.baseband_version}" if is_ar else f"Baseband Version: {report.sim_baseband.baseband_version}",
                f"التشخيص: {report.sim_baseband.diagnosis}" if is_ar else f"Diagnosis: {report.sim_baseband.diagnosis}"
            ]
            for cause in report.sim_baseband.possible_causes:
                sim_lines.append(f"  - {'سبب محتمل' if is_ar else 'Possible cause'}: {cause}")
            if report.sim_baseband.evidence:
                sim_lines.append(format_evidence_for_display(report.sim_baseband.evidence, is_ar=is_ar))

            # Display Backlight
            disp_lines = [
                f"الحالة المعتمدة: [{report.display_backlight.status}]" if is_ar else f"Verified Status: [{report.display_backlight.status}]",
                f"مستوى إضاءة الشاشة الخلفية: {report.display_backlight.brightness_now} / {report.display_backlight.max_brightness}" if is_ar else f"Backlight Level: {report.display_backlight.brightness_now} / {report.display_backlight.max_brightness}",
                f"التشخيص: {report.display_backlight.diagnosis}" if is_ar else f"Diagnosis: {report.display_backlight.diagnosis}"
            ]
            for cause in report.display_backlight.possible_causes:
                disp_lines.append(f"  - {'سبب محتمل' if is_ar else 'Possible cause'}: {cause}")
            if report.display_backlight.evidence:
                disp_lines.append(format_evidence_for_display(report.display_backlight.evidence, is_ar=is_ar))

            # Wi-Fi Chipset
            wifi_lines = [
                f"الحالة المعتمدة: [{report.wifi.status}]" if is_ar else f"Verified Status: [{report.wifi.status}]",
                f"حالة الواي فاي (Wi-Fi State): {report.wifi.state}" if is_ar else f"Wi-Fi State: {report.wifi.state}",
                f"عنوان الماك (MAC Address): {report.wifi.mac_address}" if is_ar else f"MAC Address: {report.wifi.mac_address}",
                f"الشبكة المتصل بها (SSID): {report.wifi.ssid or ('غير متصل' if is_ar else 'Disconnected')}",
                f"قوة الإشارة (RSSI): {report.wifi.rssi_dbm} dBm" if report.wifi.rssi_dbm else ("قوة الإشارة: —" if is_ar else "Signal: —"),
                f"سرعة النقل والتردد: {report.wifi.link_speed_mbps} Mbps | {report.wifi.frequency_mhz} MHz" if report.wifi.link_speed_mbps else ("السرعة والتردد: —" if is_ar else "Speed & Freq: —"),
                f"حالة التعريف والرقاقة: {report.wifi.driver_status}" if is_ar else f"Driver Status: {report.wifi.driver_status}",
                f"التشخيص: {report.wifi.diagnosis}" if is_ar else f"Diagnosis: {report.wifi.diagnosis}"
            ]
            if report.wifi.evidence:
                wifi_lines.append(format_evidence_for_display(report.wifi.evidence, is_ar=is_ar))

            # Bluetooth Controller
            bt_lines = [
                f"الحالة المعتمدة: [{report.bluetooth.status}]" if is_ar else f"Verified Status: [{report.bluetooth.status}]",
                f"حالة البلوتوث (Bluetooth): {report.bluetooth.state}" if is_ar else f"Bluetooth State: {report.bluetooth.state}",
                f"عنوان المحول (MAC Address): {report.bluetooth.mac_address}" if is_ar else f"MAC Address: {report.bluetooth.mac_address}",
                f"اسم الجهاز الظاهر: {report.bluetooth.name}" if is_ar else f"Device Name: {report.bluetooth.name}",
                f"BLE: {'UNKNOWN' if report.bluetooth.ble_supported is None else report.bluetooth.ble_supported}",
                f"التشخيص: {report.bluetooth.diagnosis}" if is_ar else f"Diagnosis: {report.bluetooth.diagnosis}"
            ]
            for cause in report.bluetooth.possible_causes:
                bt_lines.append(f"  - {'سبب محتمل' if is_ar else 'Possible cause'}: {cause}")
            if report.bluetooth.evidence:
                bt_lines.append(format_evidence_for_display(report.bluetooth.evidence, is_ar=is_ar))

            # Peripheral Chips (GPS, NFC, OTG)
            chip_lines = [
                f"الحالة المعتمدة: [{report.peripherals.status}]" if is_ar else f"Verified Status: [{report.peripherals.status}]",
                f"GPS: {report.peripherals.gps_status}",
                f"NFC: {report.peripherals.nfc_status}",
                f"USB host / OTG: {'UNKNOWN' if report.peripherals.usb_otg_supported is None else ('Declared' if report.peripherals.usb_otg_supported else 'Not declared')}",
                f"التشخيص: {report.peripherals.diagnosis}" if is_ar else f"Diagnosis: {report.peripherals.diagnosis}"
            ]
            if report.peripherals.evidence:
                chip_lines.append(format_evidence_for_display(report.peripherals.evidence, is_ar=is_ar))

            # Audio Codec IC (ALSA Soundcards)
            codec_lines = [
                f"الحالة المعتمدة: [{report.audio_codec.status}]" if is_ar else f"Verified Status: [{report.audio_codec.status}]",
                f"شريحة معالج الصوت (Codec IC): {report.audio_codec.codec_chip_name}" if is_ar else f"Audio Codec IC: {report.audio_codec.codec_chip_name}",
                f"بطاقات الصوت ALSA المكتشفة ({len(report.audio_codec.cards_detected)}): {', '.join(report.audio_codec.cards_detected) if report.audio_codec.cards_detected else ('لا توجد بطاقات!' if is_ar else 'None')}",
                f"قنوات الصوت المسجلة (PCM): {len(report.audio_codec.pcm_devices)} قناة" if is_ar else f"ALSA PCM Channels: {len(report.audio_codec.pcm_devices)}",
                f"AudioFlinger: {'UNKNOWN' if report.audio_codec.audio_flinger_alive is None else ('running' if report.audio_codec.audio_flinger_alive else 'NOT running')} | output threads: {report.audio_codec.output_threads}",
                f"التشخيص: {report.audio_codec.diagnosis}" if is_ar else f"Diagnosis: {report.audio_codec.diagnosis}"
            ]
            for cause in report.audio_codec.possible_causes:
                codec_lines.append(f"  - {'سبب محتمل' if is_ar else 'Possible cause'}: {cause}")
            if report.audio_codec.evidence:
                codec_lines.append(format_evidence_for_display(report.audio_codec.evidence, is_ar=is_ar))

            # Display Controller & Panel Info
            panel_lines = [
                f"الحالة المعتمدة: [{report.display_panel_info.status}]" if is_ar else f"Verified Status: [{report.display_panel_info.status}]",
                f"شريحة تعريف الشاشة (DSI Controller): {report.display_panel_info.panel_controller_name}" if is_ar else f"DSI Panel Controller: {report.display_panel_info.panel_controller_name}",
                f"مصدر التعريف (Source): {report.display_panel_info.driver_source}" if is_ar else f"Driver Source: {report.display_panel_info.driver_source}",
                f"حالة حماية الإقلاع (VerifiedBootState): {report.display_panel_info.verified_boot_state}" if is_ar else f"Verified Boot State: {report.display_panel_info.verified_boot_state}",
                f"حالة أمان VBMeta: {report.display_panel_info.vbmeta_device_state}" if is_ar else f"VBMeta Device State: {report.display_panel_info.vbmeta_device_state}",
                f"التشخيص: {report.display_panel_info.diagnosis}" if is_ar else f"Diagnosis: {report.display_panel_info.diagnosis}"
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
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return

        is_ar = self.current_lang == "ar"
        self.keys_text.delete("1.0", "end")
        self.keys_text.insert("end", "جاري فحص الأزرار الحية... اضغط الآن على زر الصوت أو الباور على الهاتف...\n" if is_ar else "Listening for physical key presses... Press Vol+, Vol-, or Power now...\n")

        def task():
            btn_diag = self.hardware_tester.diagnose_physical_buttons(serial)
            lines = [
                "=== نتيجة فحص أزرار الصوت والباور ===" if is_ar else "=== PHYSICAL BUTTONS TEST RESULT ===",
                f"زر خفض الصوت: {btn_diag.vol_down_status}",
                f"زر رفع الصوت: {btn_diag.vol_up_status}",
                f"زر الباور:     {btn_diag.power_status}",
                f"التشخيص:      {btn_diag.diagnosis}",
                ""
            ]
            if btn_diag.captured_events:
                lines.append("الأحداث الملتقطة:" if is_ar else "Captured Input Events:")
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
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return

        self.sec_progress.set(0)
        self.threats_text.delete("1.0", "end")
        is_ar = self.current_lang == "ar"

        def progress(pct, msg):
            def upd():
                self.sec_progress.set(pct / 100.0)
                self.sec_status_lbl.configure(text=msg)
            self.after(0, upd)

        def task():
            scan = self.malware_hunter.audit_security(serial, progress_callback=progress)
            self.last_sec_scan = scan

            t_lines = [
                f"=== فحص الحماية وصائد التجسس (مستوى الخطر: {scan.overall_risk}) ===" if is_ar else f"=== MALWARE & SECURITY AUDIT (Risk: {scan.overall_risk}) ===",
                f"إجمالي التطبيقات: {scan.total_packages_scanned} | تطبيقات المستخدم: {scan.third_party_count} | تطبيقات النظام: {scan.system_count}",
                f"تهديدات حرجة: {len(scan.critical_threats)} | تهديدات متوسطة: {len(scan.high_threats)} | تطبيقات شبحية (بدون أيقونة): {len(scan.ghost_apps)}",
                ""
            ]

            all_threats = scan.critical_threats + scan.high_threats + scan.ghost_apps
            seen = set()
            for t_item in all_threats:
                if t_item.package_name in seen:
                    continue
                seen.add(t_item.package_name)
                t_lines.append(f"[{t_item.threat_level}] {t_item.package_name} (درجة الخطر: {t_item.threat_score})")
                for r in t_item.flagged_reasons:
                    t_lines.append(f"   • {r}")
                if t_item.dangerous_permissions:
                    t_lines.append(f"   الأذونات الخطرة: {', '.join(t_item.dangerous_permissions[:3])}")
                t_lines.append("")

            if not all_threats:
                t_lines.append("✓ الهاتف نظيف: لم يتم العثور على تطبيقات تجسس أو تطبيقات شبحية مخفية." if is_ar else "✓ Clean: No active spyware, ghost apps, or RAT accessibility backdoors detected.")

            if scan.has_mitm_risk:
                t_lines.append("\n⚠️ تحذير أمني حرج: تم رصد شهادات CA للمستخدم (خطر اعتراض التشفير والتجسس MITM)!" if is_ar else "\n⚠️ CRITICAL WARNING: User-Installed Root CA Certificates Detected (MITM Traffic Interception Risk)!")
                for cert in scan.user_mitm_certificates:
                    t_lines.append(f"   • {cert}")
                t_lines.append("")

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
            lines = [f"=== الاتصالات الشبكية النشطة للخارج ({len(conns)} اتصال) ===" if is_ar else f"=== ACTIVE NETWORK CONNECTIONS ({len(conns)} Sockets) ==="]
            for c in conns:
                susp = "⚠️ [منفذ غير مألوف/مشبوه]" if c.is_suspicious else ""
                lines.append(f"{c.protocol:4s} {c.local_address}:{c.local_port} -> {c.remote_address}:{c.remote_port} [{c.state}] | التطبيق: {c.package_name} {susp}")
            if not conns:
                lines.append("لا توجد اتصالات مريبة نشطة في الخلفية.")

            def upd():
                self.net_text.delete("1.0", "end")
                self.net_text.insert("end", "\n".join(lines))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def _neutralize_selected(self, action: str):
        serial = self.current_serial.get()
        pkg = self.neutral_pkg_entry.get().strip()
        if not serial or not pkg:
            messagebox.showwarning("تنبيه", "أدخل اسم حزمة التطبيق أولاً.")
            return

        def task():
            success, msg = self.malware_hunter.neutralize_app(serial, pkg, action=action)
            def upd():
                if success:
                    messagebox.showinfo("تم الإجراء بنجاح", msg)
                else:
                    messagebox.showerror("خطأ", msg)
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def _show_rollback_dialog(self):
        serial = self.current_serial.get()
        history = self.db.get_rollback_history(serial)

        dialog = ctk.CTkToplevel(self)
        dialog.title("سجل استرجاع التطبيقات — Rollback")
        dialog.geometry("700x480")

        ctk.CTkLabel(dialog, text="التطبيقات المحذوفة والمعطلة القابلة للاسترجاع", font=ctk.CTkFont(size=14, weight="bold"), text_color="#58a6ff").pack(padx=20, pady=(15, 5))

        txt = ctk.CTkTextbox(dialog, height=300, font=ctk.CTkFont(family="Consolas", size=12))
        txt.pack(fill="both", expand=True, padx=20, pady=10)

        for h in history:
            status = f"تم الاسترجاع في {h['restored_at']}" if h.get("restored_at") else "متاح للاسترجاع الفوري"
            txt.insert("end", f"#{h['id']} [{h['action_type'].upper()}] {h['package_name']} | الوقت: {h['timestamp']} | الحالة: {status}\n")

        restore_bar = ctk.CTkFrame(dialog, fg_color="transparent")
        restore_bar.pack(fill="x", padx=20, pady=15)

        entry = ctk.CTkEntry(restore_bar, placeholder_text="اسم الحزمة المراد استرجاعها...", width=260)
        entry.pack(side="left", padx=(0, 10))

        def restore_now():
            target_pkg = entry.get().strip()
            if not target_pkg:
                return
            def t_task():
                success, msg = self.malware_hunter.rollback_package(serial, target_pkg)
                self.after(0, lambda: messagebox.showinfo("الاسترجاع", msg))
            threading.Thread(target=t_task, daemon=True).start()

        ctk.CTkButton(restore_bar, text="استرجاع التطبيق الآن", command=restore_now, fg_color="#238636").pack(side="left")

    def run_oem_bloat_scan_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return

        is_ar = self.current_lang == "ar"
        self.bloat_text.delete("1.0", "end")
        self.bloat_text.insert("end", "جاري فحص تطبيقات المصنع الدعائية المثبتة وفق قاعدة بيانات UAD-ng...\n" if is_ar else "Scanning installed OEM bloatware packages matching UAD-ng database...\n")

        def task():
            bloat_list = self.malware_hunter.scan_oem_bloatware(serial)
            self.last_bloat_scan = bloat_list

            lines = [
                f"=== فحص تطبيقات المصنع الزائدة UAD-ng ({len(bloat_list)} تطبيق تم رصده) ===" if is_ar else f"=== UAD-ng OEM BLOATWARE SCAN ({len(bloat_list)} Packages Found) ===",
                ""
            ]

            if bloat_list:
                for b in bloat_list:
                    lines.append(f"• [{b.get('oem')}] {b.get('package_name')} ({b.get('category')})")
                    lines.append(f"   الوصف: {b.get('description')}")
                    lines.append(f"   مستوى الأمان: {b.get('removal_safety')}")
                    lines.append("")
                lines.append("لإلغاء تثبيت هذه التطبيقات بأمان وبدون روت، اضغط على زر 'حذف التطبيقات المحددة بأمان'.")
            else:
                lines.append("✓ ممتاز: لم يتم العثور على أي تطبيقات نظام زائدة من قائمة UAD-ng على هذا الهاتف." if is_ar else "✓ Clean: No active OEM bloatware packages from UAD-ng database found on this device.")

            def upd():
                self.bloat_text.delete("1.0", "end")
                self.bloat_text.insert("end", "\n".join(lines))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_safe_debloat_all_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return

        is_ar = self.current_lang == "ar"
        if not self.last_bloat_scan:
            bloat_list = self.malware_hunter.scan_oem_bloatware(serial)
            self.last_bloat_scan = bloat_list

        if not self.last_bloat_scan:
            messagebox.showinfo("نظيف", "لم يتم العثور على أي تطبيقات زائدة لحذفها." if is_ar else "No bloatware packages found to remove.")
            return

        confirm = messagebox.askyesno(
            "تأكيد الحذف الآمن" if is_ar else "Confirm Safe Debloat",
            f"سيتم تعطيل {len(self.last_bloat_scan)} تطبيق دعائي للمستخدم الحالي (User 0) بأمان.\nيمكن التراجع واستعادة التطبيقات في أي وقت بدون روت.\n\nهل تريد المتابعة؟"
            if is_ar else
            f"Safe Debloat will disable {len(self.last_bloat_scan)} OEM bloatware packages for User 0.\nApps can be restored at any time.\n\nProceed?"
        )
        if not confirm:
            return

        self.bloat_text.delete("1.0", "end")
        self.bloat_text.insert("end", "جاري حذف تطبيقات المصنع بأمان...\n" if is_ar else "Safely debloating OEM packages...\n")

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
                res_txt = f"\nتم تعطيل {success_count} من أصل {len(self.last_bloat_scan)} تطبيق بنجاح!" if is_ar else f"\nSuccessfully debloated {success_count}/{len(self.last_bloat_scan)} packages!"
                self.bloat_text.insert("end", res_txt)
                messagebox.showinfo("اكتمل التنظيف" if is_ar else "Debloat Complete", res_txt)
            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def run_restore_all_debloated_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return

        is_ar = self.current_lang == "ar"
        self.bloat_text.delete("1.0", "end")
        self.bloat_text.insert("end", "جاري استرجاع التطبيقات المحذوفة وإعادة تفعيلها...\n" if is_ar else "Restoring debloated packages...\n")

        def task():
            history = self.db.get_rollback_history(serial)
            if not history:
                def none_found():
                    self.bloat_text.insert("end", "لا توجد سجلات تطبيقات محذوفة لاسترجاعها.\n" if is_ar else "No neutralized packages found in rollback history.\n")
                    messagebox.showinfo("تنبيه", "لا توجد تطبيقات محذوفة لاسترجاعها." if is_ar else "No debloated packages to restore.")
                self.after(0, none_found)
                return

            restored = 0
            for item in history:
                pkg = item.get("package_name")
                ok, msg = self.malware_hunter.rollback_package(serial, pkg)
                if ok:
                    restored += 1
                def log_one(p=pkg, s=ok):
                    self.bloat_text.insert("end", f"{'✓ تم الاسترجاع' if s else '✗ فشل'}: {p}\n")
                    self.bloat_text.see("end")
                self.after(0, log_one)
                time.sleep(0.05)

            def done():
                res_txt = f"\nتم استرجاع {restored} تطبيق بنجاح!" if is_ar else f"\nSuccessfully restored {restored} packages!"
                self.bloat_text.insert("end", res_txt)
                messagebox.showinfo("اكتملت الاستعادة" if is_ar else "Restore Complete", res_txt)
            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def run_optimizer_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return

        self.opt_progress.set(0)
        self.opt_log_text.delete("1.0", "end")

        def progress(pct, msg):
            def upd():
                self.opt_progress.set(pct / 100.0)
                self.opt_status_lbl.configure(text=msg)
                self.opt_log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {msg}\n")
                self.opt_log_text.see("end")
            self.after(0, upd)

        def task():
            res = self.optimizer.run_optimization(serial, progress_callback=progress)
            self.last_opt_result = res

            def done():
                self.lbl_ram_gain.configure(text=f"+{res.ram_freed_mb} MB")
                self.lbl_storage_gain.configure(text=f"+{res.storage_freed_mb} MB")
                self.lbl_drain_gain.configure(text=f"-{res.drain_reduction_ma} mA")
                messagebox.showinfo("اكتملت الصيانة", f"تم تحرير {res.ram_freed_mb}MB من الرام، وتنظيف {res.storage_freed_mb}MB من التخزين!")
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
            self.wireless_log_text.insert("end", "تم تشغيل وضع محاكاة الماوس والكيبورد (USB HID OTG) بنجاح!\n" if is_ar else "OTG USB HID Mouse/Keyboard Mode launched successfully!\n")
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
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف متصل بـ USB أولاً.")
            return
        is_ar = self.current_lang == "ar"
        self.wireless_log_text.delete("1.0", "end")
        self.wireless_log_text.insert("end", "جاري تحويل اتصال ADB إلى وضع TCP/IP واستخراج عنوان الـ IP...\n" if is_ar else "Switching ADB to TCP/IP mode and discovering device IP...\n")

        def task():
            ok, msg = self.bridge.enable_wireless_adb(serial, 5555)
            ip = self.bridge.get_device_ip_address(serial)
            def done():
                self.wireless_log_text.insert("end", f"{msg}\n")
                if ip:
                    ip_port = f"{ip}:5555"
                    self.lbl_wireless_ip.configure(text=f"IP: {ip_port}")
                    self.entry_wireless_addr.delete(0, "end")
                    self.entry_wireless_addr.insert(0, ip_port)
                    info_msg = (
                        f"تم تفعيل وضع ADB اللاسلكي بنجاح على العنوان:\n{ip_port}\n\nيمكنك الآن فصل كابل الـ USB واستخدام البرنامج لاسلكياً بالكامل!"
                        if is_ar else
                        f"Wireless ADB enabled on:\n{ip_port}\n\nYou can now unplug the USB cable!"
                    )
                    messagebox.showinfo("ADB اللاسلكي مفعل" if is_ar else "Wireless ADB Ready", info_msg)
                else:
                    messagebox.showwarning("تنبيه", "تم تفعيل المنفذ 5555 ولكن تعذر قراءة عنوان الـ IP تلقائياً. تأكد من اتصال الهاتف بنفس شبكة الواي فاي.")
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _connect_wireless_adb(self):
        addr = self.entry_wireless_addr.get().strip()
        if not addr:
            messagebox.showwarning("تنبيه", "أدخل عنوان IP والمنفذ (مثال: 192.168.1.50:5555)")
            return
        is_ar = self.current_lang == "ar"
        def task():
            ok, msg = self.bridge.connect_wireless_adb(addr)
            def done():
                self.wireless_log_text.insert("end", f"الاتصال اللاسلكي ({addr}): {msg}\n")
                self.wireless_log_text.see("end")
                if ok:
                    messagebox.showinfo("تم الاتصال", f"تم الاتصال بالجهاز اللاسلكي:\n{addr}")
                else:
                    messagebox.showerror("فشل الاتصال", msg)
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _disconnect_wireless_adb(self):
        addr = self.entry_wireless_addr.get().strip()
        def task():
            ok, msg = self.bridge.disconnect_wireless_adb(addr)
            def done():
                self.wireless_log_text.insert("end", f"فصل الاتصال اللاسلكي: {msg}\n")
                self.wireless_log_text.see("end")
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _pair_wireless_adb(self):
        addr = self.entry_pair_addr.get().strip()
        code = self.entry_pair_code.get().strip()
        if not addr or not code:
            messagebox.showwarning("تنبيه", "أدخل عنوان IP الاقتران والمنفذ ورمز الاقتران المكون من 6 أرقام من خيارات المطور.")
            return
        def task():
            ok, msg = self.bridge.pair_wireless_adb(addr, code)
            def done():
                self.wireless_log_text.insert("end", f"اقتران أندرويد 11+ ({addr}): {msg}\n")
                self.wireless_log_text.see("end")
                if ok:
                    messagebox.showinfo("نجاح الاقتران", "تم اقتران الجهاز لاسلكياً بنجاح! يمكنك الآن الاتصال به عبر زر الاتصال.")
                else:
                    messagebox.showerror("فشل الاقتران", msg)
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _check_provisioning(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return
        def task():
            p_status = self.provisioning.get_provisioning_status(serial)
            is_ar = self.current_lang == "ar"
            lines = [
                "=== حالة معالج التهيئة الأولي (Provisioning Status) ===" if is_ar else "=== PROVISIONING STATUS ===",
                f"حالة تهيئة الجهاز (device_provisioned):    {p_status.raw_provisioned_val} ({'نعم مكتمل' if p_status.is_device_provisioned else 'غير مكتمل'})",
                f"اكتمال إعداد المستخدم (user_setup_complete): {p_status.raw_setup_complete_val} ({'نعم مكتمل' if p_status.is_user_setup_complete else 'غير مكتمل'})",
                f"التقييم: {p_status.status_summary}"
            ]
            def upd():
                self.prov_log_text.delete("1.0", "end")
                self.prov_log_text.insert("end", "\n".join(lines))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _fix_provisioning(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return
        def task():
            ok, msg = self.provisioning.complete_setup_wizard_flags(serial)
            def upd():
                self.prov_log_text.delete("1.0", "end")
                self.prov_log_text.insert("end", f"إصلاح معالج التهيئة:\n{msg}\n")
                if ok:
                    messagebox.showinfo("تم التخطي", "تم تعيين أعلام اكتمال الإعداد وإطلاق واجهة الشاشة الرئيسية بنجاح.")
                else:
                    messagebox.showerror("خطأ", msg)
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
                "=== معلومات وبيانات وضع الفاست بوت ===" if is_ar else "=== FASTBOOT DEVICE VARIABLES ===",
                f"نوع الجهاز:           {fb.product}",
                f"إصدار البوت لودر:     {fb.bootloader_version}",
                f"إصدار المودم:         {fb.baseband_version}",
                f"حماية الإقلاع (Secure): {'مفعلة' if fb.is_secure else 'معطلة'}",
                f"حالة فتح البوت لودر:  {'مفتوح (Unlocked)' if fb.is_unlocked else 'مغلق (Locked)'}",
                f"السلوت الحالي (Slot): {fb.current_slot}",
                f"فولتية البطارية:       {fb.battery_voltage}",
                f"معمارية المعالج:       {fb.cpu_abi}",
                "\nجميع المتغيرات الخام:"
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
        def task():
            ok, msg = self.rescue_center.fix_cache_bootloop(serial)
            def upd():
                self.rescue_log_text.delete("1.0", "end")
                self.rescue_log_text.insert("end", f"إصلاح الكاش والتعليق:\n{msg}\n")
                if ok:
                    messagebox.showinfo("تم الإصلاح", "تم مسح وتهيئة قسم الكاش بنجاح.")
                else:
                    messagebox.showerror("خطأ", msg)
            self.after(0, upd)
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
            messagebox.showwarning("تنبيه", "اختر ملف .img صالح للتفليش.")
            return

        def task():
            ok, msg = self.rescue_center.flash_partition(part, path, serial)
            def upd():
                self.rescue_log_text.delete("1.0", "end")
                self.rescue_log_text.insert("end", f"تفليش {part}:\n{msg}\n")
                if ok:
                    messagebox.showinfo("نجاح التفليش", f"تم تفليش القسم {part} بنجاح.")
                else:
                    messagebox.showerror("فشل التفليش", msg)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _pull_emergency_data(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار الهاتف أولاً أو توصيله عبر USB.")
            return

        raw_cat = self.data_cat_combo.get()
        cat = raw_cat.split(" ")[0].strip().lower()
        if not cat:
            cat = "all_photos"

        desktop = os.path.join(os.path.expanduser("~"), "Desktop", f"Android_Rescue_{cat}_{int(time.time())}")
        is_ar = self.current_lang == "ar"

        self.data_log_text.delete("1.0", "end")
        self.data_log_text.insert("end", f"جاري بدء سحب البيانات ({cat}) إلى مجلد سطح المكتب...\n" if is_ar else f"Starting data extraction ({cat}) to Desktop folder...\n")

        def task():
            def prog(txt):
                self.after(0, lambda: self.data_log_text.insert("end", f"{txt}\n"))
            ok, msg = self.rescue_center.emergency_data_pull(serial, cat, desktop, progress_callback=prog)
            def upd():
                if ok:
                    messagebox.showinfo("اكتمل السحب", f"تم سحب الملفات بنجاح وحفظها على سطح المكتب في مجلد:\n{desktop}")
                    try:
                        webbrowser.open(desktop)
                    except Exception:
                        pass
                else:
                    messagebox.showwarning("تنبيه", f"نتيجة سحب البيانات:\n{msg}")
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _pull_all_photos_quick(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار الهاتف أولاً أو توصيله عبر USB.")
            return

        desktop = os.path.join(os.path.expanduser("~"), "Desktop", f"Android_All_Photos_{int(time.time())}")
        is_ar = self.current_lang == "ar"

        self.data_log_text.delete("1.0", "end")
        self.data_log_text.insert("end", "جاري فحص وسحب جميع الصور والفيديوهات من مجلدي DCIM و Pictures إلى سطح المكتب...\n" if is_ar else "Scanning and pulling all photos & videos from DCIM and Pictures...\n")

        def task():
            def prog(txt):
                self.after(0, lambda: self.data_log_text.insert("end", f"{txt}\n"))
            ok, msg = self.rescue_center.emergency_data_pull(serial, "all_photos", desktop, progress_callback=prog)
            def upd():
                if ok:
                    messagebox.showinfo("اكتمل سحب الصور", f"تم سحب جميع الصور والفيديوهات بنجاح وحفظها على سطح المكتب في:\n{desktop}")
                    try:
                        webbrowser.open(desktop)
                    except Exception:
                        pass
                else:
                    messagebox.showwarning("تنبيه", f"نتيجة سحب الصور:\n{msg}")
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
            messagebox.showwarning("تنبيه", "الرجاء اختيار ملف روم صالح (.zip) للتفليش.")
            return

        self.sideload_progress.set(0)
        self.sideload_text.delete("1.0", "end")
        self.sideload_text.insert("end", f"جاري بدء تفليش حزمة Sideload: {os.path.basename(path)}...\n" if is_ar else f"Starting ADB Sideload: {os.path.basename(path)}...\n")

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
                    messagebox.showinfo("اكتمل التفليش" if is_ar else "Sideload Complete", "تم تفليش الروم الرسمي وتحديث الجهاز بنجاح عبر Sideload!" if is_ar else "OTA Firmware package flashed successfully via Sideload!")
                else:
                    messagebox.showerror("فشل التفليش" if is_ar else "Sideload Failed", out_msg)
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _one_click_customer_backup(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً أو توصيله عبر USB.")
            return

        is_ar = self.current_lang == "ar"
        self.data_log_text.delete("1.0", "end")
        self.data_log_text.insert("end", "جاري بدء النسخ الشامل لبيانات العميل (الصور + الفيديوهات + جهات الاتصال + الرسائل)...\n" if is_ar else "Starting 1-Click Customer Backup (Media + Contacts + SMS)...\n")

        def task():
            def prog(txt):
                self.after(0, lambda: (self.data_log_text.insert("end", f"{txt}\n"), self.data_log_text.see("end")))
            ok, summary, b_dir = self.rescue_center.one_click_customer_backup(serial, progress_callback=prog)
            def done():
                if ok:
                    messagebox.showinfo(
                        "اكتمل النسخ الشامل" if is_ar else "Customer Backup Complete",
                        f"تم سحب جميع بيانات العميل بنجاح في مجلد سطح المكتب:\n{b_dir}" if is_ar else f"Customer data backup saved to Desktop:\n{b_dir}"
                    )
                    try:
                        webbrowser.open(b_dir)
                    except Exception:
                        pass
                else:
                    messagebox.showwarning("تنبيه" if is_ar else "Notice", summary)
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def run_nand_lifespan_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف أولاً.")
            return
        is_ar = self.current_lang == "ar"
        self.nand_text.delete("1.0", "end")
        self.nand_text.insert("end", "جاري قراءة سجلات JEDEC (Life Time & Pre-EOL) لشريحة الذاكرة...\n" if is_ar else "Reading JEDEC eMMC/UFS wear-level registers from hardware...\n")

        def task():
            rep = self.board_diag.read_emmc_ufs_lifespan(serial)
            self.last_storage_life = rep
            lines = [
                "=== تقرير استهلاك وعمر شريحة التخزين (JEDEC Lifespan) ===" if is_ar else "=== eMMC / UFS NAND WEAR-LEVEL & LIFESPAN REPORT ===",
                f"نوع الذاكرة (Storage Type):    {rep.storage_type}",
                f"استهلاك خلايا SLC (Type A):    {rep.life_time_type_a} [{rep.raw_hex_a}]",
                f"استهلاك خلايا MLC (Type B):    {rep.life_time_type_b} [{rep.raw_hex_b}]",
                f"حالة الكتل الاحتياطية (Pre-EOL): {rep.pre_eol_info} [{rep.raw_pre_eol}]",
                f"تقييم الصحة النهائي:           {rep.health_verdict}",
                f"التشخيص الفني:                 {rep.diagnosis}"
            ]
            def upd():
                self.nand_text.delete("1.0", "end")
                self.nand_text.insert("end", "\n".join(lines))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_speed_bench_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف أولاً.")
            return
        is_ar = self.current_lang == "ar"
        self.nand_text.delete("1.0", "end")
        self.nand_text.insert("end", "جاري تشغيل اختبار سرعة القراءة والكتابة المتتابعة بحجم 64MB (dd conv=fsync)...\n" if is_ar else "Running 64MB sequential read/write speed benchmark inside /data/local/tmp...\n")

        def task():
            w_speed, r_speed, verdict, diag = self.board_diag.benchmark_storage_speed(serial, test_size_mb=64)
            lines = [
                "=== نتيجة اختبار سرعة الذاكرة التخزينية (64MB Benchmark) ===" if is_ar else "=== STORAGE SEQUENTIAL READ / WRITE BENCHMARK (64MB) ===",
                f"سرعة الكتابة المتتابعة (Write): {_fmt(w_speed, 'MB/s')}" if is_ar else f"Sequential Write Speed: {_fmt(w_speed, 'MB/s')}",
                f"سرعة القراءة المتتابعة (Read):  {_fmt(r_speed, 'MB/s')}" if is_ar else f"Sequential Read Speed:  {_fmt(r_speed, 'MB/s')}",
                f"تقييم سرعة الناقل:              {verdict}" if is_ar else f"Throughput Verdict:     {verdict}",
                f"التشخيص الفني:                 {diag}" if is_ar else f"Technical Diagnosis:    {diag}"
            ]
            def upd():
                self.nand_text.delete("1.0", "end")
                self.nand_text.insert("end", "\n".join(lines))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_stress_test_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف أولاً.")
            return
        is_ar = self.current_lang == "ar"
        self.stress_progress.set(0)
        self.stress_text.delete("1.0", "end")
        self.stress_text.insert("end", "جاري بدء اختبار الضغط (30 ثانية) لمراقبة استقرار دوائر الباور وهبوط الفولت وترددات المعالج...\n" if is_ar else "Starting 30-second CPU/PMIC stress test...\n")

        def task():
            def prog(snap: StressTestSnapshot, msg: str):
                def update():
                    pct = min(1.0, snap.second / 30.0)
                    self.stress_progress.set(pct)
                    self.stress_lbl.configure(text=msg)
                    self.stress_text.insert("end", f"{msg}\n")
                    self.stress_text.see("end")
                self.after(0, update)

            rep = self.board_diag.run_cpu_pmic_stress_test(serial, duration_sec=30, progress_callback=prog)
            self.last_stress_report = rep

            lines = [
                "\n=== التقرير النهائي لاختبار ضغط المعالج والباور (30 ثانية) ===" if is_ar else "\n=== CPU & PMIC 30-SECOND STRESS TEST REPORT ===",
                f"التقييم العام:               {rep.verdict}" if is_ar else f"Overall Verdict:           {rep.verdict}",
                f"Initial battery voltage:   {_fmt(rep.initial_voltage_mv, 'mV')}",
                f"Minimum voltage under load: {_fmt(rep.min_voltage_mv, 'mV')}",
                f"Voltage drop:              {_fmt(rep.voltage_sag_mv, 'mV')}",
                f"Temperature ({rep.temp_source}): {_fmt(rep.initial_temp_c, '°C')} -> {_fmt(rep.max_temp_c, '°C')}",
                f"CPU frequency range:       {_fmt(rep.min_freq_mhz, 'MHz')} - {_fmt(rep.max_freq_mhz, 'MHz')}",
                f"التشخيص النهائي:             {rep.diagnosis}" if is_ar else f"Final Diagnosis:           {rep.diagnosis}"
            ]

            def done():
                self.stress_progress.set(1.0)
                self.stress_text.insert("end", "\n".join(lines))
                self.stress_text.see("end")
                if rep.severe_sag_detected:
                    messagebox.showwarning("تنبيه هبوط الجهد" if is_ar else "Voltage Sag Alert", rep.diagnosis)
                else:
                    messagebox.showinfo("اكتمل اختبار الضغط" if is_ar else "Stress Test Complete", f"استقرار النظام: {rep.verdict}\n{rep.diagnosis}")
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _switch_slot(self, slot: str):
        serial = self.current_serial.get()
        is_ar = self.current_lang == "ar"
        self.slot_log_text.delete("1.0", "end")
        self.slot_log_text.insert("end", f"جاري تحويل السلوت النشط إلى Slot {slot.upper()}...\n" if is_ar else f"Switching active boot slot to Slot {slot.upper()}...\n")

        def task():
            ok, msg = self.rescue_center.switch_active_slot(slot, serial)
            def upd():
                self.slot_log_text.delete("1.0", "end")
                self.slot_log_text.insert("end", msg)
                if ok:
                    messagebox.showinfo("تبديل السلوت" if is_ar else "Slot Switch", f"تم تحويل السلوت بنجاح إلى Slot {slot.upper()}!" if is_ar else f"Successfully switched to Slot {slot.upper()}!")
                else:
                    messagebox.showwarning("تنبيه" if is_ar else "Notice", msg)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _check_arb(self):
        serial = self.current_serial.get()
        is_ar = self.current_lang == "ar"
        self.slot_log_text.delete("1.0", "end")
        self.slot_log_text.insert("end", "جاري قراءة رقم حماية الرجوع Anti-Rollback من البوت لودر...\n" if is_ar else "Querying Anti-Rollback (ARB) hardware fuse from bootloader...\n")

        def task():
            ok, msg, arb_index = self.rescue_center.check_anti_rollback(serial)
            def upd():
                self.slot_log_text.delete("1.0", "end")
                self.slot_log_text.insert("end", msg)
                if ok:
                    messagebox.showinfo("مؤشر ARB" if is_ar else "ARB Index", f"مؤشر حماية الرجوع: ARB={arb_index}\nاحذر تفليش روم بإصدار أقل لتفادي إتلاف الهاتف." if is_ar else f"Anti-Rollback Protection Index: ARB={arb_index}\nDo not flash ROM with lower ARB!")
                else:
                    messagebox.showwarning("تنبيه" if is_ar else "Notice", msg)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _backup_critical_partitions(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف أولاً.")
            return
        is_ar = self.current_lang == "ar"
        self.part_backup_text.delete("1.0", "end")
        self.part_backup_text.insert("end", "جاري نسخ الأقسام الحساسة (EFS / FSG / Modem / Persist / Boot)...\n" if is_ar else "Dumping critical radio partitions (EFS / FSG / Modem / Persist / Boot)...\n")

        def task():
            def prog(txt):
                self.after(0, lambda: self.part_backup_text.insert("end", f"{txt}\n"))
            res = self.partition_backup.backup_critical_partitions(serial=serial, progress_callback=prog)
            def upd():
                lines = [
                    f"\n=== تقرير النسخ الاحتياطي للأقسام الحساسة ===" if is_ar else "\n=== CRITICAL PARTITION BACKUP REPORT ===",
                    f"النجاح: {'نعم (اكتمل)' if res.success else 'تنبيه/جزئي'}",
                    f"عدد الأقسام المنسوخة: {len(res.dumped_partitions)}",
                    f"المجلد المحفوظ به: {res.backup_directory}",
                    f"التشخيص: {res.diagnosis}"
                ]
                self.part_backup_text.insert("end", "\n".join(lines))
                if res.success:
                    messagebox.showinfo("اكتمل النسخ" if is_ar else "Backup Complete", f"تم نسخ أقسام الشبكة الحساسة وحفظها في:\n{res.backup_directory}" if is_ar else f"Critical partitions saved to:\n{res.backup_directory}")
                else:
                    messagebox.showwarning("تنبيه" if is_ar else "Notice", res.diagnosis)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _backup_all_partitions(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف أولاً.")
            return
        is_ar = self.current_lang == "ar"
        self.part_backup_text.delete("1.0", "end")
        self.part_backup_text.insert("end", "جاري نسخ كامل أقسام الهاتف (Full Partition Table Dump)...\n" if is_ar else "Dumping all discoverable partitions to PC...\n")

        def task():
            def prog(txt):
                self.after(0, lambda: self.part_backup_text.insert("end", f"{txt}\n"))
            res = self.partition_backup.backup_all_partitions(serial=serial, progress_callback=prog)
            def upd():
                self.part_backup_text.insert("end", f"\nاكتمل النسخ: {len(res.dumped_partitions)} قسم. المجلد: {res.backup_directory}\n")
                messagebox.showinfo("اكتمل النسخ" if is_ar else "Backup Complete", f"تم حفظ الأقسام في:\n{res.backup_directory}")
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _open_backups_folder(self):
        folder = self.partition_backup.backup_base_dir
        os.makedirs(folder, exist_ok=True)
        webbrowser.open(folder)

    def _batch_install_apks(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return
        files = filedialog.askopenfilenames(filetypes=[("Android APK", "*.apk"), ("All Files", "*.*")])
        if not files:
            return
        is_ar = self.current_lang == "ar"
        self.apk_log_text.delete("1.0", "end")
        self.apk_log_text.insert("end", f"جاري تثبيت {len(files)} تطبيق على الجهاز دفعة واحدة...\n" if is_ar else f"Batch installing {len(files)} APK files...\n")

        def task():
            def prog(cur, tot, f):
                self.after(0, lambda: self.apk_log_text.insert("end", f"[{cur}/{tot}] تثبيت: {os.path.basename(f)}...\n" if is_ar else f"[{cur}/{tot}] Installing: {os.path.basename(f)}...\n"))
            results = self.batch_apk.install_multiple_apks(serial, list(files), progress_callback=prog)
            def upd():
                successes = sum(1 for r in results if r.success)
                summary = f"\nاكتمل التثبيت: نجح {successes} من أصل {len(results)} تطبيق." if is_ar else f"\nBatch install finished: {successes}/{len(results)} successful."
                self.apk_log_text.insert("end", summary)
                messagebox.showinfo("اكتمل التثبيت" if is_ar else "Install Complete", summary)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _extract_user_apps(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return
        is_ar = self.current_lang == "ar"
        self.apk_log_text.delete("1.0", "end")
        self.apk_log_text.insert("end", "جاري حصر وتفريغ تطبيقات المستخدم وحفظها بصيغة APK على الكمبيوتر...\n" if is_ar else "Listing and extracting user apps to PC...\n")

        def task():
            def prog(cur, tot, pkg):
                self.after(0, lambda: self.apk_log_text.insert("end", f"[{cur}/{tot}] سحب: {pkg}...\n" if is_ar else f"[{cur}/{tot}] Pulling: {pkg}...\n"))
            results = self.batch_apk.extract_all_user_apks(serial, progress_callback=prog)
            def upd():
                successes = sum(1 for r in results if r.success)
                summary = f"\nاكتمل استخراج التطبيقات: تم سحب {successes} تطبيق إلى مجلد سطح المكتب." if is_ar else f"\nExtracted {successes} apps to Desktop."
                self.apk_log_text.insert("end", summary)
                messagebox.showinfo("اكتمل السحب" if is_ar else "Extraction Complete", summary)
                if results and results[0].output_path:
                    webbrowser.open(os.path.dirname(results[0].output_path))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_fast_charge_diag_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return
        is_ar = self.current_lang == "ar"
        self.fast_charge_text.delete("1.0", "end")
        self.fast_charge_text.insert("end", "جاري قراءة بروتوكول الشحن ومتحسس التيار ومفاوضة الشاحن (Power Delivery / Quick Charge)...\n" if is_ar else "Reading charging protocol negotiation, voltage, and live wattage...\n")

        def task():
            diag = self.board_diag.diagnose_fast_charge(serial)
            self.last_fast_charge = diag
            lines = [
                "=== تقرير بروتوكول الشحن السريع والقدرة الحقيقية ===" if is_ar else "=== FAST CHARGE PROTOCOL & POWER NEGOTIATION ===",
                f"البروتوكول المكتشف: {diag.protocol_name}" if is_ar else f"Protocol: {diag.protocol_name}",
                f"نوع المنفذ / الشاحن: {diag.charger_type}" if is_ar else f"Charger Type: {diag.charger_type}",
                f"[{diag.status}]",
                f"USB input voltage (measured): {_fmt(diag.voltage_mv, 'mV')}",
                f"USB input current (measured): {_fmt(diag.current_ma, 'mA')}",
                f"Input power (measured V x I): {_fmt(diag.wattage_w, 'W')}",
                f"Reported max current / voltage (not measured): {_fmt(diag.max_charging_current_ma, 'mA')} / {_fmt(diag.max_charging_voltage_mv, 'mV')}",
                f"Fast charging: {'UNKNOWN' if diag.is_fast_charging is None else ('yes' if diag.is_fast_charging else 'no')}",
                f"Battery: {_fmt(diag.battery_level, '%')} ({diag.charge_status})",
                f"التشخيص: {diag.diagnosis}" if is_ar else f"Diagnosis: {diag.diagnosis}"
            ]
            def upd():
                self.fast_charge_text.delete("1.0", "end")
                self.fast_charge_text.insert("end", "\n".join(lines))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_reset_gauge_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return
        is_ar = self.current_lang == "ar"
        def task():
            ok, msg = self.board_diag.reset_battery_stats_and_gauge(serial)
            def upd():
                self.fast_charge_text.delete("1.0", "end")
                self.fast_charge_text.insert("end", f"إعادة ضبط إحصائيات استهلاك البطارية:\n{msg}" if is_ar else f"Reset battery statistics:\n{msg}")
                messagebox.showinfo(
                    "إعادة ضبط إحصائيات البطارية" if is_ar else "Reset Battery Statistics",
                    "تم تصفير سجلات استهلاك البطارية بنجاح.\n(ملاحظة: هذا الإجراء يعيد ضبط إحصائيات نظام التشغيل فقط ولا يعاير العتاد الداخلي)." if is_ar else "Battery usage statistics reset successfully.\n(Note: This resets OS statistics only; it does not recalibrate hardware fuel gauge)."
                )
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_storage_benchmark_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return
        is_ar = self.current_lang == "ar"
        self.storage_text.delete("1.0", "end")
        self.storage_text.insert("end", "جاري فحص مؤشرات استهلاك الخلايا (JEDEC Life Time) واختبار سرعة القراءة والكتابة...\n" if is_ar else "Running eMMC/UFS health check & read/write speed benchmark...\n")

        def task():
            report = self.storage_bench.run_full_storage_test(serial)
            self.last_storage_report = report
            lines = [
                "=== تقرير صحة وسرعة ذاكرة التخزين (eMMC / UFS) ===" if is_ar else "=== STORAGE (eMMC/UFS) LIFE CYCLE & SPEED REPORT ===",
                f"الحالة المعتمدة: [{report.status}]" if is_ar else f"Verified Status: [{report.status}]",
                f"نوع الذاكرة المكتشف: {report.storage_type}" if is_ar else f"Storage Type: {report.storage_type}",
                f"استهلاك خلايا SLC (Type A): {report.life_time_type_a}" if is_ar else f"SLC Wear (Type A): {report.life_time_type_a}",
                f"استهلاك خلايا MLC (Type B): {report.life_time_type_b}" if is_ar else f"MLC Wear (Type B): {report.life_time_type_b}",
                f"حالة الكتل الاحتياطية (Pre-EOL): {report.pre_eol_info}" if is_ar else f"Reserved Blocks (Pre-EOL): {report.pre_eol_info}",
                f"سرعة الكتابة المتتابعة (Write): {report.write_speed_mb_s} MB/s" if is_ar else f"Sequential Write Speed: {report.write_speed_mb_s} MB/s",
                f"سرعة القراءة المتتابعة (Read): {report.read_speed_mb_s} MB/s" if is_ar else f"Sequential Read Speed: {report.read_speed_mb_s} MB/s",
                f"تقييم صحة الذاكرة: {report.health_verdict}" if is_ar else f"Health Verdict: {report.health_verdict}",
                f"التشخيص الفني: {report.diagnosis}" if is_ar else f"Diagnosis: {report.diagnosis}"
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
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return
        is_ar = self.current_lang == "ar"
        self.touch_text.delete("1.0", "end")
        self.touch_text.insert("end", "جاري نشر وتشغيل شبكة فحص الديجيتيزر التفاعلية على شاشة الهاتف...\n" if is_ar else "Deploying touch digitizer grid test to phone...\n")

        def task():
            ok, msg = self.touch_matrix.deploy_and_launch_touch_test(serial)
            def upd():
                self.touch_text.delete("1.0", "end")
                self.touch_text.insert("end", f"حالة اختبار اللمس:\n{msg}\n")
                if ok:
                    messagebox.showinfo("اختبار اللمس" if is_ar else "Touch Test", "تم فتح شبكة فحص اللمس على شاشة الهاتف!\nالمس جميع المربعات للتأكد من خلو الشاشة من المناطق الميتة (Dead Zones)." if is_ar else "Touch test grid opened on device! Touch all boxes to identify dead zones.")
                else:
                    messagebox.showwarning("تنبيه" if is_ar else "Notice", msg)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_full_deep_scan_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
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
                security_summary=f"Risk: {sec.overall_risk}",
                raw_dict={
                    "profile": profile.to_dict(),
                    "board": bdiag.to_dict(),
                    "hardware": haudit.to_dict(),
                    "security": sec.to_dict()
                }
            )

            def done():
                messagebox.showinfo("اكتمل الفحص الشامل", f"تم فحص العتاد بالكامل!\nصحة اللوحة الأم: {bdiag.overall_health}\nمستوى الأمان: {sec.overall_risk}\nكفاءة البطارية الحقيقية: {bdiag.pmic.true_health_pct}%")
                self.run_profile_async()
            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def run_screen_color_test_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه" if self.current_lang == "ar" else "Notice", "الرجاء اختيار هاتف أولاً." if self.current_lang == "ar" else "Select a device first.")
            return
        is_ar = self.current_lang == "ar"
        self.touch_text.delete("1.0", "end")
        self.touch_text.insert("end", "جاري تشغيل شاشة فحص الألوان والبكسلات الميتة (RGBW) وتفعيل تتبع مسار اللمس...\n" if is_ar else "Deploying full-screen RGBW dead-pixel & color canvas with touch tracking...\n")

        def task():
            ok, msg = self.hardware_tester.launch_screen_color_test(serial)
            def upd():
                self.touch_text.delete("1.0", "end")
                self.touch_text.insert("end", f"حالة فحص الشاشة:\n{msg}\n" if is_ar else f"Screen Test Status:\n{msg}\n")
                if ok:
                    answer = messagebox.askyesno(
                        "فحص الشاشة والبكسلات" if is_ar else "Screen Color Test",
                        "شاشة فحص الألوان تعمل الآن على الهاتف!\nالمس الشاشة للتنقل بين الألوان: (أحمر، أخضر، أزرق، أبيض، أسود، تدرج رمادي).\n\nهل انتهيت وترغب في إغلاق شاشة الفحص وإلغاء تتبع اللمس؟" if is_ar else "Interactive color test is now running on your phone!\nTap the screen to cycle through Red, Green, Blue, White, Black, and Grayscale.\n\nDid you finish and want to close the test?"
                    )
                    if answer:
                        self.hardware_tester.stop_screen_color_test(serial)
                        self.touch_text.insert("end", "تم إغلاق شاشة الفحص وإلغاء تتبع مسار اللمس بنجاح.\n" if is_ar else "Screen test closed and touch overlays reverted.\n")
                else:
                    messagebox.showwarning("تنبيه" if is_ar else "Notice", msg)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_speaker_tone_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه" if self.current_lang == "ar" else "Notice", "الرجاء اختيار هاتف أولاً." if self.current_lang == "ar" else "Select a device first.")
            return
        is_ar = self.current_lang == "ar"
        self.audio_codec_text.delete("1.0", "end")
        self.audio_codec_text.insert("end", "جاري توليد نغمة 1000Hz جيبية نقية وتشغيلها بأعلى صوت لاختبار نقاء مكبر الصوت...\n" if is_ar else "Generating pure 1000Hz sine test tone and playing via phone loudspeaker...\n")

        def task():
            ok, msg = self.hardware_tester.play_speaker_test_tone(serial, freq=1000, duration_sec=3.0)
            def upd():
                self.audio_codec_text.delete("1.0", "end")
                self.audio_codec_text.insert("end", f"فحص مكبر الصوت:\n{msg}\n" if is_ar else f"Speaker Test Output:\n{msg}\n")
                if ok:
                    messagebox.showinfo("فحص الصوت" if is_ar else "Speaker Test", "تم تشغيل نغمة الاختبار على مكبر صوت الهاتف بنجاح!" if is_ar else "Test tone played successfully on phone loudspeaker!")
                else:
                    messagebox.showwarning("تنبيه" if is_ar else "Notice", msg)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_companion_diag_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه" if self.current_lang == "ar" else "Notice", "الرجاء اختيار هاتف أولاً." if self.current_lang == "ar" else "Select a device first.")
            return
        is_ar = self.current_lang == "ar"
        self.sensors_text.delete("1.0", "end")
        self.sensors_text.insert("end", "جاري فحص التطبيق المساعد التفاعلي أو تفعيل وضع التشخيص Zero-APK...\n" if is_ar else "Checking Companion APK or activating Zero-APK fallback suite...\n")

        def task():
            ok, msg = self.hardware_tester.install_companion_apk(serial)
            if ok:
                self.hardware_tester.run_companion_broadcast(serial, "START_DIAGNOSTICS")
                res = self.hardware_tester.read_companion_results(serial)
                diag_txt = f"نتائج التطبيق المساعد:\n{res}" if is_ar else f"Companion Results:\n{res}"
            else:
                diag_txt = f"حالة التطبيق المساعد:\n{msg}\nتم تفعيل أدوات الفحص المباشر Zero-APK (فحص الشاشة، فحص الصوت، وفحص الحساسات المباشر)." if is_ar else f"Companion Status:\n{msg}\nZero-APK interactive suite is active."
            def upd():
                self.sensors_text.delete("1.0", "end")
                self.sensors_text.insert("end", diag_txt)
                messagebox.showinfo("الفحص التفاعلي" if is_ar else "Interactive Diag", diag_txt)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_root_forensics_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه" if self.current_lang == "ar" else "Notice", "الرجاء اختيار هاتف أولاً." if self.current_lang == "ar" else "Select a device first.")
            return
        is_ar = self.current_lang == "ar"
        self.root_forensics_text.delete("1.0", "end")
        self.root_forensics_text.insert("end", "جاري تشغيل محرك التحليل الجنائي المتقدم (Root Forensics Pro) وفحص سجلات الكيرنل وذاكرة Ramoops وناقل I2C وعمر الذاكرة الحقيقي...\n" if is_ar else "Running Root Forensics Pro: inspecting persistent ramoops, I2C bus errors, and raw NAND lifetime...\n")

        def task():
            rep = self.board_diag.run_root_forensics_pro(serial)
            self.last_root_forensics = rep
            lines = [
                "=== تقرير التحليل الجنائي المتقدم للكيرنل واللوحة الأم (Root Forensics Pro) ===" if is_ar else "=== ROOT FORENSICS PRO & KERNEL CRASH ANALYSIS ===",
                f"حالة الروت: {'مروّت (' + rep.root_type + ')' if rep.is_rooted else 'غير مروّت (SELinux Enforcing)'}" if is_ar else f"Root Status: {'ROOTED (' + rep.root_type + ')' if rep.is_rooted else 'NON_ROOT (SELinux Enforcing)'}",
                f"التشخيص العام: {rep.diagnosis}" if is_ar else f"Diagnosis: {rep.diagnosis}",
                f"استهلاك خلايا SLC (su life_time): {rep.nand_exact_lifetime_a or 'غير متاح بدون روت'}" if is_ar else f"SLC Wear (Direct sysfs): {rep.nand_exact_lifetime_a or 'Unavailable without root'}",
                f"استهلاك خلايا MLC (su life_time): {rep.nand_exact_lifetime_b or 'غير متاح بدون روت'}" if is_ar else f"MLC Wear (Direct sysfs): {rep.nand_exact_lifetime_b or 'Unavailable without root'}",
                f"حالة كتل الاحتياط (Pre-EOL): {rep.nand_exact_pre_eol or 'غير متاح بدون روت'}" if is_ar else f"Reserved Blocks (Pre-EOL): {rep.nand_exact_pre_eol or 'Unavailable without root'}",
                f"تقييم صحة الذاكرة: {rep.nand_wear_status}" if is_ar else f"NAND Wear Status: {rep.nand_wear_status}",
                f"أخطاء ناقل I2C للهاردوير: {len(rep.i2c_bus_errors)} خطأ مسجل" if is_ar else f"I2C Bus Hardware Errors: {len(rep.i2c_bus_errors)} errors",
                f"بصمات الأعطال المكتشفة في الكيرنل: {len(rep.matched_fault_signatures)}" if is_ar else f"Matched Kernel Crash Signatures: {len(rep.matched_fault_signatures)}"
            ]
            if rep.matched_fault_signatures:
                lines.append("\nالأعطال المطابقة المسجلة في الكيرنل:" if is_ar else "\nMatched Hardware Fault Signatures:")
                for f in rep.matched_fault_signatures:
                    name = f.get("name_ar" if is_ar else "name", "عطل")
                    lines.append(f"  • [{f.get('severity', 'UNKNOWN')}] {name}: {f.get('description_ar' if is_ar else 'description', '')}")
            if rep.i2c_bus_errors:
                lines.append("\nأخطاء ناقل I2C المكتشفة:" if is_ar else "\nI2C Bus Error Logs:")
                for err in rep.i2c_bus_errors[:5]:
                    lines.append(f"  • {err}")
            if rep.evidence:
                lines.append(format_evidence_for_display(rep.evidence, is_ar=is_ar))

            def upd():
                self.root_forensics_text.delete("1.0", "end")
                self.root_forensics_text.insert("end", "\n".join(lines))
                if rep.is_rooted:
                    messagebox.showinfo("التحليل الجنائي" if is_ar else "Forensics Complete", "تم استخراج أدلة الكيرنل وسجلات Ramoops واستهلاك الذاكرة الدقيق بنجاح!" if is_ar else "Root forensics completed successfully!")
                else:
                    messagebox.showwarning("تنبيه الروت" if is_ar else "Root Required", rep.diagnosis)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_efs_backup_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه" if self.current_lang == "ar" else "Notice", "الرجاء اختيار هاتف أولاً." if self.current_lang == "ar" else "Select a device first.")
            return
        is_ar = self.current_lang == "ar"
        self.root_forensics_text.delete("1.0", "end")
        self.root_forensics_text.insert("end", "جاري اكتشاف أقسام الشبكة الحساسة (EFS / NVRAM / Modem) والنسخ الاحتياطي بصيغة raw dd block images مع حساب بصمات SHA-256...\n" if is_ar else "Discovering critical EFS/NVRAM/Modem partitions and performing raw dd block backup with SHA-256 hashes...\n")

        def task():
            def prog(msg):
                self.after(0, lambda: self.root_forensics_text.insert("end", f"{msg}\n"))
            res = self.board_diag.backup_efs_nvram_partitions(serial=serial, progress_callback=prog)
            self.last_efs_backup = res
            def upd():
                lines = [
                    "\n=== تقرير النسخ الاحتياطي الخام لأقسام EFS و NVRAM ===" if is_ar else "\n=== RAW EFS / NVRAM BACKUP REPORT ===",
                    f"النجاح: {'نعم (اكتمل بنجاح)' if res.success else 'فشل أو يحتاج صلاحيات'}" if is_ar else f"Success: {'YES (Completed)' if res.success else 'FAILED / Needs Permissions'}",
                    f"عدد الأقسام المنسوخة: {len(res.backed_up_partitions)}",
                    f"حجم النسخة الإجمالي: {res.total_bytes / (1024*1024):.2f} MB",
                    f"مجلد الحفظ: {res.backup_folder}",
                    f"الرسالة: {res.message}"
                ]
                if res.hashes:
                    lines.append("بصمات SHA-256 للأقسام:" if is_ar else "Partition SHA-256 Checksums:")
                    for p, h in res.hashes.items():
                        lines.append(f"  • {p}: {h[:16]}...")
                self.root_forensics_text.insert("end", "\n".join(lines))
                if res.success:
                    messagebox.showinfo("اكتمل النسخ الخام" if is_ar else "Raw Backup Complete", f"تم حفظ صور أقسام EFS/NVRAM الحساسة في:\n{res.backup_folder}" if is_ar else f"Raw partitions successfully dumped to:\n{res.backup_folder}")
                    if res.backup_folder and os.path.exists(res.backup_folder):
                        webbrowser.open(res.backup_folder)
                else:
                    messagebox.showwarning("تنبيه" if is_ar else "Notice", res.message)
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _generate_and_open_certificate(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning("تنبيه", "الرجاء اختيار هاتف مصرح أولاً.")
            return

        tech = self.tech_name_entry.get().strip() or ("كبير مهندسي الصيانة" if self.current_lang == "ar" else "Chief Hardware Technician")
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
        lines = [f"=== سجل تقارير الفحص التشخيصي المسجلة بالذاكرة ({len(reports)}) ===" if is_ar else f"=== STORED DIAGNOSTIC REPORTS IN DATABASE ({len(reports)}) ==="]
        for r in reports:
            batt_pct = r.get('battery_health_pct')
            batt_str = f"{batt_pct}%" if batt_pct is not None else "UNKNOWN"
            lines.append(f"تقرير #{r['id']} | الجهاز: {r['device_serial']} | التاريخ: {r['timestamp']} | الحالة: {r['overall_status']} | كفاءة البطارية: {batt_str}")
        self.reports_text.delete("1.0", "end")
        self.reports_text.insert("end", "\n".join(lines))
