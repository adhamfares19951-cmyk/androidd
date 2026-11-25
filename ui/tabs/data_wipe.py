"""
Data Erasure Certificate panel (ui.tabs.data_wipe), on top of core.data_wipe.

Four-step verified workflow:
1. Assess Encryption: verify whether userdata encryption (FBE/FDE) is confirmed active on the phone.
2. Initiate Factory Reset: destructive procedure requiring explicit confirmation dialog.
3. Post-Wipe Verification: read back user_setup_complete == 0 from the fresh setup wizard.
4. Issue Signed Certificate: create verifiable JSON + printable HTML certificate with QR summary and optional Ed25519 signature.
"""

import logging
import os
import threading
import webbrowser
from tkinter import messagebox
import customtkinter as ctk

from core.data_wipe import (
    assess_wipe_readiness,
    build_certificate,
    initiate_factory_reset,
    verify_post_wipe,
    write_certificate,
    WipeAssessment,
    WipeCertificate,
    VERDICT_CRYPTO,
)
from core.report_signing import KEY_PATH_ENV, load_private_key
from ui.report_exporter import REPORTS_DIR
from ui.theme import (
    ACCENT_CYAN,
    FONT_FAMILY,
    TEXT_SECONDARY,
    TEXT_PRIMARY,
    make_card,
    make_console_box,
    make_button,
    style_entry,
)

logger = logging.getLogger("DataWipeUI")


