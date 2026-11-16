"""Motherboard & ICs tab: battery/PMIC, thermal, kernel log, storage, stress, BootROM, root forensics."""

import logging
import os
import time
import webbrowser
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from ui.theme import (
    BG_CANVAS,
    BORDER_SUBTLE,
    TEXT_SECONDARY,
    ACCENT_BLUE,
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_AMBER,
    ACCENT_RED,
    ACCENT_PURPLE,
    ACCENT_SLATE,
    FONT_FAMILY,
    make_hero_header,
    make_card,
    make_console_box,
    make_button,
    pack_button_grid,
    make_telemetry_tile,
)
from core.board_diag import StressTestSnapshot

logger = logging.getLogger("MainWindow")
from ui.ui_helpers import _tri_key, bootrom_pill_status, _fmt, format_evidence_for_display, format_log_analysis


class BoardDiagTabMixin:
    """Methods of AndroidDoctorApp for this tab (state lives on the app)."""

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
            (self._L(self._tr("btn_leakage")), self.run_leakage_test_async, "secondary"),
            (self._L(self._tr("btn_fast_charge")), self.run_fast_charge_diag_async, "success"),
            (self._L(self._tr("btn_reset_gauge")), self.run_reset_gauge_async, "secondary"),
            (self._L(self._tr("btn_thermals")), self.run_thermal_scan_async, "secondary"),
            (self._L(self._tr("btn_panics")), self.run_panic_scan_async, "secondary"),
            (self._L(self._tr("btn_scan_bootrom")), self.run_bootrom_scan_async, "purple"),
            (self._L(self._tr("btn_live_telemetry")), self._toggle_live_telemetry, "cyan"),
            (self._L(self._tr("btn_nand_lifespan")), self.run_nand_lifespan_async, "purple"),
            (self._L(self._tr("btn_speed_bench")), self.run_speed_bench_async, "cyan"),
            (self._L(self._tr("btn_stress_test")), self.run_stress_test_async, "secondary"),
            (self._L(self._tr("btn_run_blackbox")), self.run_blackbox_forensics_async, "secondary"),
            (self._L(self._tr("btn_run_root_forensics")), self.run_root_forensics_async, "purple"),
            (self._L(self._tr("btn_backup_efs_raw")), self.run_efs_backup_async, "success"),
            (self._L(self._tr("btn_analyze_log")), self.run_log_analysis_async, "cyan"),
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
            self.btn_analyze_log,
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
            self.stress_card, height=10, corner_radius=5, fg_color=BORDER_SUBTLE, progress_color=ACCENT_BLUE
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

        # Offline log analysis card (a saved UART / dmesg / last_kmsg / logcat file; no phone needed)
        self.logscan_card, badge = make_card(
            wrap, self._L(self._tr("card_logscan")), ACCENT_CYAN, L, show_badge=True
        )
        self.status_pills["logscan"] = badge
        self.logscan_text = make_console_box(
            self.logscan_card, 150, L, self._L(self._tr("ui.logscan_hint"))
        )

        # Leakage Test Live Output Card
        self.leakage_card, badge = make_card(
            wrap, self._L(self._tr("card_leakage")), ACCENT_AMBER, L, show_badge=True
        )
        self.status_pills["leakage"] = badge
        self.leakage_progress = ctk.CTkProgressBar(
            self.leakage_card, height=10, corner_radius=5, fg_color=BORDER_SUBTLE, progress_color=ACCENT_BLUE
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

    def run_pmic_diag_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        def task():
            pmic = self.board_diag.analyze_pmic(serial)
            is_ar = self.current_lang == "ar"

            v_display = f"{pmic.voltage_mv:.0f} mV ({pmic.voltage_mv / 1000.0:.2f} V) [{pmic.voltage_source}]" if (pmic.voltage_mv is not None and pmic.voltage_mv > 0) else pmic.voltage_status
            c_note = f" ({pmic.current_note})" if pmic.current_note else ""
            c_display = f"{pmic.current_ma:+.0f} mA [{pmic.current_source}]{c_note}" if pmic.current_ma is not None else f"{pmic.current_status}{c_note}"
            t_display = f"{pmic.temp_c:.1f} °C [{pmic.temp_source}]" if pmic.temp_c is not None else pmic.temp_status
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
                self._tr("ui.charge_cycle_count", unknown=pmic.cycle_count if pmic.cycle_count is not None else (pmic.cycle_status or 'UNKNOWN')),
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
                out_lines.append(format_evidence_for_display(pmic.evidence, lang=self.current_lang))

            def update_ui():
                self._ensure_tab("board_diag")
                self._set_box(self.pmic_text, "\n".join(out_lines))
                self._set_pill("pmic", getattr(pmic, "check_status", "UNKNOWN"))

            try:
                if self.winfo_exists():
                    self.after(0, update_ui)
            except (RuntimeError, tk.TclError):
                pass

        threading.Thread(target=task, daemon=True).start()

    def run_log_analysis_async(self):
        """Pick a saved log file and check it against the fault signatures (no phone, no root)."""
        path = filedialog.askopenfilename(
            title=self._tr("btn_analyze_log"),
            filetypes=[(self._tr("ui.log_files"), "*.log *.txt *.kmsg"), (self._tr("ui.all_files"), "*.*")],
        )
        if not path:
            return
        self._ensure_tab("board_diag")
        self._set_box(self.logscan_text, self._tr("ui.logscan_reading", name=os.path.basename(path)))
        lang = self.current_lang

        def task():
            from core import log_analyzer
            try:
                result = log_analyzer.analyze_file(path)
                lines, status = format_log_analysis(result, lang), result.status
            except OSError as e:
                lines, status = [self._tr("cli.logscan_unreadable_file", error=str(e))], "UNKNOWN"

            def upd():
                self._ensure_tab("board_diag")
                self._set_box(self.logscan_text, "\n".join(lines))
                self._set_pill("logscan", status)
            self.after(0, upd)

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
                lines.append(format_evidence_for_display(res.evidence, lang=self.current_lang))
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
                if not z.plausible:
                    status = self._tr("report.zone_implausible")
                elif z.limit_c is None:
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
                lines.append(format_evidence_for_display(result.evidence, lang=self.current_lang))

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
                    try:
                        if not self.winfo_exists():
                            return
                    except Exception:
                        return
                    for name in ("lbl_telem_volt", "lbl_telem_curr", "lbl_telem_batt_temp", "lbl_telem_cpu_temp"):
                        w = getattr(self, name, None)
                        if w is not None and not w.winfo_exists():
                            return
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
                lines.append(format_evidence_for_display(rep.evidence, lang=self.current_lang))

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
