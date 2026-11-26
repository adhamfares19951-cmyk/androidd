"""
IMEI Validation and Blacklist Lookup Mixin (ui.tabs.imei_check).

Provides technician and dealer tools to inspect used phone identity:
1. Free, offline validation of the 15-digit IMEI format and Luhn checksum.
2. Best-effort reading from connected phone via ADB telephony service.
3. Bring-Your-Own-Key (BYOK) lost/stolen blacklist lookup via third-party APIs
   (e.g., SickW, DHRU Fusion), strictly recording real evidence and never inventing clean verdicts.
"""

import json
import logging
import threading
from tkinter import messagebox
from typing import Optional

import customtkinter as ctk

from core.imei_check import (
    BlacklistConfig,
    ImeiCheckResult,
    check_blacklist,
    normalize_imei,
    read_imei,
    validate_imei,
)
from core.result import Status
from ui.i18n import render
from ui.theme import (
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_RED,
    BORDER_ACCENT,
    BG_CARD,
    BG_INPUT,
    FONT_FAMILY,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    make_button,
    make_card,
    make_console_box,
    style_entry,
    update_status_badge,
)

logger = logging.getLogger("ImeiCheckUI")


class ImeiCheckMixin:
    """Methods of AndroidDoctorApp for the IMEI validation and blacklist card."""

    def _build_imei_check_card(self, wrap) -> None:
        L = self._layout()
        small = ctk.CTkFont(family=FONT_FAMILY, size=11)

        self.card_imei_check, badge = make_card(
            wrap, self._L(self._tr("card_imei_check")), ACCENT_CYAN, L, show_badge=True, pady=(0, 12)
        )
        self.status_pills["imei"] = badge

        # Row 1: IMEI input + read from phone + offline format check
        row1 = ctk.CTkFrame(self.card_imei_check, fg_color="transparent")
        row1.pack(fill="x", padx=14, pady=(0, 6))

        lbl_num = ctk.CTkLabel(
            row1, text=self._L(self._tr("lbl_imei_number")), font=small, text_color=TEXT_SECONDARY
        )
        lbl_num.pack(side=L["lead"], padx=(0, 6))

        self.entry_imei = ctk.CTkEntry(row1, placeholder_text="35xxxxxxxxxxxxx", height=32, width=175)
        style_entry(self.entry_imei, L)
        self.entry_imei.pack(side=L["lead"], padx=(0, 6))

        self.btn_imei_read = make_button(
            row1, self._L(self._tr("btn_imei_read")), self.read_imei_from_device_async, variant="secondary", height=32
        )
        self.btn_imei_read.pack(side=L["lead"], padx=(0, 6))

        self.btn_imei_validate = make_button(
            row1, self._L(self._tr("btn_imei_validate")), self.validate_imei_action, variant="primary", height=32
        )
        self.btn_imei_validate.pack(side=L["lead"], padx=(0, 6))

        # Row 2: Blacklist lookup (BYOK) + Settings + Status
        row2 = ctk.CTkFrame(self.card_imei_check, fg_color="transparent")
        row2.pack(fill="x", padx=14, pady=(0, 8))

        self.btn_imei_blacklist = make_button(
            row2, self._L(self._tr("btn_imei_blacklist")), self.check_imei_blacklist_async, variant="cyan", height=32
        )
        self.btn_imei_blacklist.pack(side=L["lead"], padx=(0, 6))

        self.btn_imei_byok = make_button(
            row2, self._L(self._tr("btn_imei_byok")), self.open_byok_config_dialog, variant="secondary", height=32
        )
        self.btn_imei_byok.pack(side=L["lead"], padx=(0, 8))

        self.lbl_imei_byok_status = ctk.CTkLabel(
            row2, text="", font=small, text_color=TEXT_MUTED
        )
        self.lbl_imei_byok_status.pack(side=L["lead"], padx=4)

        # Output console
        self.imei_text = make_console_box(
            self.card_imei_check, 130, L, self._L(self._tr("ui.imei_hint"))
        )

        self._update_byok_status_label()

    # ---------------------------------------------------------------- BYOK Config
    def _load_byok_config(self) -> BlacklistConfig:
        default = BlacklistConfig()
        if not hasattr(self, "db") or not self.db:
            return default
        raw = self.db.get_setting("imei_byok_config")
        if not raw:
            return default
        try:
            d = json.loads(raw)
            return BlacklistConfig(
                endpoint=d.get("endpoint", ""),
                api_key=d.get("api_key", ""),
                service_id=d.get("service_id", ""),
                provider=d.get("provider", "generic"),
            )
        except Exception:
            return default

    def _save_byok_config(self, cfg: BlacklistConfig) -> None:
        if not hasattr(self, "db") or not self.db:
            return
        data = {
            "endpoint": cfg.endpoint,
            "api_key": cfg.api_key,
            "service_id": cfg.service_id,
            "provider": cfg.provider,
        }
        self.db.set_setting("imei_byok_config", json.dumps(data))
        self._update_byok_status_label()

    def _update_byok_status_label(self) -> None:
        if not hasattr(self, "lbl_imei_byok_status") or not self.lbl_imei_byok_status.winfo_exists():
            return
        cfg = self._load_byok_config()
        if cfg.endpoint and cfg.api_key:
            endpoint_display = cfg.endpoint.split("//")[-1].split("/")[0]
            self.lbl_imei_byok_status.configure(
                text=self._tr("ui.imei_byok_status_on", endpoint=endpoint_display),
                text_color=ACCENT_GREEN,
            )
        else:
            self.lbl_imei_byok_status.configure(
                text=self._tr("ui.imei_byok_status_off"),
                text_color=TEXT_MUTED,
            )

    def open_byok_config_dialog(self) -> None:
        L = self._layout()
        small = ctk.CTkFont(family=FONT_FAMILY, size=11)
        dialog = ctk.CTkToplevel(self)
        dialog.title(self._tr("ui.imei_byok_title"))
        dialog.geometry("480x320")
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        cfg = self._load_byok_config()

        # Endpoint URL
        ctk.CTkLabel(dialog, text=self._L(self._tr("ui.imei_byok_endpoint")), font=small, text_color=TEXT_SECONDARY, anchor=L["anchor"]).pack(fill="x", padx=20, pady=(16, 2))
        entry_ep = ctk.CTkEntry(dialog, height=32)
        style_entry(entry_ep, L)
        entry_ep.insert(0, cfg.endpoint)
        entry_ep.pack(fill="x", padx=20, pady=(0, 8))

        # API Key
        ctk.CTkLabel(dialog, text=self._L(self._tr("ui.imei_byok_key")), font=small, text_color=TEXT_SECONDARY, anchor=L["anchor"]).pack(fill="x", padx=20, pady=(0, 2))
        entry_key = ctk.CTkEntry(dialog, height=32, show="*")
        style_entry(entry_key, L)
        entry_key.insert(0, cfg.api_key)
        entry_key.pack(fill="x", padx=20, pady=(0, 8))

        # Service ID
        ctk.CTkLabel(dialog, text=self._L(self._tr("ui.imei_byok_service_id")), font=small, text_color=TEXT_SECONDARY, anchor=L["anchor"]).pack(fill="x", padx=20, pady=(0, 2))
        entry_svc = ctk.CTkEntry(dialog, height=32)
        style_entry(entry_svc, L)
        entry_svc.insert(0, cfg.service_id)
        entry_svc.pack(fill="x", padx=20, pady=(0, 16))

        btn_row = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(0, 12))

        def on_save():
            new_cfg = BlacklistConfig(
                endpoint=entry_ep.get().strip(),
                api_key=entry_key.get().strip(),
                service_id=entry_svc.get().strip(),
            )
            self._save_byok_config(new_cfg)
            dialog.destroy()
            messagebox.showinfo(self._tr("ui.notice"), self._tr("ui.imei_byok_saved"))

        make_button(btn_row, self._L(self._tr("btn_save")), on_save, variant="primary", height=32).pack(side=L["lead"], padx=(0, 8))
        make_button(btn_row, self._L(self._tr("btn_cancel")), dialog.destroy, variant="secondary", height=32).pack(side=L["lead"])

    # ---------------------------------------------------------------- Device Read & Offline Check
    def read_imei_from_device_async(self) -> None:
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        self._set_box(self.imei_text, self._tr("ui.imei_reading"))

        def task():
            imei = read_imei(self.bridge, serial)

            def done():
                if imei:
                    self.entry_imei.delete(0, "end")
                    self.entry_imei.insert(0, imei)
                    self._set_box(self.imei_text, self._tr("ui.imei_read_success", imei=imei))
                    self.validate_imei_action()
                else:
                    self._set_box(self.imei_text, self._tr("ui.imei_read_failed"))

            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def validate_imei_action(self) -> None:
        raw = self.entry_imei.get().strip()
        result = validate_imei(raw)

        pill = self.status_pills.get("imei")
        if pill:
            update_status_badge(pill, "PASS" if result.status == Status.PASS else "FAIL")

        rendered = self._render_imei_result(result)
        self._set_box(self.imei_text, rendered)

    # ---------------------------------------------------------------- Blacklist Lookup
    def check_imei_blacklist_async(self) -> None:
        raw = self.entry_imei.get().strip()
        if not raw:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("lbl_imei_number"))
            return

        cfg = self._load_byok_config()
        self._set_box(self.imei_text, self._tr("ui.imei_checking"))

        def task():
            result = check_blacklist(raw, config=cfg)

            def done():
                pill = self.status_pills.get("imei")
                if pill:
                    st_map = {
                        Status.PASS: "PASS",
                        Status.FAIL: "FAIL",
                        Status.UNKNOWN: "UNKNOWN",
                        Status.NEEDS_ROOT: "ROOT",
                    }
                    update_status_badge(pill, st_map.get(result.status, "UNKNOWN"))

                rendered = self._render_imei_result(result, cfg=cfg)
                self._set_box(self.imei_text, rendered)

            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()

    def _render_imei_result(self, res: ImeiCheckResult, cfg: Optional[BlacklistConfig] = None) -> str:
        lines = []
        status_sym = "[PASS]" if res.status == Status.PASS else ("[FAIL]" if res.status == Status.FAIL else "[UNKNOWN]")
        lines.append(f"=== IMEI Audit: {res.imei or 'N/A'} {status_sym} ===")

        # Format & Checksum
        chk_line = "Valid 15-digit GSM format & Luhn checksum OK." if res.valid_format else "Format or Luhn checksum MISMATCH."
        lines.append(f"Format: {chk_line}")

        # Verdict
        if res.detail:
            lines.append(f"Detail: {render(res.detail, self.current_lang)}")

        # Blacklist Status
        if res.blacklist == "clean":
            lines.append("Blacklist Status: CLEAN (Not reported lost/stolen)")
        elif res.blacklist == "blacklisted":
            lines.append("Blacklist Status: BLOCKED / REPORTED LOST OR STOLEN")
        else:
            lines.append("Blacklist Status: UNKNOWN (Requires active BYOK API lookup)")

        # Evidence excerpt if any
        if res.evidence:
            lines.append("-" * 60)
            lines.append("Lookup Evidence (Raw Response Excerpt):")
            for ev in res.evidence:
                lines.append(f"[{ev.source}]: {ev.raw_excerpt}")

        return "\n".join(lines)
