"""System Maintenance tab."""

import time
import threading
from tkinter import messagebox
import customtkinter as ctk
from ui.theme import (
    BG_CANVAS,
    BG_CARD,
    BORDER_SUBTLE,
    TEXT_SECONDARY,
    ACCENT_BLUE,
    ACCENT_GREEN,
    ACCENT_AMBER,
    FONT_FAMILY,
    make_hero_header,
    make_card,
    make_console_box,
    make_button,
    make_telemetry_tile,
)
from ui.ui_helpers import maintenance_pill_status, _fmt


class OptimizerTabMixin:
    """Methods of AndroidDoctorApp for this tab (state lives on the app)."""

    # =========================================================================
    # TAB 5: SYSTEM OPTIMIZER
    # =========================================================================
    def _build_optimizer_tab(self):
        L = self._layout()
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color=BG_CANVAS)
        self.tab_frames["optimizer"] = tab

        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        make_hero_header(
            wrap,
            self._L(self._tr("opt_title")),
            self._L(self._tr("opt_sub")),
            L,
            accent_color=ACCENT_GREEN,
        )

        # Action & Progress Card
        act_card = ctk.CTkFrame(
            wrap,
            fg_color=BG_CARD,
            corner_radius=12,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        act_card.pack(fill="x", pady=(0, 12))

        top_row = ctk.CTkFrame(act_card, fg_color="transparent")
        top_row.pack(fill="x", padx=14, pady=(12, 8))

        self.btn_run_opt = make_button(
            top_row,
            self._L(self._tr("btn_run_opt")),
            self.run_optimizer_async,
            variant="success",
            height=38,
        )
        self.btn_run_opt.pack(side=L["lead"])

        self.opt_progress = ctk.CTkProgressBar(
            act_card, height=11, corner_radius=6, fg_color=BORDER_SUBTLE, progress_color=ACCENT_GREEN
        )
        self.opt_progress.set(0)
        self.opt_progress.pack(fill="x", padx=14, pady=(2, 6))

        self.opt_status_lbl = ctk.CTkLabel(
            act_card,
            text=self._L(self._tr("ui.optimizer_idle")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_SECONDARY,
            anchor=L["anchor"],
            justify=L["justify"],
        )
        self.opt_status_lbl.pack(fill="x", padx=14, pady=(0, 12))

        # Before & After Comparison Cards
        comp_grid = ctk.CTkFrame(wrap, fg_color="transparent")
        comp_grid.pack(fill="x", pady=(0, 12))
        comp_grid.grid_columnconfigure((0, 1, 2), weight=1, uniform="opt_kpi")

        o_cols = (2, 1, 0) if L["is_rtl"] else (0, 1, 2)
        self.card_ram_gain, self.lbl_ram_gain = make_telemetry_tile(
            comp_grid, self._L(self._tr("gain_ram")), self._tr("ui.0_0_mb"), ACCENT_BLUE, L
        )
        self.card_ram_gain.grid(row=0, column=o_cols[0], padx=4, sticky="nsew")

        self.card_storage_gain, self.lbl_storage_gain = make_telemetry_tile(
            comp_grid, self._L(self._tr("gain_storage")), self._tr("ui.0_0_mb"), ACCENT_GREEN, L
        )
        self.card_storage_gain.grid(row=0, column=o_cols[1], padx=4, sticky="nsew")

        self.card_drain_gain, self.lbl_drain_gain = make_telemetry_tile(
            comp_grid, self._L(self._tr("gain_drain")), self._tr("ui.0_0_ma"), ACCENT_AMBER, L
        )
        self.card_drain_gain.grid(row=0, column=o_cols[2], padx=4, sticky="nsew")

        # Live Execution Terminal
        self.opt_log_card, badge = make_card(
            wrap, self._L(self._tr("card_opt_log")), ACCENT_GREEN, L, show_badge=True, pady=(0, 0)
        )
        self.status_pills["maint"] = badge
        self.opt_log_text = make_console_box(self.opt_log_card, 260, L)

    def run_optimizer_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        self._ensure_tab("optimizer")
        self.opt_progress.set(0)
        self._set_box(self.opt_log_text, "")

        def progress(pct, msg):
            def upd():
                self._ensure_tab("optimizer")
                self.opt_progress.set(pct / 100.0)
                self.opt_status_lbl.configure(text=self._L(self._m(msg)))
                self._append_box(self.opt_log_text, f"[{time.strftime('%H:%M:%S')}] {msg}\n")
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
                self._ensure_tab("optimizer")
                self.lbl_ram_gain.configure(text=f"{_fmt(res.before.ram_free_mb)} -> {_fmt(res.after.ram_free_mb, 'MB')}")
                self.lbl_storage_gain.configure(text=_fmt(res.storage_freed_mb, "MB"))
                self.lbl_drain_gain.configure(text="—")
                self._append_box(self.opt_log_text, "\n" + "\n".join(lines) + "\n")
                self._set_pill("maint", maintenance_pill_status(res.actions))
                messagebox.showinfo(self._tr("ui.maintenance_finished"),
                                    (self._tr("ui.measured_change_in_free_storage_after", mb=_fmt(res.storage_freed_mb, 'MB'))))
            self.after(0, done)

        threading.Thread(target=task, daemon=True).start()
