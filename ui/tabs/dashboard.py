"""Dashboard tab: device profile, live telemetry tiles, full deep scan."""

import threading
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from ui.ui_helpers import _fmt
from ui.theme import (
    BG_CANVAS,
    BG_TILE,
    BG_TILE_ALT,
    BORDER_SUBTLE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    ACCENT_BLUE,
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_AMBER,
    ACCENT_RED,
    ACCENT_PURPLE,
    FONT_FAMILY,
    make_hero_header,
    make_card,
    make_button,
    pack_button_grid,
    make_telemetry_tile,
    BG_CARD,
)


class DashboardTabMixin:
    """Methods of AndroidDoctorApp for this tab (state lives on the app)."""

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
            fg_color="#2a1416",
            border_color="#5c2427",
            border_width=1,
            corner_radius=12,
        )
        self.alert_title_lbl = ctk.CTkLabel(
            self.alert_card,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color="#ff7b72",
            anchor=L["anchor"],
            justify=L["justify"],
        )
        self.alert_title_lbl.pack(fill="x", padx=15, pady=(12, 4))

        self.alert_desc_lbl = ctk.CTkLabel(
            self.alert_card,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color="#f0c6c3",
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
            variant="primary",
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
            fg_color=BG_CARD,
            border_color=BORDER_SUBTLE,
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
            text_color=TEXT_PRIMARY,
            anchor=L["anchor"],
            justify=L["justify"],
        ).pack(side=L["lead"], fill="x", expand=True)

        make_button(
            guide_top,
            text=self._L(self._tr("guide_btn_restart_adb")),
            command=self._restart_adb_service,
            variant="secondary",
            height=30,
            font_size=12,
        ).pack(side=L["trail"], padx=4)

        for step_key, step_pad in (("guide_step1", 2), ("guide_step2", 2), ("guide_step3", (2, 12))):
            ctk.CTkLabel(
                self.guide_card,
                text=self._L(self._tr(step_key)),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=TEXT_SECONDARY,
                anchor=L["anchor"],
                justify=L["justify"],
                wraplength=820,
            ).pack(fill="x", padx=18, pady=step_pad)

        # Responsive Quick Actions Toolbar
        _, action_btns = pack_button_grid(
            wrap,
            [
                (self._L(self._tr("btn_full_scan")), self.run_full_deep_scan_async, "primary"),
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
        self.storage_title_lbl, self.storage_bar, _ = _make_gauge_block("ui.internal_storage", ACCENT_BLUE)
        self.batt_title_lbl, self.batt_bar, batt_box = _make_gauge_block("ui.battery", ACCENT_BLUE)

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
                storage_summary=f"{_fmt(profile.storage_free_gb, 'GB')} free",
                ram_summary=f"{_fmt(profile.ram_free_mb, 'MB')} free",
                sensor_summary=f"{len(haudit.sensors)} active sensors",
                security_summary=self._m(sec.summary),
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
                self.spec_labels["cores"].configure(text=self._L(self._tr("ui.cores", cpu_cores=_fmt(profile.cpu_cores), cpu_architecture=profile.cpu_architecture)))
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
                if profile.ram_total_mb and profile.ram_used_pct is not None:
                    pct = profile.ram_used_pct / 100.0
                    self.ram_bar.set(pct)
                    self.ram_title_lbl.configure(text=self._L(self._tr("ui.ram_mb_free_mb", ram_free_mb=profile.ram_free_mb, ram_total_mb=profile.ram_total_mb, ram_used_pct=profile.ram_used_pct)))

                if profile.storage_total_gb and profile.storage_used_pct is not None:
                    pct = profile.storage_used_pct / 100.0
                    self.storage_bar.set(pct)
                    self.storage_title_lbl.configure(text=self._L(self._tr("ui.storage_gb_free_gb", storage_free_gb=profile.storage_free_gb, storage_total_gb=profile.storage_total_gb, storage_used_pct=profile.storage_used_pct)))

                self.batt_bar.set((profile.battery_level_pct or 0) / 100.0)
                self.batt_title_lbl.configure(text=self._L(self._tr("ui.battery_2", battery_level_pct=_fmt(profile.battery_level_pct), battery_status=profile.battery_status)))
                self.batt_quick_lbl.configure(text=self._L(self._tr("ui.voltage_mv_temp_c_health", battery_voltage_mv=_fmt(profile.battery_voltage_mv), battery_temp_c=_fmt(profile.battery_temp_c), battery_health=profile.battery_health)))

            try:
                if self.winfo_exists():
                    self.after(0, update_ui)
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=task, daemon=True).start()
