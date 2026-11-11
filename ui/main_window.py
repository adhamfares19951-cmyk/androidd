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
    BG_CARD_ELEVATED,
    BG_HOVER,
    NAV_ICONS,
    ICON_REFRESH,
    icon_image,
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

# Re-exported: tests and older callers import these from ui.main_window.
from ui.ui_helpers import _tri_key, _feature_key, _flinger_key, maintenance_pill_status, bootrom_pill_status, _fmt, format_evidence_for_display  # noqa: F401
from ui.tabs.dashboard import DashboardTabMixin
from ui.tabs.board_diag import BoardDiagTabMixin
from ui.tabs.hardware import HardwareTabMixin
from ui.tabs.security import SecurityTabMixin
from ui.tabs.optimizer import OptimizerTabMixin
from ui.tabs.rescue import RescueTabMixin
from ui.tabs.reports import ReportsTabMixin

logger = logging.getLogger("MainWindow")
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")
ctk.ThemeManager.theme["CTkFont"]["family"] = "Segoe UI"


class AndroidDoctorApp(DashboardTabMixin, BoardDiagTabMixin, HardwareTabMixin, SecurityTabMixin, OptimizerTabMixin, RescueTabMixin, ReportsTabMixin, ctk.CTk):
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
        self.grid_rowconfigure(0, weight=0)
        self.grid_rowconfigure(1, weight=1)

        # ------------------ SIDEBAR ------------------
        # Placed on the reading-start side: right in Arabic, left in English (_apply_direction).
        self.sidebar_frame = ctk.CTkFrame(self, width=248, corner_radius=0, fg_color=BG_SIDEBAR, border_width=0)
        self.sidebar_frame.grid_propagate(False)
        self.sidebar_frame.grid_columnconfigure(0, weight=1)
        for r in range(6):
            self.sidebar_frame.grid_rowconfigure(r, weight=0)
        self.sidebar_frame.grid_rowconfigure(6, weight=1)
        self.sidebar_frame.grid_rowconfigure(7, weight=0)
        self.sidebar_edge = ctk.CTkFrame(self, width=1, corner_radius=0, fg_color=BORDER_SUBTLE)

        # Brand: monogram + name
        brand = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        brand.grid(row=0, column=0, padx=18, pady=(20, 18), sticky="ew")
        self.brand_mark = ctk.CTkLabel(
            brand, text="AD", width=34, height=34, corner_radius=9,
            fg_color=ACCENT_BLUE, text_color="#ffffff",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
        )
        self.brand_mark.pack(side=L["lead"])
        brand_text = ctk.CTkFrame(brand, fg_color="transparent")
        brand_text.pack(side=L["lead"], fill="x", expand=True, padx=10)
        self.logo_label = ctk.CTkLabel(
            brand_text,
            text=self._L(self._tr("ui.android_doctor_pro_2")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=TEXT_PRIMARY,
            anchor=L["anchor"],
        )
        self.logo_label.pack(fill="x")
        self.subtitle_label = ctk.CTkLabel(
            brand_text,
            text=self._L(self._tr("ui.hardware_diagnostics_suite")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_MUTED,
            anchor=L["anchor"],
            justify=L["justify"],
            wraplength=170,
        )
        self.subtitle_label.pack(fill="x")

        # Device picker
        dev_card = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        dev_card.grid(row=1, column=0, padx=14, pady=(0, 14), sticky="ew")
        self.dev_select_label = ctk.CTkLabel(
            dev_card,
            text=self._L(self._tr("target_device")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_MUTED,
            anchor=L["anchor"],
        )
        self.dev_select_label.pack(fill="x", padx=4, pady=(0, 6))
        dev_row = ctk.CTkFrame(dev_card, fg_color="transparent")
        dev_row.pack(fill="x")
        self.device_combo = ctk.CTkComboBox(
            dev_row,
            values=[self._tr("no_devices")],
            command=self._on_device_selected,
            height=34,
            corner_radius=7,
            fg_color=BG_INPUT,
            border_color=BORDER_ACCENT,
            border_width=1,
            button_color=BG_CARD_ELEVATED,
            button_hover_color=BG_HOVER,
            dropdown_fg_color=BG_CARD_ELEVATED,
            dropdown_hover_color=BG_HOVER,
            dropdown_text_color=TEXT_PRIMARY,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            justify=L["justify"],
        )
        self.device_combo.pack(side=L["lead"], fill="x", expand=True)
        self.btn_refresh_dev = ctk.CTkButton(
            dev_row,
            text="",
            image=icon_image(ICON_REFRESH, TEXT_SECONDARY, 16),
            width=34,
            height=34,
            corner_radius=7,
            fg_color=BG_CARD_ELEVATED,
            hover_color=BG_HOVER,
            border_width=1,
            border_color=BORDER_ACCENT,
            command=self.refresh_devices_async,
        )
        self.btn_refresh_dev.pack(side=L["trail"], padx=(6, 0) if L["trail"] == "right" else (0, 6))

        # Navigation
        self.nav_section_label = ctk.CTkLabel(
            self.sidebar_frame, text="", height=1, fg_color="transparent")
        self.nav_section_label.grid(row=2, column=0, sticky="ew")
        self.nav_container = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.nav_container.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 6))

        self.nav_buttons = {}
        self._nav_icons = {}
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
            glyph = NAV_ICONS.get(key, "")
            self._nav_icons[key] = (icon_image(glyph, TEXT_MUTED, 18), icon_image(glyph, ACCENT_BLUE, 18))
            btn = ctk.CTkButton(
                self.nav_container,
                text=self._L(self._tr(tr_key)),
                image=self._nav_icons[key][0],
                compound=L["lead"],
                anchor=L["anchor"],
                height=38,
                corner_radius=7,
                border_width=0,
                fg_color="transparent",
                text_color=TEXT_SECONDARY,
                hover_color=BG_HOVER,
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                command=lambda k=key: self.select_tab(k),
            )
            btn.pack(fill="x", pady=1)
            self.nav_buttons[key] = btn

        self.sidebar_spacer = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.sidebar_spacer.grid(row=6, column=0, sticky="nsew")

        # Language switch (segmented)
        lang_bar = ctk.CTkFrame(self.sidebar_frame, fg_color=BG_TILE, corner_radius=8,
                                border_width=1, border_color=BORDER_SUBTLE)
        lang_bar.grid(row=7, column=0, padx=14, pady=(0, 10), sticky="ew")
        lang_bar.grid_columnconfigure((0, 1), weight=1, uniform="lang_col")
        self.btn_lang_ar = ctk.CTkButton(
            lang_bar, text=self._L(self._tr("ui.text")), height=28, corner_radius=6,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            command=lambda: self.switch_language("ar"),
        )
        self.btn_lang_en = ctk.CTkButton(
            lang_bar, text=self._tr("ui.english"), height=28, corner_radius=6,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            command=lambda: self.switch_language("en"),
        )
        self._style_lang_buttons()

        # Connection status
        self.status_box = ctk.CTkFrame(self.sidebar_frame, fg_color="transparent")
        self.status_box.grid(row=8, column=0, padx=14, pady=(0, 16), sticky="ew")
        status_inner = ctk.CTkFrame(self.status_box, fg_color="transparent")
        status_inner.pack(fill="x", padx=4)
        self.adb_status_dot = ctk.CTkFrame(status_inner, width=8, height=8, corner_radius=4, fg_color=TEXT_MUTED)
        self.adb_status_dot.pack(side=L["lead"], padx=(0, 8) if L["lead"] == "left" else (8, 0))
        self.adb_status_label = ctk.CTkLabel(
            status_inner,
            text=self._L(self._tr("adb_disconnected")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_MUTED,
            anchor=L["anchor"],
            justify=L["justify"],
            wraplength=190,
        )
        self.adb_status_label.pack(side=L["lead"], fill="x", expand=True)

        # ------------------ MAIN CONTENT AREA ------------------
        self.tools_banner = ctk.CTkFrame(
            self,
            fg_color="#2a1416",
            corner_radius=8,
            border_width=1,
            border_color="#5c2427",
        )
        self.tools_banner_lbl = ctk.CTkLabel(
            self.tools_banner,
            text="",
            text_color="#ff9b94",
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
            variant="secondary",
            height=30,
            width=150,
        )
        self.btn_repair_tools.pack(side=L["trail"], padx=14, pady=8)

        self.content_frame = ctk.CTkFrame(self, corner_radius=0, fg_color=BG_CANVAS)
        self.content_frame.grid_columnconfigure(0, weight=1)
        self.content_frame.grid_rowconfigure(0, weight=1)

        self._apply_direction()

        self.tab_frames = {}
        self._init_tab_builders()
        self.select_tab("dashboard")
        self.after(150, self._build_next_pending_tab)
        self.after(300, self.refresh_devices_async)
        self.after(5000, self._auto_poll_devices)
        self.after(400, self._check_tools_background)

    def _sidebar_col(self) -> int:
        return 2 if self._layout()["is_rtl"] else 0

    def _content_col(self) -> int:
        return 0 if self._layout()["is_rtl"] else 2

    def _apply_direction(self):
        """Sidebar on the reading-start side: right for Arabic, left for English."""
        side, content = self._sidebar_col(), self._content_col()
        self.grid_columnconfigure(side, weight=0, minsize=248)
        self.grid_columnconfigure(1, weight=0, minsize=1)
        self.grid_columnconfigure(content, weight=1, minsize=0)
        self.sidebar_frame.grid(row=0, column=side, rowspan=2, sticky="nsew")
        self.sidebar_edge.grid(row=0, column=1, rowspan=2, sticky="ns")
        self.content_frame.grid(row=1, column=content, sticky="nsew", padx=(28, 20) if side == 0 else (20, 28), pady=(4, 0))
        if getattr(self, "tools_banner", None) is not None and self.tools_banner.winfo_ismapped():
            self.tools_banner.grid(row=0, column=content, sticky="ew", padx=20, pady=(14, 0))

    def _style_lang_buttons(self):
        for btn, code, col in ((self.btn_lang_ar, "ar", 0), (self.btn_lang_en, "en", 1)):
            active = self.current_lang == code
            btn.configure(
                fg_color=BG_CARD_ELEVATED if active else "transparent",
                hover_color=BG_HOVER,
                text_color=TEXT_PRIMARY if active else TEXT_MUTED,
            )
            # Arabic first on the right in RTL, English first on the left in LTR
            pos = (1 - col) if self._layout()["is_rtl"] else col
            btn.grid(row=0, column=pos, padx=3, pady=3, sticky="ew")

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

        self._style_lang_buttons()
        self._apply_direction()

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
                    text=self._L(self._tr(tr_key)), anchor=L["anchor"], compound=L["lead"]
                )

        self.dev_select_label.configure(
            text=self._L(self._tr("target_device")), anchor=L["anchor"]
        )
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
                        fg_color=BG_CARD_ELEVATED,
                        text_color=TEXT_PRIMARY,
                        image=self._nav_icons.get(k, (None, None))[1],
                    )
            else:
                if frame is not None:
                    frame.grid_forget()
                if k in self.nav_buttons:
                    self.nav_buttons[k].configure(
                        fg_color="transparent",
                        text_color=TEXT_SECONDARY,
                        image=self._nav_icons.get(k, (None, None))[0],
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
        self.tools_banner.grid(row=0, column=self._content_col(), sticky="ew", padx=20, pady=(14, 0))

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
                    self.alert_card.configure(fg_color="#2a1416", border_color="#5c2427")
                    self.alert_title_lbl.configure(
                        text=self._L(self._tr("ui.device_is_unauthorized", serials_str=serials_str)),
                        text_color="#ff7b72",
                    )
                    self.alert_desc_lbl.configure(
                        text=self._L(self._tr("alert_unauthorized_desc")),
                        text_color="#f0c6c3",
                    )
                    self.alert_card.pack(fill="x", pady=(0, 12))
                if hasattr(self, "guide_card"):
                    self.guide_card.pack_forget()

                self.adb_status_dot.configure(fg_color=ACCENT_RED)
                self.adb_status_label.configure(
                    text=self._L(self._tr("ui.adb_unauthorized_accept_rsa_prompt", serials_str=serials_str)),
                    text_color="#ff7b72",
                )

            # 2. State: Physical phone detected on Windows USB PnP, but ADB debugging is OFF
            elif diag.get("has_pnp_without_adb") and pnp_phones:
                phone_names = ", ".join(p["name"] for p in pnp_phones)
                if hasattr(self, "alert_card"):
                    self.alert_card.configure(fg_color="#221c10", border_color="#4d3d17")
                    self.alert_title_lbl.configure(
                        text=self._L(self._tr("ui.phone_detected_on_usb_but_usb", phone_names=phone_names)),
                        text_color="#e3b341",
                    )
                    desc_text = self._tr("alert_pnp_desc").replace("{names}", phone_names)
                    self.alert_desc_lbl.configure(
                        text=self._L(desc_text),
                        text_color="#ecdcb3",
                    )
                    self.alert_card.pack(fill="x", pady=(0, 12))
                if hasattr(self, "guide_card"):
                    self.guide_card.pack_forget()

                self.adb_status_dot.configure(fg_color=ACCENT_AMBER)
                self.adb_status_label.configure(
                    text=self._L(self._tr("ui.usb_enable_usb_debugging", phone_names=phone_names)),
                    text_color="#e3b341",
                )

            # 3. State: Device is offline
            elif offline_devs and not authorized_devs and not fastboot_devs:
                serials_str = ", ".join(d["serial"] for d in offline_devs)
                if hasattr(self, "alert_card"):
                    self.alert_card.configure(fg_color="#221c10", border_color="#4d3d17")
                    self.alert_title_lbl.configure(
                        text=self._L(self._tr("ui.device_is_offline", serials_str=serials_str)),
                        text_color="#e3b341",
                    )
                    self.alert_desc_lbl.configure(
                        text=self._L(self._tr("alert_offline_desc")),
                        text_color="#ecdcb3",
                    )
                    self.alert_card.pack(fill="x", pady=(0, 12))
                if hasattr(self, "guide_card"):
                    self.guide_card.pack_forget()

                self.adb_status_dot.configure(fg_color="#d29922")
                self.adb_status_label.configure(
                    text=self._L(self._tr("ui.adb_offline_replug_usb_cable")),
                    text_color="#e3b341",
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
                    text_color="#56d364",
                )
                self.run_profile_async()
            elif cur in ready_serials:
                cur_dev = next(d for d in devices if d["serial"] == cur)
                self.adb_status_dot.configure(fg_color=ACCENT_GREEN)
                self.adb_status_label.configure(
                    text=self._L(self._tr("ui.adb_2", upper=cur_dev['state'].upper(), cur=cur)),
                    text_color="#56d364",
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


