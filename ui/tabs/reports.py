"""Diagnostic Reports tab: report export (optionally signed) and history."""

import logging
import os
import time
import threading
import webbrowser
from tkinter import filedialog, messagebox
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

        actions = ctk.CTkFrame(btn_row, fg_color="transparent")
        self.btn_export_cert = make_button(
            actions, self._L(self._tr("btn_gen_signed_cert")), self._generate_and_open_certificate, variant="primary", height=36
        )
        self.btn_export_pdf = make_button(
            actions, self._L(self._tr("btn_export_pdf")), self._export_pdf_async, variant="secondary", height=36
        )
        btn_open_fld = make_button(
            actions, self._L(self._tr("btn_open_folder")), lambda: (os.makedirs(REPORTS_DIR, exist_ok=True), webbrowser.open(REPORTS_DIR)), variant="secondary", height=36
        )

        # Row 0: technician name (label on the reading-start side). Row 1: actions, main one first.
        entry_col, label_col = (0, 1) if L["is_rtl"] else (1, 0)
        btn_row.grid_columnconfigure(entry_col, weight=1)
        lbl_tech.grid(row=0, column=label_col, padx=(6, 0) if L["is_rtl"] else (0, 6), sticky=L["anchor"])
        self.tech_name_entry.grid(row=0, column=entry_col, sticky="ew")
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        for b in (self.btn_export_cert, self.btn_export_pdf, btn_open_fld):
            b.pack(side=L["lead"], padx=(0, 8) if L["lead"] == "left" else (8, 0))

        # All connected devices at once (one report each)
        batch_card, _ = make_card(wrap, self._L(self._tr("card_batch_scan")), ACCENT_BLUE, L, pady=(0, 12))
        ctk.CTkLabel(
            batch_card, text=self._L(self._tr("ui.batch_hint")), font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=TEXT_SECONDARY, anchor=L["anchor"], justify=L["justify"], wraplength=1000,
        ).pack(fill="x", padx=16, pady=(0, 8))
        self.btn_batch_scan = make_button(
            batch_card, self._L(self._tr("btn_batch_scan")), self._run_batch_scan_async, variant="secondary", height=36
        )
        self.btn_batch_scan.pack(side="top", anchor=L["anchor"], padx=16, pady=(0, 10))
        self.batch_text = make_console_box(batch_card, 120, L)

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

        self.last_report_path = report_path
        webbrowser.open(f"file:///{os.path.abspath(report_path)}")
        self._refresh_past_reports()

    def _run_batch_scan_async(self):
        """Full diagnostics of every connected device in parallel; one line per device as it finishes."""
        from core.report_signing import KEY_PATH_ENV
        from diagnostics_runner import diagnose_all

        self.btn_batch_scan.configure(state="disabled")
        self._set_box(self.batch_text, self._tr("ui.batch_listing"))
        lang = self.current_lang
        key_path = os.environ.get(KEY_PATH_ENV) or None

        def line(run):
            return self._tr("ui.batch_line", serial=run.serial, model=run.model or "-", state=run.state,
                            status=run.overall, report=os.path.basename(run.report_path) if run.report_path else "-")

        def task():
            try:
                devices = self.bridge.list_devices()
            except Exception as e:
                logger.error(f"Batch scan: listing devices failed: {e}")
                devices = []
            if not devices:
                self.after(0, lambda: (self._set_box(self.batch_text, self._tr("ui.batch_none")),
                                       self.btn_batch_scan.configure(state="normal")))
                return
            self.after(0, lambda: self._set_box(self.batch_text, self._tr("ui.batch_running", count=len(devices)) + "\n"))

            def on_done(run):
                self.after(0, lambda r=run: self._append_box(self.batch_text, line(r) + "\n"))

            # The window's database: it opens a connection per operation, so close() at the end is harmless.
            runs = diagnose_all(self.bridge, devices, lang, lambda: self.db, REPORTS_DIR, key_path, on_done=on_done)
            ok = sum(1 for r in runs if r.report_path)

            def finish():
                self._append_box(self.batch_text, "\n" + self._tr("ui.batch_done", reports=ok, count=len(runs)) + "\n")
                self.btn_batch_scan.configure(state="normal")
                self._refresh_past_reports()
            self.after(0, finish)
        threading.Thread(target=task, daemon=True).start()

    def _export_pdf_async(self):
        """PDF copy of the last report made here, or of one the technician picks."""
        from core.report_signing import KEY_PATH_ENV
        from ui.pdf_export import PdfExportError, export_pdf
        from core.report_signing import SigningError

        html_path = getattr(self, "last_report_path", None)
        if not html_path or not os.path.isfile(html_path):
            os.makedirs(REPORTS_DIR, exist_ok=True)
            html_path = filedialog.askopenfilename(
                title=self._tr("ui.pdf_choose_report"), initialdir=REPORTS_DIR,
                filetypes=[("HTML", "*.html")])
            if not html_path:
                return
        self.btn_export_pdf.configure(state="disabled")
        key_path = os.environ.get(KEY_PATH_ENV) or None

        def task():
            try:
                pdf_path, _ = export_pdf(html_path, key_path)
                error = None
            except (PdfExportError, SigningError) as e:
                pdf_path, error = None, e.msg
            except Exception as e:  # never leave the button disabled
                logger.error(f"PDF export failed: {e}")
                pdf_path, error = None, str(e)

            def done():
                self.btn_export_pdf.configure(state="normal")
                if error is not None:
                    messagebox.showerror(self._tr("ui.notice"), self._tr("cli.pdf_failed", error=self._m(error)))
                    return
                webbrowser.open(f"file:///{os.path.abspath(pdf_path)}")
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

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
