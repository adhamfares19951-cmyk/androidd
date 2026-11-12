"""Security & Permissions tab: sensitive-permission inventory, sockets, debloat/rollback."""

import time
import threading
from tkinter import messagebox
import customtkinter as ctk
from ui.theme import (
    BG_CANVAS,
    BG_CARD,
    BORDER_SUBTLE,
    TEXT_SECONDARY,
    ACCENT_CYAN,
    ACCENT_RED,
    ACCENT_PURPLE,
    FONT_FAMILY,
    make_hero_header,
    make_card,
    make_console_box,
    make_button,
    pack_button_grid,
    style_entry,
)
from ui.i18n import render
from ui.ui_helpers import format_evidence_for_display


class SecurityTabMixin:
    """Methods of AndroidDoctorApp for this tab (state lives on the app)."""

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
            (self._L(self._tr("btn_run_sec")), self.run_security_audit_async, "primary"),
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
            t_lines.append(format_evidence_for_display(scan.evidence, lang=self.current_lang))

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

        ctk.CTkLabel(dialog, text=self._tr("ui.removed_and_disabled_apps_that_can"), font=ctk.CTkFont(size=14, weight="bold"), text_color="#ececee").pack(padx=20, pady=(15, 5))

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

        ctk.CTkButton(restore_bar, text=self._tr("ui.restore_app_now"), command=restore_now, fg_color="#4c8dff").pack(side="left")

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
                    lines.append(f"• [{b.get('vendor')}] {b.get('package_name')} ({b.get('label')})")
                    lines.append(self._tr("ui.description", current_lang=render(b.get('description'), self.current_lang)))
                    lines.append(self._tr("ui.removal_safety", removal_safety=self._m(b.get('safety'))))
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
        if self.last_bloat_scan is None:   # never scanned: scan in the background, then ask again
            self.run_oem_bloat_scan_async()
            return

        if not self.last_bloat_scan:
            messagebox.showinfo(self._tr("ui.nothing_found"), self._tr("ui.no_bloatware_packages_found_to_remove"))
            return

        confirm = messagebox.askyesno(
            self._tr("ui.confirm_safe_debloat"),
            self._tr("ui.safe_debloat_will_disable_oem_bloatware", last_bloat_scan=len(self.last_bloat_scan))
        )
        if not confirm:
            return

        self._set_box(self.bloat_text, self._tr("ui.safely_debloating_oem_packages"))

        def task():
            success_count = 0
            for b in self.last_bloat_scan:
                pkg = b.get("package_name")
                ok, msg = self.malware_hunter.neutralize_app(serial, pkg, action="disable")
                if ok:
                    success_count += 1
                def log_one(p=pkg, s=ok):
                    self._append_box(self.bloat_text, f"{'✓' if s else '✗'} {p}\n")
                self.after(0, log_one)
                time.sleep(0.05)

            def done():
                res_txt = self._tr("ui.successfully_debloated_packages", success_count=success_count, last_bloat_scan=len(self.last_bloat_scan))
                self._append_box(self.bloat_text, res_txt)
                messagebox.showinfo(self._tr("ui.debloat_complete"), res_txt)
            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def run_restore_all_debloated_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        is_ar = self.current_lang == "ar"
        self._set_box(self.bloat_text, self._tr("ui.restoring_debloated_packages"))

        def task():
            history = self.db.get_rollback_history(serial)
            if not history:
                def none_found():
                    self._append_box(self.bloat_text, self._tr("ui.no_neutralized_packages_found_in_rollback"))
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
                    self._append_box(self.bloat_text, self._tr("ui.text_3", failed=self._tr("ui.restored") if s else self._tr("ui.failed"), p=p))
                self.after(0, log_one)
                time.sleep(0.05)

            def done():
                res_txt = self._tr("ui.successfully_restored_packages", restored=restored)
                self._append_box(self.bloat_text, res_txt)
                messagebox.showinfo(self._tr("ui.restore_complete"), res_txt)
            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()
