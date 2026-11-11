"""Rescue & Recovery tab: scrcpy, wireless ADB, fastboot, sideload, backups, APK batch."""

import os
import time
import webbrowser
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
from ui.theme import (
    BG_CANVAS,
    BG_CARD,
    BG_TILE,
    BG_INPUT,
    BORDER_SUBTLE,
    BORDER_ACCENT,
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
    make_console_box,
    make_button,
    style_entry,
)


class RescueTabMixin:
    """Methods of AndroidDoctorApp for this tab (state lives on the app)."""

    # =========================================================================
    # TAB 6: RESCUE & SYSTEM RECOVERY
    # =========================================================================
    def _build_rescue_tab(self):
        L = self._layout()
        tab = ctk.CTkScrollableFrame(self.content_frame, fg_color=BG_CANVAS)
        self.tab_frames["rescue_center"] = tab

        wrap = ctk.CTkFrame(tab, fg_color="transparent")
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        make_hero_header(
            wrap,
            self._L(self._tr("rescue_title")),
            self._L(self._tr("rescue_sub")),
            L,
            accent_color=ACCENT_PURPLE,
        )

        # Helper to build 3-col / N-col inline action rows inside a card without clipping
        def _card_btn_grid(card, specs, cols=3, pady=(0, 10)):
            bar = ctk.CTkFrame(card, fg_color="transparent")
            bar.pack(fill="x", padx=14, pady=pady)
            eff = max(1, min(cols, len(specs)))
            for c in range(eff):
                bar.grid_columnconfigure(c, weight=1, uniform=f"cgrid_{eff}")
            out_btns = []
            wrap_w = max(140, int(680 // eff) - 20)
            for idx, (label, cmd, variant) in enumerate(specs):
                r = idx // eff
                coff = idx % eff
                col = (eff - 1 - coff) if L["is_rtl"] else coff
                b = make_button(bar, label, cmd, variant=variant, height=34, font_size=11)
                try:
                    b._text_label.configure(wraplength=wrap_w, justify="center")
                except Exception:
                    pass
                b.grid(row=r, column=col, padx=4, pady=3, sticky="ew")
                out_btns.append(b)
            return out_btns

        # 1. Scrcpy Mirroring Card
        mirror_card, _ = make_card(wrap, self._L(self._tr("card_mirror")), ACCENT_BLUE, L)
        m_btns = _card_btn_grid(
            mirror_card,
            [
                (self._L(self._tr("btn_launch_mirror")), self._launch_scrcpy, "primary"),
                (self._L(self._tr("btn_stop_mirror")), self._stop_scrcpy, "secondary"),
                (self._L(self._tr("btn_scrcpy_otg")), self._launch_scrcpy_otg, "purple"),
            ],
            cols=3,
            pady=(0, 6),
        )
        self.btn_launch_mirror, self.btn_stop_mirror, self.btn_launch_otg = m_btns

        chk_row = ctk.CTkFrame(mirror_card, fg_color="transparent")
        chk_row.pack(fill="x", padx=14, pady=(0, 12))
        self.mirror_off_var = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            chk_row,
            text=self._L(self._tr("check_turn_off")),
            variable=self.mirror_off_var,
            text_color=TEXT_PRIMARY,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        ).pack(side=L["lead"], padx=4)

        # 2. Virtual On-Screen Control Pad Card
        self.vpad_card, _ = make_card(wrap, self._L(self._tr("card_vpad")), ACCENT_CYAN, L)
        vpad_row1 = ctk.CTkFrame(self.vpad_card, fg_color="transparent")
        vpad_row1.pack(fill="x", padx=14, pady=(2, 8))

        lbl_pin = ctk.CTkLabel(
            vpad_row1,
            text=self._L(self._tr("lbl_vpad_pin")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=TEXT_SECONDARY,
        )
        self.vpad_pin_entry = ctk.CTkEntry(vpad_row1, placeholder_text="1234", height=32)
        style_entry(self.vpad_pin_entry, L)
        b_pin = make_button(vpad_row1, self._L(self._tr("btn_send_pin")), self._send_virtual_pin, variant="primary", height=32)
        b_ent = make_button(vpad_row1, self._L(self._tr("btn_send_enter")), lambda: self._send_virtual_key(66), variant="secondary", height=32)
        b_swp = make_button(vpad_row1, self._L(self._tr("btn_swipe_unlock")), self._send_virtual_swipe, variant="success", height=32)

        if L["is_rtl"]:
            vpad_row1.grid_columnconfigure(3, weight=1)
            b_swp.grid(row=0, column=0, padx=3, sticky="ew")
            b_ent.grid(row=0, column=1, padx=3, sticky="ew")
            b_pin.grid(row=0, column=2, padx=3, sticky="ew")
            self.vpad_pin_entry.grid(row=0, column=3, padx=6, sticky="ew")
            lbl_pin.grid(row=0, column=4, padx=(6, 0))
        else:
            vpad_row1.grid_columnconfigure(1, weight=1)
            lbl_pin.grid(row=0, column=0, padx=(0, 6))
            self.vpad_pin_entry.grid(row=0, column=1, padx=6, sticky="ew")
            b_pin.grid(row=0, column=2, padx=3, sticky="ew")
            b_ent.grid(row=0, column=3, padx=3, sticky="ew")
            b_swp.grid(row=0, column=4, padx=3, sticky="ew")

        _card_btn_grid(
            self.vpad_card,
            [
                (self._L(self._tr("btn_key_power")), lambda: self._send_virtual_key(26), "danger"),
                (self._L(self._tr("btn_key_vol_up")), lambda: self._send_virtual_key(24), "secondary"),
                (self._L(self._tr("btn_key_vol_down")), lambda: self._send_virtual_key(25), "secondary"),
                (self._L(self._tr("btn_key_home")), lambda: self._send_virtual_key(3), "secondary"),
                (self._L(self._tr("btn_key_back")), lambda: self._send_virtual_key(4), "secondary"),
                (self._L(self._tr("btn_key_menu")), lambda: self._send_virtual_key(82), "secondary"),
            ],
            cols=3,
            pady=(0, 12),
        )

        # 3. Wireless ADB Card
        self.wireless_card, _ = make_card(wrap, self._L(self._tr("card_wireless_adb")), ACCENT_GREEN, L)

        w_row1 = ctk.CTkFrame(self.wireless_card, fg_color="transparent")
        w_row1.pack(fill="x", padx=14, pady=(2, 8))
        make_button(
            w_row1, self._L(self._tr("btn_enable_wireless")), self._enable_wireless_adb, variant="success", height=32
        ).pack(side=L["lead"], padx=4)
        self.lbl_wireless_ip = ctk.CTkLabel(
            w_row1,
            text=self._tr("ui.ip"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color="#34d399",
            fg_color=BG_TILE,
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.lbl_wireless_ip.pack(side=L["lead"], padx=8)

        w_row2 = ctk.CTkFrame(self.wireless_card, fg_color="transparent")
        w_row2.pack(fill="x", padx=14, pady=(0, 8))
        lbl_wt = ctk.CTkLabel(w_row2, text=self._L(self._tr("lbl_wireless_target")), font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=TEXT_SECONDARY)
        self.entry_wireless_addr = ctk.CTkEntry(w_row2, placeholder_text="192.168.1.100:5555", height=32)
        style_entry(self.entry_wireless_addr, L)
        b_wc = make_button(w_row2, self._L(self._tr("btn_wireless_connect")), self._connect_wireless_adb, variant="primary", height=32)
        b_wd = make_button(w_row2, self._L(self._tr("btn_disconnect_wireless")), self._disconnect_wireless_adb, variant="secondary", height=32)

        if L["is_rtl"]:
            w_row2.grid_columnconfigure(2, weight=1)
            b_wd.grid(row=0, column=0, padx=3)
            b_wc.grid(row=0, column=1, padx=3)
            self.entry_wireless_addr.grid(row=0, column=2, padx=6, sticky="ew")
            lbl_wt.grid(row=0, column=3, padx=(6, 0))
        else:
            w_row2.grid_columnconfigure(1, weight=1)
            lbl_wt.grid(row=0, column=0, padx=(0, 6))
            self.entry_wireless_addr.grid(row=0, column=1, padx=6, sticky="ew")
            b_wc.grid(row=0, column=2, padx=3)
            b_wd.grid(row=0, column=3, padx=3)

        w_row3 = ctk.CTkFrame(self.wireless_card, fg_color="transparent")
        w_row3.pack(fill="x", padx=14, pady=(0, 10))
        lbl_wp = ctk.CTkLabel(w_row3, text=self._L(self._tr("lbl_wireless_pair")), font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=TEXT_SECONDARY)
        self.entry_pair_addr = ctk.CTkEntry(w_row3, placeholder_text="192.168.1.100:37123", width=150, height=32)
        style_entry(self.entry_pair_addr, L)
        lbl_wk = ctk.CTkLabel(w_row3, text=self._L(self._tr("lbl_wireless_code")), font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=TEXT_SECONDARY)
        self.entry_pair_code = ctk.CTkEntry(w_row3, placeholder_text="123456", width=85, height=32)
        style_entry(self.entry_pair_code, L)
        b_pair = make_button(w_row3, self._L(self._tr("btn_pair_wireless")), self._pair_wireless_adb, variant="purple", height=32, width=105)

        if L["is_rtl"]:
            w_row3.grid_columnconfigure(3, weight=1)
            b_pair.grid(row=0, column=0, padx=3)
            self.entry_pair_code.grid(row=0, column=1, padx=4)
            lbl_wk.grid(row=0, column=2, padx=4)
            self.entry_pair_addr.grid(row=0, column=3, padx=6, sticky="ew")
            lbl_wp.grid(row=0, column=4, padx=(6, 0))
        else:
            w_row3.grid_columnconfigure(1, weight=1)
            lbl_wp.grid(row=0, column=0, padx=(0, 6))
            self.entry_pair_addr.grid(row=0, column=1, padx=6, sticky="ew")
            lbl_wk.grid(row=0, column=2, padx=4)
            self.entry_pair_code.grid(row=0, column=3, padx=4)
            b_pair.grid(row=0, column=4, padx=3)

        self.wireless_log_text = make_console_box(
            self.wireless_card, 85, L, self._L(self._tr("ui.to_enable_wireless_adb_connect_device"))
        )

        # 4. Setup Wizard & System Provisioning Card
        prov_card, _ = make_card(wrap, self._L(self._tr("card_provisioning")), ACCENT_BLUE, L)
        _card_btn_grid(
            prov_card,
            [
                (self._L(self._tr("btn_check_prov")), self._check_provisioning, "secondary"),
                (self._L(self._tr("btn_fix_prov")), self._fix_provisioning, "success"),
                (self._L(self._tr("btn_open_settings")), self._launch_settings, "primary"),
            ],
            cols=3,
        )
        self.prov_log_text = make_console_box(
            prov_card, 90, L, self._L(self._tr("ui.click_inspect_setup_flags_to_verify"))
        )

        # 5. Fastboot Rescue Card
        fb_card, _ = make_card(wrap, self._L(self._tr("card_fb")), ACCENT_RED, L)
        _card_btn_grid(
            fb_card,
            [
                (self._L(self._tr("btn_fb_vars")), self._read_fastboot_vars, "secondary"),
                (self._L(self._tr("btn_fix_bootloop")), self._fix_cache_bootloop, "danger"),
                (self._L(self._tr("btn_fb_reboot")), lambda: self.bridge.fastboot_reboot("", self.current_serial.get()), "secondary"),
            ],
            cols=3,
        )

        flash_bar = ctk.CTkFrame(fb_card, fg_color="transparent")
        flash_bar.pack(fill="x", padx=14, pady=(0, 10))
        lbl_part = ctk.CTkLabel(flash_bar, text=self._L(self._tr("lbl_partition")), font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=TEXT_SECONDARY)
        self.part_combo = ctk.CTkComboBox(
            flash_bar,
            values=["boot", "recovery", "vbmeta", "dtbo", "system"],
            width=110,
            height=32,
            fg_color=BG_INPUT,
            border_color=BORDER_ACCENT,
            button_color=ACCENT_BLUE,
            dropdown_fg_color=BG_CARD,
        )
        self.img_path_entry = ctk.CTkEntry(flash_bar, placeholder_text=self._tr("ui.path_to_image_img"), width=130, height=32)
        style_entry(self.img_path_entry)
        b_br = make_button(flash_bar, self._L(self._tr("btn_browse")), self._browse_image_file, variant="secondary", height=32, width=80)
        b_fl = make_button(flash_bar, self._L(self._tr("btn_flash")), self._flash_partition, variant="primary", height=32, width=90)

        if L["is_rtl"]:
            flash_bar.grid_columnconfigure(2, weight=1)
            b_fl.grid(row=0, column=0, padx=3)
            b_br.grid(row=0, column=1, padx=3)
            self.img_path_entry.grid(row=0, column=2, padx=6, sticky="ew")
            self.part_combo.grid(row=0, column=3, padx=4)
            lbl_part.grid(row=0, column=4, padx=(6, 0))
        else:
            flash_bar.grid_columnconfigure(2, weight=1)
            lbl_part.grid(row=0, column=0, padx=(0, 6))
            self.part_combo.grid(row=0, column=1, padx=4)
            self.img_path_entry.grid(row=0, column=2, padx=6, sticky="ew")
            b_br.grid(row=0, column=3, padx=3)
            b_fl.grid(row=0, column=4, padx=3)

        self.rescue_log_text = make_console_box(fb_card, 120, L)

        # 6. Emergency Data Extraction Card
        data_card, _ = make_card(wrap, self._L(self._tr("card_data")), ACCENT_GREEN, L)

        data_row = ctk.CTkFrame(data_card, fg_color="transparent")
        data_row.pack(fill="x", padx=14, pady=(0, 6))
        lbl_cat = ctk.CTkLabel(data_row, text=self._L(self._tr("lbl_category")), font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=TEXT_SECONDARY)
        # Keep raw combo option strings untransformed so _pull_emergency_data .split()[0] works safely
        self.data_cat_combo = ctk.CTkComboBox(
            data_row,
            values=[
                self._tr("ui.all_photos_all_photos_dcim"),
                self._tr("ui.full_storage_full_storage_dump"),
                self._tr("ui.whatsapp_whatsapp_media_only_chats_are"),
                self._tr("ui.downloads_downloads"),
                self._tr("ui.documents_documents"),
            ],
            height=32,
            fg_color=BG_INPUT,
            border_color=BORDER_ACCENT,
            button_color=ACCENT_BLUE,
            dropdown_fg_color=BG_CARD,
        )
        if L["is_rtl"]:
            data_row.grid_columnconfigure(0, weight=1)
            self.data_cat_combo.grid(row=0, column=0, padx=(0, 8), sticky="ew")
            lbl_cat.grid(row=0, column=1)
        else:
            data_row.grid_columnconfigure(1, weight=1)
            lbl_cat.grid(row=0, column=0, padx=(0, 8))
            self.data_cat_combo.grid(row=0, column=1, sticky="ew")

        _card_btn_grid(
            data_card,
            [
                (self._L(self._tr("btn_pull_data")), self._pull_emergency_data, "success"),
                (self._L(self._tr("ui.1_click_full_photos_pull")), self._pull_all_photos_quick, "success"),
                (self._L(self._tr("btn_one_click_backup")), self._one_click_customer_backup, "cyan"),
            ],
            cols=3,
        )
        self.data_log_text = make_console_box(
            data_card, 110, L, self._L(self._tr("ui.emergency_data_and_photo_pull_from"))
        )

        # 7. ADB Sideload OTA ROM Flasher Card
        sideload_card, _ = make_card(wrap, self._L(self._tr("card_sideload")), ACCENT_CYAN, L)

        sideload_bar = ctk.CTkFrame(sideload_card, fg_color="transparent")
        sideload_bar.pack(fill="x", padx=14, pady=(0, 8))
        self.sideload_entry = ctk.CTkEntry(
            sideload_bar, placeholder_text=self._tr("ui.path_to_ota_update_package_zip"), height=32
        )
        style_entry(self.sideload_entry)
        b_ota = make_button(sideload_bar, self._L(self._tr("btn_browse_ota")), self._browse_ota_file, variant="secondary", height=32)
        b_sl = make_button(sideload_bar, self._L(self._tr("btn_flash_sideload")), self._flash_sideload, variant="cyan", height=32)

        if L["is_rtl"]:
            sideload_bar.grid_columnconfigure(2, weight=1)
            b_sl.grid(row=0, column=0, padx=3)
            b_ota.grid(row=0, column=1, padx=3)
            self.sideload_entry.grid(row=0, column=2, padx=(6, 0), sticky="ew")
        else:
            sideload_bar.grid_columnconfigure(0, weight=1)
            self.sideload_entry.grid(row=0, column=0, padx=(0, 6), sticky="ew")
            b_ota.grid(row=0, column=1, padx=3)
            b_sl.grid(row=0, column=2, padx=3)

        self.sideload_progress = ctk.CTkProgressBar(
            sideload_card, height=10, corner_radius=5, fg_color=BORDER_SUBTLE, progress_color=ACCENT_CYAN
        )
        self.sideload_progress.set(0)
        self.sideload_progress.pack(fill="x", padx=14, pady=(2, 6))

        self.sideload_lbl = ctk.CTkLabel(
            sideload_card,
            text=self._L(self._tr("ui.ready_put_device_in_recovery_mode")),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=TEXT_SECONDARY,
            anchor=L["anchor"],
            justify=L["justify"],
        )
        self.sideload_lbl.pack(fill="x", padx=14, pady=(0, 6))
        self.sideload_text = make_console_box(
            sideload_card, 100, L, self._L(self._tr("ui.stream_official_ota_rom_update_zip"))
        )

        # 8. Fastboot Active Slot (A/B) & Anti-Rollback (ARB) Card
        slot_card, _ = make_card(wrap, self._L(self._tr("card_ab_slot")), ACCENT_AMBER, L)
        _card_btn_grid(
            slot_card,
            [
                (self._L(self._tr("btn_switch_slot_a")), lambda: self._switch_slot("a"), "primary"),
                (self._L(self._tr("btn_switch_slot_b")), lambda: self._switch_slot("b"), "primary"),
                (self._L(self._tr("btn_check_arb")), self._check_arb, "danger"),
            ],
            cols=3,
        )
        self.slot_log_text = make_console_box(
            slot_card, 90, L, self._L(self._tr("ui.in_fastboot_switch_between_slot_a"))
        )

        # 9. Critical Partitions & Radio Backup Card
        part_card, _ = make_card(wrap, self._L(self._tr("card_partition_backup")), ACCENT_GREEN, L)
        _card_btn_grid(
            part_card,
            [
                (self._L(self._tr("btn_backup_critical")), self._backup_critical_partitions, "success"),
                (self._L(self._tr("btn_backup_all")), self._backup_all_partitions, "cyan"),
                (self._L(self._tr("btn_open_backups")), self._open_backups_folder, "secondary"),
            ],
            cols=3,
        )
        self.part_backup_text = make_console_box(
            part_card, 110, L, self._L(self._tr("ui.dump_and_backup_critical_radio_calibration"))
        )

        # 10. Batch APK Installer & Extractor Card
        apk_card, _ = make_card(wrap, self._L(self._tr("card_batch_apk")), ACCENT_PURPLE, L, pady=(0, 0))
        _card_btn_grid(
            apk_card,
            [
                (self._L(self._tr("btn_install_batch_apk")), self._batch_install_apks, "purple"),
                (self._L(self._tr("btn_extract_apps")), self._extract_user_apps, "secondary"),
            ],
            cols=2,
        )
        self.apk_log_text = make_console_box(
            apk_card, 110, L, self._L(self._tr("ui.batch_install_multiple_apk_files_r"))
        )

    def _launch_scrcpy(self):
        serial = self.current_serial.get()
        turn_off = self.mirror_off_var.get()
        threading.Thread(target=lambda: self.rescue_center.launch_screen_mirror(serial, turn_screen_off=turn_off), daemon=True).start()

    def _stop_scrcpy(self):
        threading.Thread(target=self.rescue_center.stop_screen_mirror, daemon=True).start()

    def _launch_scrcpy_otg(self):
        serial = self.current_serial.get()
        is_ar = self.current_lang == "ar"
        threading.Thread(target=lambda: self.rescue_center.launch_otg_mode(serial), daemon=True).start()
        if hasattr(self, "wireless_log_text"):
            self.wireless_log_text.insert("end", self._tr("ui.otg_usb_hid_mouse_keyboard_mode"))
            self.wireless_log_text.see("end")

    def _send_virtual_pin(self):
        serial = self.current_serial.get()
        pin = self.vpad_pin_entry.get().strip()
        if not pin:
            return
        def task():
            self.rescue_center.send_text(pin, serial)
            time.sleep(0.2)
            self.rescue_center.send_keyevent(66, serial)
        threading.Thread(target=task, daemon=True).start()

    def _send_virtual_swipe(self):
        serial = self.current_serial.get()
        threading.Thread(target=lambda: self.rescue_center.send_swipe(500, 1600, 500, 400, 300, serial), daemon=True).start()

    def _send_virtual_key(self, key_code: int):
        serial = self.current_serial.get()
        threading.Thread(target=lambda: self.rescue_center.send_keyevent(key_code, serial), daemon=True).start()

    def _enable_wireless_adb(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_phone_connected_over_usb"))
            return
        is_ar = self.current_lang == "ar"
        self.wireless_log_text.delete("1.0", "end")
        self.wireless_log_text.insert("end", self._tr("ui.switching_adb_to_tcp_ip_mode"))

        def task():
            ok, msg = self.bridge.enable_wireless_adb(serial, 5555)
            ip = self.bridge.get_device_ip_address(serial)
            def done():
                self.wireless_log_text.insert("end", f"{msg}\n")
                if ip:
                    ip_port = f"{ip}:5555"
                    self.lbl_wireless_ip.configure(text=self._tr("ui.ip_2", ip_port=ip_port))
                    self.entry_wireless_addr.delete(0, "end")
                    self.entry_wireless_addr.insert(0, ip_port)
                    info_msg = (
                        self._tr("ui.wireless_adb_enabled_on_you_can", ip_port=ip_port)
                    )
                    messagebox.showinfo(self._tr("ui.wireless_adb_ready"), self._m(info_msg))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.port_5555_was_enabled_but_the"))
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _connect_wireless_adb(self):
        addr = self.entry_wireless_addr.get().strip()
        if not addr:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.enter_the_ip_address_and_port"))
            return
        is_ar = self.current_lang == "ar"
        def task():
            ok, msg = self.bridge.connect_wireless_adb(addr)
            def done():
                self.wireless_log_text.insert("end", self._tr("ui.wireless_connection", addr=addr, msg=msg))
                self.wireless_log_text.see("end")
                if ok:
                    messagebox.showinfo(self._tr("ui.connected"), self._tr("ui.connected_to_the_wireless_device", addr=addr))
                else:
                    messagebox.showerror(self._tr("ui.connection_failed"), self._m(msg))
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _disconnect_wireless_adb(self):
        addr = self.entry_wireless_addr.get().strip()
        def task():
            ok, msg = self.bridge.disconnect_wireless_adb(addr)
            def done():
                self.wireless_log_text.insert("end", self._tr("ui.wireless_disconnect", msg=msg))
                self.wireless_log_text.see("end")
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _pair_wireless_adb(self):
        addr = self.entry_pair_addr.get().strip()
        code = self.entry_pair_code.get().strip()
        if not addr or not code:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.enter_the_pairing_ip_address_and"))
            return
        def task():
            ok, msg = self.bridge.pair_wireless_adb(addr, code)
            def done():
                self.wireless_log_text.insert("end", self._tr("ui.android_11_pairing", addr=addr, msg=msg))
                self.wireless_log_text.see("end")
                if ok:
                    messagebox.showinfo(self._tr("ui.pairing_succeeded"), self._tr("ui.the_device_was_paired_wirelessly_you"))
                else:
                    messagebox.showerror(self._tr("ui.pairing_failed"), self._m(msg))
                self.refresh_devices_sync()
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _check_provisioning(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        def task():
            p_status = self.provisioning.get_provisioning_status(serial)
            is_ar = self.current_lang == "ar"
            lines = [
                self._tr("ui.provisioning_status"),
                self._tr("ui.device_provisioned", raw_provisioned_val=p_status.raw_provisioned_val, not_completed=self._tr("ui.yes_completed") if p_status.is_device_provisioned else self._tr("ui.not_completed")),
                self._tr("ui.user_setup_complete", raw_setup_complete_val=p_status.raw_setup_complete_val, not_completed=self._tr("ui.yes_completed") if p_status.is_user_setup_complete else self._tr("ui.not_completed")),
                self._tr("ui.result", status_summary=p_status.status_summary)
            ]
            def upd():
                self.prov_log_text.delete("1.0", "end")
                self.prov_log_text.insert("end", "\n".join(lines))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _fix_provisioning(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        def task():
            ok, msg = self.provisioning.complete_setup_wizard_flags(serial)
            def upd():
                self.prov_log_text.delete("1.0", "end")
                self.prov_log_text.insert("end", self._tr("ui.setup_wizard_flags", msg=msg))
                if ok:
                    messagebox.showinfo(self._tr("ui.skipped"), self._tr("ui.setup_complete_flags_were_set_and"))
                else:
                    messagebox.showerror(self._tr("ui.error"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _launch_settings(self):
        serial = self.current_serial.get()
        if not serial:
            return
        def task():
            ok, msg = self.provisioning.launch_settings_activity(serial)
            def upd():
                self.prov_log_text.delete("1.0", "end")
                self.prov_log_text.insert("end", f"{msg}\n")
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _read_fastboot_vars(self):
        serial = self.current_serial.get()
        def task():
            fb = self.rescue_center.read_fastboot_variables(serial)
            is_ar = self.current_lang == "ar"
            lines = [
                self._tr("ui.fastboot_device_variables"),
                self._tr("ui.product", product=fb.product),
                self._tr("ui.bootloader_version", bootloader_version=fb.bootloader_version),
                self._tr("ui.baseband_version_2", baseband_version=fb.baseband_version),
                self._tr("ui.secure_boot", disabled=self._tr("ui.enabled") if fb.is_secure else self._tr("ui.disabled")),
                self._tr("ui.bootloader_lock", locked=self._tr("ui.unlocked") if fb.is_unlocked else self._tr("ui.locked")),
                self._tr("ui.current_slot", current_slot=fb.current_slot),
                self._tr("ui.battery_voltage_bootloader", battery_voltage=fb.battery_voltage),
                self._tr("ui.cpu_abi", cpu_abi=fb.cpu_abi),
                self._tr("ui.all_raw_variables")
            ]
            for k, v in fb.raw_vars.items():
                lines.append(f"  {k}: {v}")

            def upd():
                self.rescue_log_text.delete("1.0", "end")
                self.rescue_log_text.insert("end", "\n".join(lines))
            self.after(0, upd)

        threading.Thread(target=task, daemon=True).start()

    def _fix_cache_bootloop(self):
        serial = self.current_serial.get()
        is_ar = self.current_lang == "ar"

        def task():
            has_cache, check_msg = self.rescue_center.has_cache_partition(serial)
            if has_cache is not True:
                def no_cache():
                    self.rescue_log_text.delete("1.0", "end")
                    self.rescue_log_text.insert("end", f"{check_msg}\n")
                    messagebox.showinfo(self._tr("ui.erase_cache"),
                                        (self._tr("ui.this_device_has_no_cache_partition")))
                self.after(0, no_cache)
                return
            confirm = {"ok": False}
            def ask():
                confirm["ok"] = messagebox.askyesno(
                    self._tr("ui.erase_cache"),
                    (self._tr("ui.this_erases_the_cache_partition_only")))
                if confirm["ok"]:
                    threading.Thread(target=run_erase, daemon=True).start()
            def run_erase():
                ok, msg = self.rescue_center.fix_cache_bootloop(serial)
                def upd():
                    self.rescue_log_text.delete("1.0", "end")
                    self.rescue_log_text.insert("end", f"{check_msg}\n{msg}\n")
                    if ok:
                        messagebox.showinfo(self._tr("ui.erase_cache"),
                                            self._tr("ui.cache_partition_erased_reboot_to_check"))
                    else:
                        messagebox.showerror(self._tr("ui.error"), self._m(msg))
                self.after(0, upd)
            self.after(0, ask)
        threading.Thread(target=task, daemon=True).start()

    def _browse_image_file(self):
        f = filedialog.askopenfilename(filetypes=[("Android Image", "*.img"), ("All Files", "*.*")])
        if f:
            self.img_path_entry.delete(0, "end")
            self.img_path_entry.insert(0, f)

    def _flash_partition(self):
        serial = self.current_serial.get()
        part = self.part_combo.get().strip()
        path = self.img_path_entry.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.choose_a_valid_img_file_to"))
            return

        def task():
            ok, msg = self.rescue_center.flash_partition(part, path, serial)
            def upd():
                self.rescue_log_text.delete("1.0", "end")
                self.rescue_log_text.insert("end", self._tr("ui.flash", part=part, msg=msg))
                if ok:
                    messagebox.showinfo(self._tr("ui.flash_succeeded"), self._tr("ui.partition_was_flashed", part=part))
                else:
                    messagebox.showerror(self._tr("ui.flash_failed"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _pull_emergency_data(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_the_phone_first_or_connect"))
            return

        raw_cat = self.data_cat_combo.get()
        cat = raw_cat.split(" ")[0].strip().lower()
        if not cat:
            cat = "all_photos"

        desktop = os.path.join(os.path.expanduser("~"), "Desktop", f"Android_Rescue_{cat}_{int(time.time())}")
        is_ar = self.current_lang == "ar"

        self.data_log_text.delete("1.0", "end")
        self.data_log_text.insert("end", self._tr("ui.starting_data_extraction_to_desktop_folder", cat=cat))

        def task():
            def prog(txt):
                self.after(0, lambda: self.data_log_text.insert("end", f"{txt}\n"))
            ok, msg = self.rescue_center.emergency_data_pull(serial, cat, desktop, progress_callback=prog)
            def upd():
                if ok:
                    messagebox.showinfo(self._tr("ui.copy_finished"), self._tr("ui.files_were_copied_to_the_desktop", desktop=desktop))
                    try:
                        webbrowser.open(desktop)
                    except Exception:
                        pass
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.data_copy_result", msg=msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _pull_all_photos_quick(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_the_phone_first_or_connect"))
            return

        desktop = os.path.join(os.path.expanduser("~"), "Desktop", f"Android_All_Photos_{int(time.time())}")
        is_ar = self.current_lang == "ar"

        self.data_log_text.delete("1.0", "end")
        self.data_log_text.insert("end", self._tr("ui.scanning_and_pulling_all_photos_videos"))

        def task():
            def prog(txt):
                self.after(0, lambda: self.data_log_text.insert("end", f"{txt}\n"))
            ok, msg = self.rescue_center.emergency_data_pull(serial, "all_photos", desktop, progress_callback=prog)
            def upd():
                if ok:
                    messagebox.showinfo(self._tr("ui.photo_copy_finished"), self._tr("ui.all_photos_and_videos_were_copied", desktop=desktop))
                    try:
                        webbrowser.open(desktop)
                    except Exception:
                        pass
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.photo_copy_result", msg=msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _browse_ota_file(self):
        f = filedialog.askopenfilename(filetypes=[("OTA Firmware Package", "*.zip"), ("All Files", "*.*")])
        if f:
            self.sideload_entry.delete(0, "end")
            self.sideload_entry.insert(0, f)

    def _flash_sideload(self):
        serial = self.current_serial.get()
        path = self.sideload_entry.get().strip()
        is_ar = self.current_lang == "ar"
        if not path or not os.path.exists(path):
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.choose_a_valid_rom_file_zip"))
            return

        self.sideload_progress.set(0)
        self.sideload_text.delete("1.0", "end")
        self.sideload_text.insert("end", self._tr("ui.starting_adb_sideload", path=os.path.basename(path)))

        def task():
            def prog(pct, msg):
                def update():
                    self.sideload_progress.set(pct / 100.0)
                    self.sideload_lbl.configure(text=f"[{pct}%] {msg}")
                    self.sideload_text.insert("end", f"{msg}\n")
                    self.sideload_text.see("end")
                self.after(0, update)

            ok, out_msg = self.rescue_center.sideload_firmware(path, serial, progress_callback=prog)
            def done():
                if ok:
                    messagebox.showinfo(self._tr("ui.sideload_complete"), self._tr("ui.ota_firmware_package_flashed_successfully_via"))
                else:
                    messagebox.showerror(self._tr("ui.sideload_failed"), self._m(out_msg))
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _one_click_customer_backup(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first_or"))
            return

        is_ar = self.current_lang == "ar"
        self.data_log_text.delete("1.0", "end")
        self.data_log_text.insert("end", self._tr("ui.starting_1_click_customer_backup_media"))

        def task():
            def prog(txt):
                self.after(0, lambda: (self.data_log_text.insert("end", f"{txt}\n"), self.data_log_text.see("end")))
            ok, summary, b_dir = self.rescue_center.one_click_customer_backup(serial, progress_callback=prog)
            def done():
                if ok:
                    messagebox.showinfo(
                        self._tr("ui.customer_backup_complete"),
                        self._tr("ui.customer_data_backup_saved_to_desktop", b_dir=b_dir)
                    )
                    try:
                        webbrowser.open(b_dir)
                    except Exception:
                        pass
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(summary))
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _switch_slot(self, slot: str):
        serial = self.current_serial.get()
        is_ar = self.current_lang == "ar"
        self.slot_log_text.delete("1.0", "end")
        self.slot_log_text.insert("end", self._tr("ui.switching_active_boot_slot_to_slot", upper=slot.upper()))

        def task():
            ok, msg = self.rescue_center.switch_active_slot(slot, serial)
            def upd():
                self.slot_log_text.delete("1.0", "end")
                self.slot_log_text.insert("end", self._m(msg))
                if ok:
                    messagebox.showinfo(self._tr("ui.slot_switch"), self._tr("ui.successfully_switched_to_slot", upper=slot.upper()))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _check_arb(self):
        serial = self.current_serial.get()
        is_ar = self.current_lang == "ar"
        self.slot_log_text.delete("1.0", "end")
        self.slot_log_text.insert("end", self._tr("ui.querying_anti_rollback_arb_hardware_fuse"))

        def task():
            ok, msg, arb_index = self.rescue_center.check_anti_rollback(serial)
            def upd():
                self.slot_log_text.delete("1.0", "end")
                self.slot_log_text.insert("end", self._m(msg))
                if ok:
                    messagebox.showinfo(self._tr("ui.arb_index"), self._tr("ui.anti_rollback_protection_index_arb_do", arb_index=arb_index))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(msg))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _backup_critical_partitions(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_phone_first"))
            return
        is_ar = self.current_lang == "ar"
        self.part_backup_text.delete("1.0", "end")
        self.part_backup_text.insert("end", self._tr("ui.dumping_critical_radio_partitions_efs_fsg"))

        def task():
            def prog(txt):
                self.after(0, lambda: self.part_backup_text.insert("end", f"{txt}\n"))
            res = self.partition_backup.backup_critical_partitions(serial=serial, progress_callback=prog)
            def upd():
                lines = [
                    self._tr("ui.critical_partition_backup_report"),
                    self._tr("ui.success", warning_partial=self._tr("ui.yes_completed_2") if res.success else self._tr("ui.warning_partial")),
                    self._tr("ui.partitions_copied", dumped_partitions=len(res.dumped_partitions)),
                    self._tr("ui.saved_in_folder", backup_directory=res.backup_directory),
                    self._tr("ui.diagnosis_2", diagnosis=res.diagnosis)
                ]
                self.part_backup_text.insert("end", "\n".join(lines))
                if res.success:
                    messagebox.showinfo(self._tr("ui.backup_complete"), self._tr("ui.critical_partitions_saved_to", backup_directory=res.backup_directory))
                else:
                    messagebox.showwarning(self._tr("ui.notice"), self._m(res.diagnosis))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _backup_all_partitions(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_a_phone_first"))
            return
        is_ar = self.current_lang == "ar"
        self.part_backup_text.delete("1.0", "end")
        self.part_backup_text.insert("end", self._tr("ui.dumping_all_discoverable_partitions_to_pc"))

        def task():
            def prog(txt):
                self.after(0, lambda: self.part_backup_text.insert("end", f"{txt}\n"))
            res = self.partition_backup.backup_all_partitions(serial=serial, progress_callback=prog)
            def upd():
                self.part_backup_text.insert("end", self._tr("ui.copy_finished_partitions_folder", dumped_partitions=len(res.dumped_partitions), backup_directory=res.backup_directory))
                messagebox.showinfo(self._tr("ui.backup_complete"), self._tr("ui.partitions_saved_in", backup_directory=res.backup_directory))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _open_backups_folder(self):
        folder = self.partition_backup.backup_base_dir
        os.makedirs(folder, exist_ok=True)
        webbrowser.open(folder)

    def _batch_install_apks(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        files = filedialog.askopenfilenames(filetypes=[("Android APK", "*.apk"), ("All Files", "*.*")])
        if not files:
            return
        is_ar = self.current_lang == "ar"
        self.apk_log_text.delete("1.0", "end")
        self.apk_log_text.insert("end", self._tr("ui.batch_installing_apk_files", files=len(files)))

        def task():
            def prog(cur, tot, f):
                self.after(0, lambda: self.apk_log_text.insert("end", self._tr("ui.installing", cur=cur, tot=tot, f=os.path.basename(f))))
            results = self.batch_apk.install_multiple_apks(serial, list(files), progress_callback=prog)
            def upd():
                successes = sum(1 for r in results if r.success)
                summary = self._tr("ui.batch_install_finished_successful", successes=successes, results=len(results))
                self.apk_log_text.insert("end", self._m(summary))
                messagebox.showinfo(self._tr("ui.install_complete"), self._m(summary))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()

    def _extract_user_apps(self):
        serial = self.current_serial.get()
        if not serial:
            messagebox.showwarning(self._tr("ui.notice"), self._tr("ui.select_an_authorized_phone_first"))
            return
        is_ar = self.current_lang == "ar"
        self.apk_log_text.delete("1.0", "end")
        self.apk_log_text.insert("end", self._tr("ui.listing_and_extracting_user_apps_to"))

        def task():
            def prog(cur, tot, pkg):
                self.after(0, lambda: self.apk_log_text.insert("end", self._tr("ui.pulling", cur=cur, tot=tot, pkg=pkg)))
            results = self.batch_apk.extract_all_user_apks(serial, progress_callback=prog)
            def upd():
                successes = sum(1 for r in results if r.success)
                summary = self._tr("ui.extracted_apps_to_desktop", successes=successes)
                self.apk_log_text.insert("end", self._m(summary))
                messagebox.showinfo(self._tr("ui.extraction_complete"), self._m(summary))
                if results and results[0].output_path:
                    webbrowser.open(os.path.dirname(results[0].output_path))
            self.after(0, upd)
        threading.Thread(target=task, daemon=True).start()