class DataWipeMixin:
    """Methods of AndroidDoctorApp for the Data Erasure Certificate card."""

    def _build_data_wipe_card(self, wrap) -> None:
        L = self._layout()
        small = ctk.CTkFont(family=FONT_FAMILY, size=11)

        self.data_wipe_card, badge = make_card(
            wrap, self._L(self._tr("card_data_wipe")), ACCENT_CYAN, L, show_badge=True, pady=(0, 12)
        )
        self.status_pills["wipe"] = badge

        # Action Buttons Rows (2 rows of 3 to comfortably fit 790px budget)
        btn_bar1 = ctk.CTkFrame(self.data_wipe_card, fg_color="transparent")
        btn_bar1.pack(fill="x", padx=14, pady=(0, 6))

        self.btn_wipe_assess = make_button(
            btn_bar1, self._L(self._tr("btn_wipe_assess")), self.run_wipe_assess_async, variant="primary", height=32
        )
        self.btn_wipe_assess.pack(side=L["lead"], padx=(0, 6))

        self.btn_wipe_reset = make_button(
            btn_bar1, self._L(self._tr("btn_wipe_reset")), self.initiate_wipe_reset_async, variant="warning", height=32
        )
        self.btn_wipe_reset.pack(side=L["lead"], padx=4)

        self.btn_wipe_verify = make_button(
            btn_bar1, self._L(self._tr("btn_wipe_verify")), self.run_wipe_verify_async, variant="cyan", height=32
        )
        self.btn_wipe_verify.pack(side=L["lead"], padx=4)

        btn_bar2 = ctk.CTkFrame(self.data_wipe_card, fg_color="transparent")
        btn_bar2.pack(fill="x", padx=14, pady=(0, 8))

        self.btn_wipe_issue = make_button(
            btn_bar2, self._L(self._tr("btn_wipe_issue_cert")), self.issue_wipe_certificate_async, variant="success", height=32
        )
        self.btn_wipe_issue.pack(side=L["lead"], padx=(0, 6))

        self.btn_wipe_print = make_button(
            btn_bar2, self._L(self._tr("btn_wipe_print")), self.print_wipe_certificate, variant="secondary", height=32
        )
        self.btn_wipe_print.pack(side=L["lead"], padx=4)

        self.btn_wipe_folder = make_button(
            btn_bar2,
            self._L(self._tr("btn_wipe_open_folder")),
            lambda: (os.makedirs(REPORTS_DIR, exist_ok=True), webbrowser.open(REPORTS_DIR)),
            variant="secondary",
            height=32,
        )
        self.btn_wipe_folder.pack(side=L["lead"], padx=4)

        # Form Inputs Grid (Operator, Shop, Notes)
        form_grid = ctk.CTkFrame(self.data_wipe_card, fg_color="transparent")
        form_grid.pack(fill="x", padx=14, pady=(0, 10))
        form_grid.grid_columnconfigure((0, 1, 2), weight=1, uniform="wipe_form")
        cols = (2, 1, 0) if L["is_rtl"] else (0, 1, 2)

        # Column 0: Operator
        ctk.CTkLabel(
            form_grid, text=self._L(self._tr("lbl_wipe_operator")), font=small, text_color=TEXT_SECONDARY, anchor=L["anchor"]
        ).grid(row=0, column=cols[0], padx=4, pady=1, sticky="ew")
        self.wipe_operator_entry = ctk.CTkEntry(form_grid, height=30)
        style_entry(self.wipe_operator_entry, L)
        self.wipe_operator_entry.grid(row=1, column=cols[0], padx=4, pady=(0, 6), sticky="ew")

        # Column 1: Shop
        ctk.CTkLabel(
            form_grid, text=self._L(self._tr("lbl_wipe_shop")), font=small, text_color=TEXT_SECONDARY, anchor=L["anchor"]
        ).grid(row=0, column=cols[1], padx=4, pady=1, sticky="ew")
        self.wipe_shop_entry = ctk.CTkEntry(form_grid, height=30)
        style_entry(self.wipe_shop_entry, L)
        self.wipe_shop_entry.grid(row=1, column=cols[1], padx=4, pady=(0, 6), sticky="ew")

        # Column 2: Notes
        ctk.CTkLabel(
            form_grid, text=self._L(self._tr("lbl_wipe_notes")), font=small, text_color=TEXT_SECONDARY, anchor=L["anchor"]
        ).grid(row=0, column=cols[2], padx=4, pady=1, sticky="ew")
        self.wipe_notes_entry = ctk.CTkEntry(form_grid, height=30)
        style_entry(self.wipe_notes_entry, L)
        self.wipe_notes_entry.grid(row=1, column=cols[2], padx=4, pady=(0, 6), sticky="ew")

        # Output console
        self.wipe_text = make_console_box(self.data_wipe_card, 160, L, self._L(self._tr("ui.wipe_hint")))
        self.last_wipe_assessment = None
        self.last_wipe_certificate = None
        self.last_wipe_cert_files = None
        self.last_wipe_verified = False

    # ---------------------------------------------------------------- Step 1: Assess
    def run_wipe_assess_async(self) -> None:
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        self._set_box(self.wipe_text, self._tr("ui.wipe_assessing"))

        def task():
            try:
                assessment = assess_wipe_readiness(self.bridge, serial)
            except Exception as e:
                logger.error(f"Wipe assessment error: {e}")
                self.after(0, lambda: self._set_box(self.wipe_text, f"Error: {e}"))
                return

            self.last_wipe_assessment = assessment

            def done():
                lines = [
                    f"[{assessment.status}] {self._m(assessment.detail)}",
                    f"  • Brand & Model: {assessment.device.get('brand', '')} {assessment.device.get('model', '')}",
                    f"  • Serial: {assessment.device.get('serial', '')}",
                    f"  • Android: {assessment.device.get('android_version', '-')} (Build: {assessment.device.get('build_id', '-')})",
                    f"  • Encryption: {assessment.crypto_type} (Active: {assessment.encrypted})",
                ]
                self._set_box(self.wipe_text, "\n".join(lines))
                self._set_pill("wipe", str(assessment.status))

            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    # ---------------------------------------------------------------- Step 2: Reset
    def initiate_wipe_reset_async(self) -> None:
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        confirm = messagebox.askyesno(
            self._tr("ui.wipe_confirm_title"),
            self._tr("ui.wipe_confirm_msg"),
        )
        if not confirm:
            return

        def task():
            try:
                res = initiate_factory_reset(self.bridge, serial, confirm=True)
            except Exception as e:
                logger.error(f"Wipe initiation error: {e}")
                self.after(0, lambda: self._set_box(self.wipe_text, f"Error: {e}"))
                return

            self.last_wipe_reset_result = res

            def done():
                lines = [
                    self._m(res["message"]),
                    "",
                    "Next step: Wait for the phone to reboot and display the Setup Wizard, then click '3. Verify Post-Wipe State'.",
                ]
                self._set_box(self.wipe_text, "\n".join(lines))

            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    # ---------------------------------------------------------------- Step 3: Verify
    def run_wipe_verify_async(self) -> None:
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        def task():
            try:
                verified = verify_post_wipe(self.bridge, serial)
            except Exception as e:
                logger.error(f"Wipe verification error: {e}")
                verified = False

            self.last_wipe_verified = verified

            def done():
                if verified:
                    self._set_box(self.wipe_text, self._tr("ui.wipe_verified_success"))
                    self._set_pill("wipe", "PASS")
                else:
                    self._set_box(self.wipe_text, self._tr("ui.wipe_verified_pending"))

            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    # ---------------------------------------------------------------- Step 4: Issue Cert
    def issue_wipe_certificate_async(self) -> None:
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        if self.last_wipe_assessment is None:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.wipe_need_assessment"))
            return

        operator = self.wipe_operator_entry.get().strip()
        if not operator and hasattr(self, "tech_name_entry"):
            operator = self.tech_name_entry.get().strip()

        shop = self.wipe_shop_entry.get().strip()
        notes = self.wipe_notes_entry.get().strip()
        verified = getattr(self, "last_wipe_verified", False)
        reset_res = getattr(self, "last_wipe_reset_result", {}) or {}
        method = reset_res.get("method") or "factory_reset"

        def task():
            try:
                cert = build_certificate(
                    self.last_wipe_assessment,
                    method=method,
                    post_wipe_verified=verified,
                    operator=operator,
                    shop=shop,
                    notes=notes,
                )
                key_path = os.environ.get(KEY_PATH_ENV) or None
                signing_key = None
                if key_path:
                    try:
                        signing_key = load_private_key(key_path)
                    except Exception as e:
                        logger.warning(f"Signing key load failed: {e}")

                out = write_certificate(cert, out_dir=REPORTS_DIR, signing_key=signing_key)
                self.last_wipe_certificate = cert
                self.last_wipe_cert_files = out
            except Exception as e:
                logger.error(f"Certificate issuance failed: {e}")
                self.after(0, lambda: self._set_box(self.wipe_text, f"Issuance error: {e}"))
                return

            def done():
                lines = [
                    self._tr("ui.wipe_cert_issued", cert_id=cert.cert_id, has_key=str(out["signed"]), path=out["json_path"]),
                    f"HTML View: {out.get('html_path', '-')}",
                    f"Verdict: {cert.verdict}",
                    f"Post-Wipe Verified: {cert.post_wipe_verified}",
                ]
                if out.get("qr_text"):
                    lines.append(f"QR Summary: {out['qr_text'][:70]}...")
                self._set_box(self.wipe_text, "\n".join(lines))

            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    # ---------------------------------------------------------------- Print / Export
    def print_wipe_certificate(self) -> None:
        if not self.last_wipe_cert_files or not self.last_wipe_cert_files.get("html_path"):
            messagebox.showinfo(self._tr("ui.notice"), self._tr("ui.wipe_need_assessment"))
            return
        html_path = self.last_wipe_cert_files["html_path"]
        try:
            from ui.pdf_export import html_to_pdf, default_pdf_path
            pdf_path = default_pdf_path(html_path)
            html_to_pdf(html_path, pdf_path)
            webbrowser.open(os.path.abspath(pdf_path))
        except Exception:
            webbrowser.open("file:///" + os.path.abspath(html_path).replace("\\", "/"))
