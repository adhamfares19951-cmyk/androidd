"""Diagnostic Reports tab: report export (optionally signed) and history."""

import logging
import os
import time
import webbrowser
from tkinter import messagebox
import customtkinter as ctk
from ui.theme import (
    BG_CANVAS,
    TEXT_SECONDARY,
    ACCENT_BLUE,
    ACCENT_CYAN,
    FONT_FAMILY,
    make_hero_header,
    make_card,
    make_console_box,
    make_button,
    style_entry,
)
from ui.report_exporter import DiagnosticReportExporter, REPORTS_DIR

logger = logging.getLogger("MainWindow")


class ReportsTabMixin:
    """Methods of AndroidDoctorApp for this tab (state lives on the app)."""

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
        leakage = getattr(self, "last_leakage", None)
        leakage_dict = leakage.to_dict() if leakage is not None else {}

        from core.report_signing import SigningError, KEY_PATH_ENV
        try:
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
                leakage_result=leakage_dict,
                signing_key_path=os.environ.get(KEY_PATH_ENV) or None,
                technician_name=tech,
                ticket_id=ticket_id,
                customer_name=c_name,
                customer_phone=c_phone,
                reported_fault=c_fault,
                repair_cost=c_cost,
                lang=self.current_lang
            )
        except SigningError as e:
            messagebox.showerror(self._tr("ui.notice"), self._tr("cli.signing_failed", error=self._m(e.msg)))
            return

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
