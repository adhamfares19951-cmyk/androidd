"""
Live Charging Curve Mixin (ui.tabs.charging_curve), on top of core.charging_curve.

Captures second-by-second voltage (mV), current (mA), power (W) and battery
temperature (°C) using MotherboardDiagnostics.analyze_pmic without fabricating
any missing data. Renders a live dual-axis plot on a CTkCanvas / tk.Canvas and
allows CSV export for warranty/diagnostic logs.
"""

import csv
import logging
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import List, Optional

import customtkinter as ctk

from core.charging_curve import ChargingCurve, ChargingCurveCapture, ChargingSample
from core.result import Status
from ui.theme import (
    ACCENT_AMBER,
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_RED,
    BORDER_SUBTLE,
    FONT_FAMILY,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    make_button,
    make_card,
    make_console_box,
    update_status_badge,
)

logger = logging.getLogger("ChargingCurveUI")


class ChargingCurveMixin:
    """Methods of AndroidDoctorApp for the Live Charging Curve card."""

    def _build_charging_curve_card(self, wrap) -> None:
        L = self._layout()
        small = ctk.CTkFont(family=FONT_FAMILY, size=11)
        bold_small = ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold")

        # Internal state initialized first
        self.cc_capturing = False
        self._cc_stop_requested = False
        self.last_charge_curve: Optional[ChargingCurve] = None
        self.cc_samples: List[ChargingSample] = []
        self._cc_requested_duration = 60.0

        self.charging_curve_card, badge = make_card(
            wrap, self._L(self._tr("card_charge_curve")), ACCENT_CYAN, L, show_badge=True, pady=(0, 12)
        )
        self.status_pills["charge_curve"] = badge

        # Controls Bar: Duration selector, Start, Stop, Export CSV
        ctrl_bar = ctk.CTkFrame(self.charging_curve_card, fg_color="transparent")
        ctrl_bar.pack(fill="x", padx=14, pady=(0, 8))

        lbl_dur = ctk.CTkLabel(
            ctrl_bar, text=self._L(self._tr("lbl_charge_curve_duration")), font=small, text_color=TEXT_SECONDARY
        )
        lbl_dur.pack(side=L["lead"], padx=(0, 6))

        self.combo_cc_dur = ctk.CTkComboBox(
            ctrl_bar,
            values=["30s", "60s", "120s"],
            width=85,
            height=32,
            state="readonly",
        )
        self.combo_cc_dur.set("60s")
        self.combo_cc_dur.pack(side=L["lead"], padx=(0, 8))

        self.btn_cc_start = make_button(
            ctrl_bar, self._L(self._tr("btn_charge_curve_start")), self.start_charge_curve_async, variant="primary", height=32
        )
        self.btn_cc_start.pack(side=L["lead"], padx=(0, 6))

        self.btn_cc_stop = make_button(
            ctrl_bar, self._L(self._tr("btn_charge_curve_stop")), self.stop_charge_curve, variant="warning", height=32
        )
        self.btn_cc_stop.pack(side=L["lead"], padx=(0, 6))
        self.btn_cc_stop.configure(state="disabled")

        self.btn_cc_export = make_button(
            ctrl_bar, self._L(self._tr("btn_charge_curve_export")), self.export_charge_curve_csv, variant="secondary", height=32
        )
        self.btn_cc_export.pack(side=L["lead"], padx=(0, 6))
        self.btn_cc_export.configure(state="disabled")

        # Telemetry Indicators Strip (5 columns)
        telem_strip = ctk.CTkFrame(self.charging_curve_card, fg_color="transparent")
        telem_strip.pack(fill="x", padx=14, pady=(0, 8))
        telem_strip.grid_columnconfigure((0, 1, 2, 3, 4), weight=1, uniform="cc_telem")
        cols = (4, 3, 2, 1, 0) if L["is_rtl"] else (0, 1, 2, 3, 4)

        # Elapsed
        f_el = ctk.CTkFrame(telem_strip, fg_color="#1f2937", corner_radius=6)
        f_el.grid(row=0, column=cols[0], padx=3, sticky="ew")
        ctk.CTkLabel(f_el, text="Time / Duration", font=small, text_color=TEXT_MUTED).pack(pady=(4, 0))
        self.lbl_cc_elapsed = ctk.CTkLabel(f_el, text="0s / 60s", font=bold_small, text_color=TEXT_PRIMARY)
        self.lbl_cc_elapsed.pack(pady=(0, 4))

        # Voltage
        f_v = ctk.CTkFrame(telem_strip, fg_color="#1f2937", corner_radius=6)
        f_v.grid(row=0, column=cols[1], padx=3, sticky="ew")
        ctk.CTkLabel(f_v, text="Voltage", font=small, text_color=TEXT_MUTED).pack(pady=(4, 0))
        self.lbl_cc_voltage = ctk.CTkLabel(f_v, text="— V", font=bold_small, text_color=TEXT_PRIMARY)
        self.lbl_cc_voltage.pack(pady=(0, 4))

        # Current
        f_c = ctk.CTkFrame(telem_strip, fg_color="#1f2937", corner_radius=6)
        f_c.grid(row=0, column=cols[2], padx=3, sticky="ew")
        ctk.CTkLabel(f_c, text="Current", font=small, text_color=TEXT_MUTED).pack(pady=(4, 0))
        self.lbl_cc_current = ctk.CTkLabel(f_c, text="— mA", font=bold_small, text_color=ACCENT_CYAN)
        self.lbl_cc_current.pack(pady=(0, 4))

        # Power
        f_p = ctk.CTkFrame(telem_strip, fg_color="#1f2937", corner_radius=6)
        f_p.grid(row=0, column=cols[3], padx=3, sticky="ew")
        ctk.CTkLabel(f_p, text="Power (W)", font=small, text_color=TEXT_MUTED).pack(pady=(4, 0))
        self.lbl_cc_power = ctk.CTkLabel(f_p, text="— W", font=bold_small, text_color=ACCENT_GREEN)
        self.lbl_cc_power.pack(pady=(0, 4))

        # Temp
        f_t = ctk.CTkFrame(telem_strip, fg_color="#1f2937", corner_radius=6)
        f_t.grid(row=0, column=cols[4], padx=3, sticky="ew")
        ctk.CTkLabel(f_t, text="Temp (°C)", font=small, text_color=TEXT_MUTED).pack(pady=(4, 0))
        self.lbl_cc_temp = ctk.CTkLabel(f_t, text="— °C", font=bold_small, text_color=ACCENT_AMBER)
        self.lbl_cc_temp.pack(pady=(0, 4))

        # Live Canvas Plot (Dual-axis: Power W & Temp °C)
        self.cc_canvas = tk.Canvas(
            self.charging_curve_card,
            height=165,
            bg="#0d1117",
            bd=0,
            highlightthickness=1,
            highlightbackground="#30363d",
        )
        self.cc_canvas.pack(fill="x", padx=14, pady=(2, 8))
        self.cc_canvas.bind("<Configure>", lambda e: self._draw_cc_canvas())

        # Summary console
        self.cc_console = make_console_box(
            self.charging_curve_card, 110, L, self._L(self._tr("ui.charge_curve_hint"))
        )

        # Draw empty grid initial state
        self.after(50, self._draw_cc_canvas)

    # ---------------------------------------------------------------- Plot Rendering
    def _draw_cc_canvas(self) -> None:
        canvas = getattr(self, "cc_canvas", None)
        if not canvas or not canvas.winfo_exists():
            return

        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 100:
            w = 640
        if h < 50:
            h = 165

        canvas.delete("all")

        left = 42
        right = w - 42
        top = 26
        bottom = h - 22
        plot_w = max(10, right - left)
        plot_h = max(10, bottom - top)

        dur = max(1.0, getattr(self, "_cc_requested_duration", 60.0))

        # Dynamic Power scale (at least 15W ceiling, or rounded up above peak)
        max_p = 15.0
        samples = getattr(self, "cc_samples", [])
        valid_powers = [s.power_w for s in samples if s.power_w is not None]
        if valid_powers:
            peak = max(valid_powers)
            if peak > 15.0:
                max_p = (int(peak / 5.0) + 1) * 5.0

        # Fixed Temp scale: 20°C to 60°C
        min_t = 20.0
        max_t = 60.0

        # Horizontal Grid Lines & Y-axis labels
        grid_steps = 4
        for i in range(grid_steps + 1):
            y = top + plot_h - (i / grid_steps) * plot_h
            canvas.create_line(left, y, right, y, fill="#1e293b", dash=(3, 3))

            # Left Y-axis (Power in Watts)
            pw_val = (i / grid_steps) * max_p
            canvas.create_text(
                left - 6,
                y,
                text=f"{pw_val:.0f}W",
                fill="#38bdf8",
                anchor="e",
                font=(FONT_FAMILY, 8),
            )

            # Right Y-axis (Temp in Celsius)
            tm_val = min_t + (i / grid_steps) * (max_t - min_t)
            canvas.create_text(
                right + 6,
                y,
                text=f"{tm_val:.0f}°C",
                fill="#fbbf24",
                anchor="w",
                font=(FONT_FAMILY, 8),
            )

        # Vertical Grid Lines & X-axis labels (Time)
        time_steps = 4
        for i in range(time_steps + 1):
            x = left + (i / time_steps) * plot_w
            canvas.create_line(x, top, x, bottom, fill="#1e293b", dash=(3, 3))
            t_val = (i / time_steps) * dur
            canvas.create_text(
                x,
                bottom + 10,
                text=f"{t_val:.0f}s",
                fill="#94a3b8",
                anchor="n",
                font=(FONT_FAMILY, 8),
            )

        # Legend at top
        canvas.create_line(left, 13, left + 20, 13, fill="#06b6d4", width=2)
        canvas.create_text(left + 24, 13, text="Power (W)", fill="#38bdf8", anchor="w", font=(FONT_FAMILY, 9, "bold"))

        canvas.create_line(left + 110, 13, left + 130, 13, fill="#f59e0b", width=2, dash=(4, 2))
        canvas.create_text(left + 134, 13, text="Temp (°C)", fill="#fbbf24", anchor="w", font=(FONT_FAMILY, 9, "bold"))

        # Empty State Message
        if not samples:
            canvas.create_text(
                left + plot_w / 2,
                top + plot_h / 2,
                text=self._tr("ui.charge_curve_hint"),
                fill="#64748b",
                font=(FONT_FAMILY, 10),
            )
            return

        # Power Line (Cyan)
        power_coords = []
        for s in samples:
            if s.power_w is not None:
                x = left + (s.t_seconds / dur) * plot_w
                y = top + plot_h - (min(s.power_w, max_p) / max_p) * plot_h
                power_coords.append((x, y))

        if len(power_coords) >= 2:
            flat_pts = [c for pt in power_coords for c in pt]
            canvas.create_line(flat_pts, fill="#06b6d4", width=2, smooth=False)

        for x, y in power_coords:
            canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill="#06b6d4", outline="#0891b2")

        if power_coords:
            lx, ly = power_coords[-1]
            canvas.create_oval(lx - 4, ly - 4, lx + 4, ly + 4, fill="#38bdf8", outline="#ffffff", width=1)

        # Temp Line (Amber, dashed)
        temp_coords = []
        for s in samples:
            if s.temp_c is not None:
                x = left + (s.t_seconds / dur) * plot_w
                clamped_t = max(min_t, min(s.temp_c, max_t))
                y = top + plot_h - ((clamped_t - min_t) / (max_t - min_t)) * plot_h
                temp_coords.append((x, y))

        if len(temp_coords) >= 2:
            flat_t = [c for pt in temp_coords for c in pt]
            canvas.create_line(flat_t, fill="#f59e0b", width=2, dash=(4, 2), smooth=False)

        for x, y in temp_coords:
            canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill="#f59e0b", outline="#d97706")

        if temp_coords:
            lx, ly = temp_coords[-1]
            canvas.create_oval(lx - 4, ly - 4, lx + 4, ly + 4, fill="#fbbf24", outline="#ffffff", width=1)

    # ---------------------------------------------------------------- Controls & Lifecycle
    def start_charge_curve_async(self) -> None:
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        dur_str = self.combo_cc_dur.get()
        try:
            duration_s = float(dur_str.rstrip("s"))
        except ValueError:
            duration_s = 60.0

        self._cc_requested_duration = duration_s
        self.cc_capturing = True
        self._cc_stop_requested = False
        self.cc_samples = []

        self.btn_cc_start.configure(state="disabled")
        self.btn_cc_stop.configure(state="normal")
        self.btn_cc_export.configure(state="disabled")

        self.lbl_cc_elapsed.configure(text=f"0s / {duration_s:.0f}s")
        self.lbl_cc_voltage.configure(text="— V")
        self.lbl_cc_current.configure(text="— mA")
        self.lbl_cc_power.configure(text="— W")
        self.lbl_cc_temp.configure(text="— °C")

        self._set_box(
            self.cc_console,
            self._tr("ui.charge_curve_recording", elapsed="0"),
        )
        self._draw_cc_canvas()

        def task():
            try:
                diag = self.board_diag
                cap = ChargingCurveCapture(diag=diag)
                curve = cap.capture(
                    serial=serial,
                    duration_s=duration_s,
                    interval_s=1.0,
                    progress=lambda s: self.after(0, lambda: self._on_cc_sample(s, duration_s)),
                    should_stop=lambda: self._cc_stop_requested,
                )
                self.last_charge_curve = curve
                self.after(0, lambda: self._on_cc_done(curve))
            except Exception as e:
                logger.error(f"Charging curve capture failed: {e}")
                self.after(0, lambda: self._on_cc_error(e))

        threading.Thread(target=task, daemon=True).start()

    def stop_charge_curve(self) -> None:
        self._cc_stop_requested = True
        self.btn_cc_stop.configure(state="disabled")

    def _on_cc_sample(self, sample: ChargingSample, total_s: float) -> None:
        self.cc_samples.append(sample)

        # Update telemetry labels
        self.lbl_cc_elapsed.configure(text=f"{sample.t_seconds:.0f}s / {total_s:.0f}s")
        if sample.voltage_mv is not None:
            self.lbl_cc_voltage.configure(text=f"{sample.voltage_mv / 1000.0:.2f} V")
        if sample.current_ma is not None:
            self.lbl_cc_current.configure(text=f"{sample.current_ma:.0f} mA")
        if sample.power_w is not None:
            self.lbl_cc_power.configure(text=f"{sample.power_w:.2f} W")
        if sample.temp_c is not None:
            self.lbl_cc_temp.configure(text=f"{sample.temp_c:.1f} °C")

        self._draw_cc_canvas()

    def _on_cc_done(self, curve: ChargingCurve) -> None:
        self.cc_capturing = False
        self.btn_cc_start.configure(state="normal")
        self.btn_cc_stop.configure(state="disabled")
        self.btn_cc_export.configure(state="normal")

        # Update status pill
        pill = self.status_pills.get("charge_curve")
        if pill:
            status_map = {
                Status.PASS: "PASS",
                Status.UNKNOWN: "UNKNOWN",
                Status.FAIL: "FAIL",
                Status.NEEDS_ROOT: "ROOT",
            }
            update_status_badge(pill, status_map.get(curve.status, "UNKNOWN"))

        # Format rows table in console
        lines = [
            self._tr(
                "ui.charge_curve_done",
                samples=str(curve.sample_count),
                peak=f"{curve.peak_power_w:.2f}" if curve.peak_power_w is not None else "N/A",
                max_t=f"{curve.max_temp_c:.1f}" if curve.max_temp_c is not None else "N/A",
            ),
            "-" * 65,
            f"{'Time':<8} | {'Volt':<10} | {'Current':<10} | {'Power':<10} | {'Temp':<8} | {'State'}",
            "-" * 65,
        ]

        for s in curve.samples:
            v_s = f"{s.voltage_mv:.0f} mV" if s.voltage_mv is not None else "N/A"
            c_s = f"{s.current_ma:.0f} mA" if s.current_ma is not None else "N/A"
            p_s = f"{s.power_w:.2f} W" if s.power_w is not None else "N/A"
            t_s = f"{s.temp_c:.1f} C" if s.temp_c is not None else "N/A"
            chg_s = "CHG" if s.is_charging else "DISCHG"
            lines.append(f"{s.t_seconds:>6.1f}s | {v_s:<10} | {c_s:<10} | {p_s:<10} | {t_s:<8} | {chg_s}")

        self._set_box(self.cc_console, "\n".join(lines))
        self._draw_cc_canvas()

    def _on_cc_error(self, err: Exception) -> None:
        self.cc_capturing = False
        self.btn_cc_start.configure(state="normal")
        self.btn_cc_stop.configure(state="disabled")
        self._set_box(self.cc_console, f"Error: {err}")

    def export_charge_curve_csv(self) -> None:
        if not self.last_charge_curve or not self.last_charge_curve.samples:
            messagebox.showinfo(self._tr("ui.notice"), self._tr("ui.charge_curve_no_data_alert"))
            return

        serial = self.current_serial.get() or "device"
        default_name = f"charging_curve_{serial}_{int(self.last_charge_curve.duration_s)}s.csv"

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Spreadsheet", "*.csv"), ("All files", "*.*")],
            initialfile=default_name,
        )
        if not path:
            return

        try:
            rows = self.last_charge_curve.to_rows()
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f, fieldnames=["t", "voltage_mv", "current_ma", "power_w", "temp_c", "charging"]
                )
                writer.writeheader()
                writer.writerows(rows)

            messagebox.showinfo(self._tr("ui.notice"), self._tr("ui.charge_curve_csv_saved", path=path))
        except Exception as e:
            logger.error(f"Failed to export CSV: {e}")
            messagebox.showerror(self._tr("ui.error"), str(e))
