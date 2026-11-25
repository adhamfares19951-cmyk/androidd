"""Hardware & Sensors tab: subsystem audit, keys, touch/colour/tone bench helpers."""

import logging
import threading
from tkinter import filedialog, messagebox
import customtkinter as ctk
from core.result import CheckResult
from ui.theme import (
    BG_CANVAS,
    BG_CARD,
    BORDER_SUBTLE,
    TEXT_PRIMARY,
    ACCENT_BLUE,
    ACCENT_CYAN,
    ACCENT_GREEN,
    ACCENT_AMBER,
    ACCENT_PURPLE,
    FONT_FAMILY,
    make_hero_header,
    make_card,
    make_console_box,
    make_button,
    pack_button_grid,
)
from ui.ui_helpers import (
    _tri_key,
    _feature_key,
    _flinger_key,
    format_evidence_for_display,
    format_display_check,
    format_parts_check,
    format_network_diag,
)

logger = logging.getLogger("MainWindow")


class HardwareTabMixin:
    """Methods of AndroidDoctorApp for this tab (state lives on the app)."""

    # =========================================================================
    # TAB 3: HARDWARE, SENSORS & PERIPHERALS TESTER
    # =========================================================================
    def _build_hardware_tester_tab(self):
        L = self._layout()
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color=BG_CANVAS)
        self.tab_frames["hardware_tester"] = tab

        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        make_hero_header(
            wrap,
            self._L(self._tr("hw_title")),
            self._L(self._tr("hw_sub")),
            L,
            accent_color=ACCENT_BLUE,
        )

        # Responsive 3x3 Button Grid (Zero clipping on all widths)
        hw_specs = [
            (self._L(self._tr("btn_audit_hw")), self.run_hardware_audit_async, "primary"),
            (self._L(self._tr("btn_run_network_diag")), self.run_network_diag_async, "cyan"),
            (self._L(self._tr("ui.test_physical_keys_live")), self.run_buttons_test_async, "secondary"),
            (self._L(self._tr("btn_storage_bench")), self.run_storage_benchmark_async, "purple"),
            (self._L(self._tr("btn_test_vib")), self._trigger_vibration, "secondary"),
            (self._L(self._tr("btn_test_audio")), self._trigger_audio, "secondary"),
            (self._L(self._tr("btn_speaker_test")), self.run_speaker_tone_async, "success"),
            (self._L(self._tr("btn_lcd_color_test")), self.run_screen_color_test_async, "warning"),
            (self._L(self._tr("btn_touch_matrix")), self.run_touch_matrix_async, "cyan"),
            (self._L(self._tr("btn_companion_diag")), self.run_companion_diag_async, "purple"),
            (self._L(self._tr("btn_display_check")), self.run_display_check_async, "cyan"),
            (self._L(self._tr("btn_companion_wifi")), self.run_companion_wifi_async, "purple"),
            (self._L(self._tr("btn_companion_import")), self.import_companion_file, "secondary"),
        ]
        _, h_btns = pack_button_grid(
            wrap, hw_specs, cols=3, is_rtl=L["is_rtl"], pady=(0, 8), height=36
        )
        (
            self.btn_audit_hw,
            self.btn_network_diag,
            self.btn_test_keys,
            self.btn_storage_bench,
            self.btn_test_vib,
            self.btn_test_audio,
            self.btn_speaker_test,
            self.btn_lcd_color_test,
            self.btn_touch_matrix,
            self.btn_companion_diag,
            self.btn_display_check,
            self.btn_companion_wifi,
            self.btn_companion_import,
        ) = h_btns

        # Touch Overlay Switch Strip
        sw_strip = ctk.CTkFrame(
            wrap,
            fg_color=BG_CARD,
            corner_radius=10,
            border_width=1,
            border_color=BORDER_SUBTLE,
        )
        sw_strip.pack(fill="x", pady=(0, 12))
        self.switch_pointer = ctk.CTkSwitch(
            sw_strip,
            text=self._L(self._tr("switch_pointer")),
            variable=self.pointer_location_var,
            command=self._toggle_pointer_location,
            progress_color=ACCENT_BLUE,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
        )
        self.switch_pointer.pack(side=L["lead"], padx=14, pady=8)

        col_a = 1 if L["is_rtl"] else 0
        col_b = 0 if L["is_rtl"] else 1
        pad_a = (6, 0) if L["is_rtl"] else (0, 6)
        pad_b = (0, 6) if L["is_rtl"] else (6, 0)

        # Screen originality indicators (read only; indicators, never proof)
        self.display_check_card, _ = make_card(wrap, self._L(self._tr("card_display_check")), ACCENT_CYAN, L)
        self.display_check_text = make_console_box(
            self.display_check_card, 160, L, self._L(self._tr("ui.display_hint"))
        )

        # Network & Cellular Diagnostics (core.network_diag: Modem/EFS, SIM reader, RF/antenna)
        self.network_diag_card, badge = make_card(
            wrap, self._L(self._tr("card_network_diag")), ACCENT_CYAN, L, show_badge=True
        )
        self.status_pills["network"] = badge

        net_ctrl = ctk.CTkFrame(self.network_diag_card, fg_color="transparent")
        net_ctrl.pack(fill="x", padx=14, pady=(0, 6))

        self.btn_network_card_run = make_button(
            net_ctrl,
            self._L(self._tr("btn_run_network_diag")),
            self.run_network_diag_async,
            variant="cyan",
            height=32,
        )
        self.btn_network_card_run.pack(side=L["lead"], padx=(0, 6))

        # Interactive manual-step frame (hidden until SIM absent requires testing with known-good card)
        self.net_manual_frame = ctk.CTkFrame(net_ctrl, fg_color="transparent")
        self.btn_sim_inserted = make_button(
            self.net_manual_frame,
            self._L(self._tr("btn_sim_inserted")),
            lambda: self.run_network_diag_async(answers={"known_good_sim": "inserted"}),
            variant="success",
            height=30,
        )
        self.btn_sim_inserted.pack(side=L["lead"], padx=4)
        self.btn_sim_skip = make_button(
            self.net_manual_frame,
            self._L(self._tr("btn_sim_skip")),
            lambda: self.run_network_diag_async(answers={"known_good_sim": "skip"}),
            variant="secondary",
            height=30,
        )
        self.btn_sim_skip.pack(side=L["lead"], padx=4)

        self.network_diag_text = make_console_box(
            self.network_diag_card, 160, L, self._L(self._tr("ui.network_hint"))
        )

        # Content Grid 1: Sensors & Cameras
        hw_grid = ctk.CTkFrame(wrap, fg_color="transparent")
        hw_grid.pack(fill="both", expand=True, pady=(0, 12))
        hw_grid.grid_columnconfigure((0, 1), weight=1, uniform="hw_dual_top")

        self.sensor_card, badge = make_card(
            hw_grid, self._L(self._tr("card_sensors")), ACCENT_BLUE, L, show_badge=True, pack_card=False
        )
        self.status_pills["sensors"] = badge
        self.sensor_card.grid(row=0, column=col_a, padx=pad_a, sticky="nsew")
        self.sensors_text = make_console_box(self.sensor_card, 210, L)

        self.camera_card, badge = make_card(
            hw_grid, self._L(self._tr("card_cameras")), ACCENT_CYAN, L, show_badge=True, pack_card=False
        )
        self.status_pills["cameras"] = badge
        self.camera_card.grid(row=0, column=col_b, padx=pad_b, sticky="nsew")
        self.camera_text = make_console_box(self.camera_card, 210, L)

        # Content Grid 2: Low-Level Peripherals
        periph_grid = ctk.CTkFrame(wrap, fg_color="transparent")
        periph_grid.pack(fill="both", expand=True)
        periph_grid.grid_columnconfigure((0, 1), weight=1, uniform="hw_dual_bot")

        def _grid_card(row: int, col: int, padx: tuple[int, int], pady: tuple[int, int], title_key: str, accent: str, pill_key: str | None, height: int, init_key: str | None = None):
            card, bdg = make_card(
                periph_grid,
                self._L(self._tr(title_key)),
                accent,
                L,
                show_badge=(pill_key is not None),
                pack_card=False,
            )
            if pill_key and bdg is not None:
                self.status_pills[pill_key] = bdg
            card.grid(row=row, column=col, padx=padx, pady=pady, sticky="nsew")
            init_txt = self._L(self._tr(init_key)) if init_key else ""
            box = make_console_box(card, height, L, init_txt)
            return card, box

        self.fp_card, self.fp_text = _grid_card(0, col_a, pad_a, (0, 12), "card_fingerprint", ACCENT_BLUE, "fp", 140)
        self.keys_card, self.keys_text = _grid_card(0, col_b, pad_b, (0, 12), "card_keys", ACCENT_GREEN, "keys", 140)

        self.sim_card, self.sim_text = _grid_card(1, col_a, pad_a, (0, 12), "card_sim", ACCENT_CYAN, "sim", 140)
        self.backlight_card, self.backlight_text = _grid_card(1, col_b, pad_b, (0, 12), "card_backlight", ACCENT_AMBER, "backlight", 140)

        self.wifi_card, self.wifi_text = _grid_card(2, col_a, pad_a, (0, 12), "card_wifi", ACCENT_BLUE, "wifi", 140)
        self.bt_card, self.bt_text = _grid_card(2, col_b, pad_b, (0, 12), "card_bluetooth", ACCENT_PURPLE, "bt", 140)

        self.chips_card, badge = make_card(
            periph_grid, self._L(self._tr("card_chips")), ACCENT_CYAN, L, show_badge=True, pack_card=False
        )
        self.status_pills["chips"] = badge
        self.chips_card.grid(row=3, column=0, columnspan=2, padx=0, pady=(0, 12), sticky="nsew")
        self.chips_text = make_console_box(self.chips_card, 110, L)

        self.storage_card, self.storage_text = _grid_card(
            4, col_a, pad_a, (0, 12), "card_storage_bench", ACCENT_PURPLE, "storage", 145, "ui.click_test_storage_wear_speed_to"
        )
        self.touch_card, self.touch_text = _grid_card(
            4, col_b, pad_b, (0, 12), "card_touch_matrix", ACCENT_CYAN, None, 145, "ui.click_interactive_touch_digitizer_grid_test"
        )

        self.audio_codec_card, self.audio_codec_text = _grid_card(
            5, col_a, pad_a, (0, 0), "card_audio_codec", "#f472b6", "audio_codec", 145, "ui.inspects_alsa_soundcards_audio_codec_ic"
        )
        self.panel_info_card, self.panel_info_text = _grid_card(
            5, col_b, pad_b, (0, 0), "card_panel_info", ACCENT_AMBER, "panel_info", 145, "ui.reads_the_kernel_reported_display_controller"
        )

    def run_hardware_audit_async(self):
        serial = self.current_serial.get()
        if not serial:
            return

        def task():
            report = self.hardware_tester.run_full_audit(serial)
            self.last_hw_audit = report
            is_ar = self.current_lang == "ar"

            # Sensors
            s_lines = [self._tr("ui.reported_sensors_registered", sensors=len(report.sensors))]
            for s in report.sensors:
                power_txt = f" | {s.power_ma} mA" if s.power_ma is not None else ""
                s_lines.append(f"• {s.name} ({s.vendor}) | {s.sensor_type}{power_txt}")
            if not report.sensors:
                s_lines.append(f"[{report.sensors_status}]")
            if report.missing_critical_sensors:
                s_lines.append((self._tr("ui.common_sensors_not_reported_not_necessarily")) + ", ".join(report.missing_critical_sensors))

            # Cameras & Audio
            c_lines = [self._tr("ui.camera_modules_detected", cameras=len(report.cameras))]
            for c in report.cameras:
                flash_txt = "UNKNOWN" if c.flash_available is None else ((self._tr("ui.yes")) if c.flash_available else (self._tr("ui.no_2")))
                c_lines.append(self._tr("ui.camera_flash", camera_id=c.camera_id, facing=self._tr(f"camera.facing.{c.facing}"), flash_txt=flash_txt))
            if not report.cameras:
                c_lines.append(f"[{report.cameras_status}]")

            c_lines.append(self._tr("ui.audio_subsystem"))
            c_lines.append(self._tr("ui.mode_ringer", mode=report.audio.mode, ringer_mode=report.audio.ringer_mode))

            # Fingerprint
            fp_lines = [
                self._tr("ui.verified_status", status=report.fingerprint.status),
                self._tr("ui.fingerprint_sensor_vendor", vendor=report.fingerprint.vendor),
                self._tr("ui.diagnosis", diagnosis=report.fingerprint.diagnosis)
            ]
            for cause in report.fingerprint.possible_causes:
                fp_lines.append(self._tr("ui.text_2", possible_cause=self._tr("ui.possible_cause"), cause=cause))
            if report.fingerprint.evidence:
                fp_lines.append(format_evidence_for_display(report.fingerprint.evidence, lang=self.current_lang))

            # Physical Keys
            key_lines = [
                self._tr("ui.verified_status", status=report.buttons.status),
                self._tr("ui.volume_down_vol", vol_down_status=self._tr(f"keys.state.{report.buttons.vol_down_status}")),
                self._tr("ui.volume_up_vol", vol_up_status=self._tr(f"keys.state.{report.buttons.vol_up_status}")),
                self._tr("ui.power", power_status=self._tr(f"keys.state.{report.buttons.power_status}")),
                self._tr("ui.diagnosis_2", diagnosis=report.buttons.diagnosis)
            ]
            if report.buttons.evidence:
                key_lines.append(format_evidence_for_display(report.buttons.evidence, lang=self.current_lang))

            # SIM & Baseband
            sim_lines = [
                self._tr("ui.verified_status", status=report.sim_baseband.status),
                self._tr("ui.sim_1_state", sim1_state=report.sim_baseband.sim1_state),
                self._tr("ui.sim_2_state", sim2_state=report.sim_baseband.sim2_state),
                self._tr("ui.carrier_operator", carrier_name=report.sim_baseband.carrier_name),
                self._tr("ui.network_type", network_type=report.sim_baseband.network_type),
                self._tr("ui.signal_strength", signal_strength=report.sim_baseband.signal_strength),
                self._tr("ui.baseband_version", baseband_version=report.sim_baseband.baseband_version),
                self._tr("ui.diagnosis_2", diagnosis=report.sim_baseband.diagnosis)
            ]
            for cause in report.sim_baseband.possible_causes:
                sim_lines.append(self._tr("ui.text_2", possible_cause=self._tr("ui.possible_cause"), cause=cause))
            if report.sim_baseband.evidence:
                sim_lines.append(format_evidence_for_display(report.sim_baseband.evidence, lang=self.current_lang))

            # Display Backlight
            disp_lines = [
                self._tr("ui.verified_status", status=report.display_backlight.status),
                self._tr("ui.backlight_level", brightness_now=report.display_backlight.brightness_now, max_brightness=report.display_backlight.max_brightness),
                self._tr("ui.diagnosis_2", diagnosis=report.display_backlight.diagnosis)
            ]
            for cause in report.display_backlight.possible_causes:
                disp_lines.append(self._tr("ui.text_2", possible_cause=self._tr("ui.possible_cause"), cause=cause))
            if report.display_backlight.evidence:
                disp_lines.append(format_evidence_for_display(report.display_backlight.evidence, lang=self.current_lang))

            # Wi-Fi Chipset
            wifi_lines = [
                self._tr("ui.verified_status", status=report.wifi.status),
                self._tr("ui.wi_fi_state", state=report.wifi.state),
                self._tr("ui.mac_address", mac_address=report.wifi.mac_address),
                self._tr("ui.connected_network_ssid", disconnected=report.wifi.ssid or (self._tr("ui.disconnected"))),
                self._tr("ui.signal_rssi_dbm", rssi_dbm=report.wifi.rssi_dbm) if report.wifi.rssi_dbm else (self._tr("ui.signal")),
                self._tr("ui.link_speed_and_frequency_mbps_mhz", link_speed_mbps=report.wifi.link_speed_mbps, frequency_mhz=report.wifi.frequency_mhz) if report.wifi.link_speed_mbps else (self._tr("ui.speed_freq")),
                self._tr("ui.driver_status", driver_status=self._tr(f"wifi.driver.{report.wifi.driver_status}")),
                self._tr("ui.diagnosis_2", diagnosis=report.wifi.diagnosis)
            ]
            if report.wifi.evidence:
                wifi_lines.append(format_evidence_for_display(report.wifi.evidence, lang=self.current_lang))

            # Bluetooth Controller
            bt_lines = [
                self._tr("ui.verified_status", status=report.bluetooth.status),
                self._tr("ui.bluetooth_state", state=report.bluetooth.state),
                self._tr("ui.mac_address_2", mac_address=report.bluetooth.mac_address),
                self._tr("ui.device_name", name=report.bluetooth.name),
                self._tr("ui.ble", ble_supported=self._tr(_tri_key(report.bluetooth.ble_supported))),
                self._tr("ui.diagnosis_2", diagnosis=report.bluetooth.diagnosis)
            ]
            for cause in report.bluetooth.possible_causes:
                bt_lines.append(self._tr("ui.text_2", possible_cause=self._tr("ui.possible_cause"), cause=cause))
            if report.bluetooth.evidence:
                bt_lines.append(format_evidence_for_display(report.bluetooth.evidence, lang=self.current_lang))

            # Peripheral Chips (GPS, NFC, OTG)
            chip_lines = [
                self._tr("ui.verified_status", status=report.peripherals.status),
                self._tr("ui.gps", gps_status=self._tr(f'feature.{report.peripherals.gps_status}')),
                self._tr("ui.nfc", nfc_status=self._tr(f'feature.{report.peripherals.nfc_status}')),
                self._tr("ui.usb_host_otg", usb_otg_supported=self._tr(_feature_key(report.peripherals.usb_otg_supported))),
                self._tr("ui.diagnosis_2", diagnosis=report.peripherals.diagnosis)
            ]
            if report.peripherals.evidence:
                chip_lines.append(format_evidence_for_display(report.peripherals.evidence, lang=self.current_lang))

            # Audio Codec IC (ALSA Soundcards)
            codec_lines = [
                self._tr("ui.verified_status", status=report.audio_codec.status),
                self._tr("ui.audio_codec_ic", codec_chip_name=report.audio_codec.codec_chip_name),
                self._tr("ui.alsa_sound_cards_found", cards_detected=len(report.audio_codec.cards_detected), none=', '.join(report.audio_codec.cards_detected) if report.audio_codec.cards_detected else (self._tr("ui.none"))),
                self._tr("ui.alsa_pcm_channels", pcm_devices=len(report.audio_codec.pcm_devices)),
                self._tr("ui.audioflinger_output_threads", audio_flinger_alive=self._tr(_flinger_key(report.audio_codec.audio_flinger_alive)), output_threads=report.audio_codec.output_threads),
                self._tr("ui.diagnosis_2", diagnosis=report.audio_codec.diagnosis)
            ]
            for cause in report.audio_codec.possible_causes:
                codec_lines.append(self._tr("ui.text_2", possible_cause=self._tr("ui.possible_cause"), cause=cause))
            if report.audio_codec.evidence:
                codec_lines.append(format_evidence_for_display(report.audio_codec.evidence, lang=self.current_lang))

            # Display Controller & Panel Info
            panel_lines = [
                self._tr("ui.verified_status", status=report.display_panel_info.status),
                self._tr("ui.dsi_panel_controller", panel_controller_name=report.display_panel_info.panel_controller_name),
                self._tr("ui.driver_source", driver_source=report.display_panel_info.driver_source),
                self._tr("ui.verified_boot_state", verified_boot_state=report.display_panel_info.verified_boot_state),
                self._tr("ui.vbmeta_device_state", vbmeta_device_state=report.display_panel_info.vbmeta_device_state),
                self._tr("ui.diagnosis_2", diagnosis=report.display_panel_info.diagnosis)
            ]
            if report.display_panel_info.evidence:
                panel_lines.append(format_evidence_for_display(report.display_panel_info.evidence, lang=self.current_lang))

            self.last_audio_codec = report.audio_codec
            self.last_panel_info = report.display_panel_info

            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.sensors_text, "\n".join(s_lines))
                self._set_pill("sensors", getattr(report, "sensors_status", "UNKNOWN"))

                self._set_box(self.camera_text, "\n".join(c_lines))
                self._set_pill("cameras", getattr(report, "cameras_status", "UNKNOWN"))

                self._set_box(self.fp_text, "\n".join(fp_lines))
                self._set_pill("fp", getattr(report.fingerprint, "status", "UNKNOWN"))

                self._set_box(self.keys_text, "\n".join(key_lines))
                self._set_pill("keys", getattr(report.buttons, "status", "UNKNOWN"))

                self._set_box(self.sim_text, "\n".join(sim_lines))
                self._set_pill("sim", getattr(report.sim_baseband, "status", "UNKNOWN"))

                self._set_box(self.backlight_text, "\n".join(disp_lines))
                self._set_pill("backlight", getattr(report.display_backlight, "status", "UNKNOWN"))

                if hasattr(self, "wifi_text"):
                    self._set_box(self.wifi_text, "\n".join(wifi_lines))
                    self._set_pill("wifi", getattr(report.wifi, "status", "UNKNOWN"))
                if hasattr(self, "bt_text"):
                    self._set_box(self.bt_text, "\n".join(bt_lines))
                    self._set_pill("bt", getattr(report.bluetooth, "status", "UNKNOWN"))
                if hasattr(self, "chips_text"):
                    self._set_box(self.chips_text, "\n".join(chip_lines))
                    self._set_pill("chips", getattr(report.peripherals, "status", "UNKNOWN"))
                if hasattr(self, "audio_codec_text"):
                    self._set_box(self.audio_codec_text, "\n".join(codec_lines))
                    self._set_pill("audio_codec", getattr(report.audio_codec, "status", "UNKNOWN"))
                if hasattr(self, "panel_info_text"):
                    self._set_box(self.panel_info_text, "\n".join(panel_lines))
                    self._set_pill("panel_info", getattr(report.display_panel_info, "status", "UNKNOWN"))
                self.pointer_location_var.set(report.pointer_location_active)

            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_buttons_test_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return

        self._ensure_tab("hardware_tester")
        is_ar = self.current_lang == "ar"
        self._set_box(self.keys_text, self._tr("ui.listening_for_physical_key_presses_press"))

        def task():
            btn_diag = self.hardware_tester.diagnose_physical_buttons(serial)
            lines = [
                self._tr("ui.physical_buttons_test_result"),
                self._tr("ui.volume_down", vol_down_status=self._tr(f"keys.state.{btn_diag.vol_down_status}")),
                self._tr("ui.volume_up", vol_up_status=self._tr(f"keys.state.{btn_diag.vol_up_status}")),
                self._tr("ui.power_2", power_status=self._tr(f"keys.state.{btn_diag.power_status}")),
                self._tr("ui.diagnosis_3", diagnosis=btn_diag.diagnosis),
                ""
            ]
            if btn_diag.captured_events:
                lines.append(self._tr("ui.captured_input_events"))
                for ev in btn_diag.captured_events:
                    lines.append(f"  {ev}")

            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.keys_text, "\n".join(lines))
                self._set_pill("keys", getattr(btn_diag, "status", "UNKNOWN"))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def _trigger_vibration(self):
        serial = self.current_serial.get()
        if serial:
            self.hardware_tester.trigger_vibrator(serial, 1000)

    def _trigger_audio(self):
        serial = self.current_serial.get()
        if serial:
            self.hardware_tester.play_audio_test(serial)

    def _toggle_pointer_location(self):
        serial = self.current_serial.get()
        if serial:
            enabled = self.pointer_location_var.get()
            self.hardware_tester.set_pointer_location(serial, enabled)

    def run_display_check_async(self):
        """Screen originality indicators: reads only (panel identity, display modes, under-display fingerprint)."""
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        self._ensure_tab("hardware_tester")
        self._set_box(self.display_check_text, self._tr("ui.display_hint"))
        lang = self.current_lang

        def task():
            from core.display_check import DisplayOriginalityCheck
            from core.parts_check import PartsCheck
            result = DisplayOriginalityCheck(self.bridge).run(serial)
            self.last_display_check = result
            lines = format_display_check(result, lang)
            if result.evidence:
                lines.append(format_evidence_for_display(result.evidence, lang=lang))
            try:
                parts = PartsCheck(self.bridge).run(serial)
            except Exception as e:      # the screen result must still be shown
                logger.error(f"Parts check failed: {e}")
                parts = None
            self.last_parts_check = parts
            if parts is not None:
                lines += [""] + format_parts_check(parts, lang)
                if parts.evidence:
                    lines.append(format_evidence_for_display(parts.evidence, lang=lang))

            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.display_check_text, "\n".join(lines))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_network_diag_async(self, answers=None):
        """Cellular network & SIM diagnostics (core.network_diag)."""
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        self._ensure_tab("hardware_tester")
        self._set_box(self.network_diag_text, self._tr("ui.network_running"))
        lang = self.current_lang
        L = self._layout()

        def task():
            from core.network_diag import NetworkDiagnostics
            result = NetworkDiagnostics(self.bridge).diagnose(serial=serial, answers=answers)
            self.last_network_diag = result
            lines = format_network_diag(result, lang)
            if result.evidence:
                lines.append("")
                lines.append(format_evidence_for_display(result.evidence, lang=lang))

            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.network_diag_text, "\n".join(lines))
                if hasattr(self, "_set_pill"):
                    self._set_pill("network", str(result.status))
                    self._set_pill("sim", str(result.status))
                if hasattr(self, "sim_text"):
                    self._set_box(self.sim_text, "\n".join(lines))
                if hasattr(self, "net_manual_frame"):
                    if result.manual_step is not None:
                        self.net_manual_frame.pack(side=L["lead"], padx=6)
                    else:
                        self.net_manual_frame.pack_forget()

            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def run_storage_benchmark_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        self._ensure_tab("hardware_tester")
        is_ar = self.current_lang == "ar"
        self._set_box(self.storage_text, self._tr("ui.running_emmc_ufs_health_check_read"))

        def task():
            report = self.storage_bench.run_full_storage_test(serial)
            self.last_storage_report = report
            lines = [
                self._tr("ui.storage_emmc_ufs_life_cycle_speed"),
                self._tr("ui.verified_status", status=report.status),
                self._tr("ui.storage_type_2", storage_type=report.storage_type),
                self._tr("ui.slc_wear_type_a", life_time_type_a=report.life_time_type_a),
                self._tr("ui.mlc_wear_type_b", life_time_type_b=report.life_time_type_b),
                self._tr("ui.reserved_blocks_pre_eol_2", pre_eol_info=report.pre_eol_info),
                self._tr("ui.sequential_write_speed_mb_s", write_speed_mb_s=report.write_speed_mb_s),
                self._tr("ui.sequential_read_speed_mb_s", read_speed_mb_s=report.read_speed_mb_s),
                self._tr("ui.health_verdict", health_verdict=report.health_verdict),
                self._tr("ui.diagnosis", diagnosis=report.diagnosis)
            ]
            if report.evidence:
                lines.append(format_evidence_for_display(report.evidence, lang=self.current_lang))
            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.storage_text, "\n".join(lines))
                self._set_pill("storage", getattr(report, "status", "UNKNOWN"))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_touch_matrix_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        self._ensure_tab("hardware_tester")
        is_ar = self.current_lang == "ar"
        self._set_box(self.touch_text, self._tr("ui.deploying_touch_digitizer_grid_test_to"))

        def task():
            ok, msg = self.touch_matrix.deploy_and_launch_touch_test(serial)
            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.touch_text, self._tr("ui.touch_test", msg=msg))
                if ok:
                    messagebox.showinfo(self._tr("ui.touch_test_2"), self._tr("ui.touch_test_grid_opened_on_device"))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_screen_color_test_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_device_first"))
            return
        self._ensure_tab("hardware_tester")
        is_ar = self.current_lang == "ar"
        self._set_box(self.touch_text, self._tr("ui.deploying_full_screen_rgbw_dead_pixel"))

        def task():
            ok, msg = self.hardware_tester.launch_screen_color_test(serial)
            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.touch_text, self._tr("ui.screen_test_status", msg=msg))
                if ok:
                    answer = messagebox.askyesno(
                        self._tr("ui.screen_color_test"),
                        self._tr("ui.interactive_color_test_is_now_running")
                    )
                    if answer:
                        self.hardware_tester.stop_screen_color_test(serial)
                        self._append_box(self.touch_text, self._tr("ui.screen_test_closed_and_touch_overlays"))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_speaker_tone_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_device_first"))
            return
        self._ensure_tab("hardware_tester")
        is_ar = self.current_lang == "ar"
        self._set_box(self.audio_codec_text, self._tr("ui.generating_pure_1000hz_sine_test_tone"))

        def task():
            ok, msg = self.hardware_tester.play_speaker_test_tone(serial, freq=1000, duration_sec=3.0)
            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.audio_codec_text, self._tr("ui.speaker_test_output", msg=msg))
                if ok:
                    messagebox.showinfo(self._tr("ui.speaker_test"), self._tr("ui.test_tone_played_successfully_on_phone"))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def run_companion_diag_async(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_device_first"))
            return
        self._ensure_tab("hardware_tester")
        self._set_box(self.sensors_text, self._tr("ui.companion_running"))
        self.btn_companion_diag.configure(state="disabled")

        def task():
            from core import companion
            from core.model_db import default_db
            try:
                run = companion.run_companion(self.bridge, serial)
                spec = default_db().lookup(self.bridge.get_prop("ro.product.model", serial=serial) or "",
                                          self.bridge.get_prop("ro.product.device", serial=serial) or "")
                checks = companion.judge(run, spec)
                notice = run.notice
            except Exception as e:
                logger.error(f"Companion run failed: {e}")
                checks = [CheckResult(check_id="companion.run", detail=str(e))]
                notice = None
            self.last_companion_checks = checks  # added to the diagnostic report
            lines = [self._m(notice)] if notice is not None else []   # the app already on the phone was used
            diag_txt = "\n".join(lines + self._companion_check_lines(checks))

            def upd():
                self._ensure_tab("hardware_tester")
                self._set_box(self.sensors_text, diag_txt)
                self.btn_companion_diag.configure(state="normal")
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    # ------------------------------------------------------------- companion app without USB debugging
    def _companion_check_lines(self, checks):
        lines = []
        for c in checks:
            lines.append(f"[{c.status}] {self._tr(c.check_id)}")
            if c.detail is not None:
                lines.append(f"    {self._m(c.detail)}")
            for cause in c.possible_causes:
                lines.append(f"    - {self._tr('common.possible_cause')}: {self._m(cause)}")
        return lines

    def _show_companion_result(self, run, how_key):
        """Judges a run that came without ADB (Wi-Fi or file) and shows it; the checks go into the next report."""
        from core import companion
        from core.companion_lan import device_label, reported_device, reported_model
        from core.model_db import default_db
        try:
            spec = (default_db().lookup(reported_model(run), reported_device(run))
                    if (reported_model(run) or reported_device(run)) else None)
            checks = companion.judge(run, spec)
        except Exception as e:
            logger.error(f"Companion result could not be judged: {e}")
            checks = [CheckResult(check_id="companion.run", detail=str(e))]
        self.last_companion_checks = checks
        head = [self._tr(how_key, device=device_label(run) or self._tr("ui.companion_unknown_phone"))]
        txt = "\n".join(head + self._companion_check_lines(checks))
        self._ensure_tab("hardware_tester")
        self._set_box(self.sensors_text, txt)

    def import_companion_file(self):
        path = filedialog.askopenfilename(
            title=self._tr("btn_companion_import"),
            filetypes=[(self._tr("ui.companion_result_files"), "ADP_*.txt *.txt"), ("*", "*.*")],
        )
        if not path:
            return
        from core.companion_lan import load_result_file
        self._show_companion_result(load_result_file(path), "ui.companion_from_file")

    def run_companion_wifi_async(self):
        """QR code -> the phone opens the PC's page, installs/opens the app, taps Start -> results over Wi-Fi."""
        from core.companion_lan import CompanionWifiSession, qr_png
        session = CompanionWifiSession(lang=self.current_lang)
        try:
            session.start()
        except Exception as e:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.companion_wifi_failed", error=str(e)))
            return
        self.btn_companion_wifi.configure(state="disabled")
        url = session.url()
        state = {"closed": False}

        dialog = ctk.CTkToplevel(self)
        dialog.title(self._tr("btn_companion_wifi"))
        dialog.geometry("520x680")
        ctk.CTkLabel(dialog, text=self._tr("ui.companion_wifi_scan"), wraplength=470, justify="center",
                     font=ctk.CTkFont(size=14, weight="bold"), text_color=TEXT_PRIMARY).pack(padx=20, pady=(16, 8))
        img = qr_png(url, box_size=7)
        if img is not None:
            qr_image = ctk.CTkImage(light_image=img, dark_image=img, size=(300, 300))
            ctk.CTkLabel(dialog, image=qr_image, text="").pack(pady=6)
            dialog._qr_image = qr_image  # keep a reference while the window is open
        entry = ctk.CTkEntry(dialog, width=460, justify="center")
        entry.insert(0, url)
        entry.configure(state="readonly")
        entry.pack(padx=20, pady=6)
        ctk.CTkLabel(dialog, text=self._tr("ui.companion_wifi_steps"), wraplength=470, justify="left",
                     text_color=TEXT_PRIMARY).pack(padx=20, pady=6)
        status = ctk.CTkLabel(dialog, text=self._tr("ui.companion_wifi_waiting"), wraplength=470,
                              text_color=ACCENT_AMBER)
        status.pack(padx=20, pady=10)

        def close():
            if state["closed"]:
                return
            state["closed"] = True
            session.stop()
            self.btn_companion_wifi.configure(state="normal")
            try:
                dialog.destroy()
            except Exception:
                pass

        ctk.CTkButton(dialog, text=self._tr("ui.companion_wifi_stop"), command=close).pack(pady=(4, 16))
        dialog.protocol("WM_DELETE_WINDOW", close)

        def on_event(kind, info):
            keys = {"page": "ui.companion_wifi_page_opened", "apk": "ui.companion_wifi_apk_downloaded",
                    "rejected": "ui.companion_wifi_rejected"}
            if kind in keys:
                text = self._tr(keys[kind], peer=info.get("peer", ""))
                self.after(0, lambda: (not state["closed"]) and status.configure(text=text))
        session.on_event = on_event

        def wait_task():
            while not state["closed"] and not session.received.wait(1.0):
                pass
            if state["closed"] and not session.received.is_set():
                return
            run = session.run

            def done():
                close()
                self._show_companion_result(run, "ui.companion_from_wifi")
            self.after(0, done)
        threading.Thread(target=wait_task, daemon=True).start()
