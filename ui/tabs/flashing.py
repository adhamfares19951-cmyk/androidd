"""Official firmware flashing card (Rescue tab), on top of core.flashing.OfficialFlasher.

Flashing is destructive and irreversible, so this card never flashes without an explicit confirmation, runs a
safety check (model / unlock / Anti-Rollback) that can be inspected on its own first, and only ever drives the
safety-valved engine - which itself refuses theft-protection partitions and any auth/FRP-bypass argument."""

import os
import threading
from tkinter import filedialog, messagebox

import customtkinter as ctk

from core.flashing import ODIN_SLOTS, SAFE_FASTBOOT_PARTITIONS, OfficialFlasher
from core.result import Status
from ui.theme import (ACCENT_RED, FONT_FAMILY, TEXT_SECONDARY, make_button, make_card, make_console_box,
                      style_entry)

# segmented-button label key -> internal mode
FLASH_MODES = ("sideload", "fastboot", "mtk", "odin")


class FlashingMixin:
    """Methods of AndroidDoctorApp for the official-firmware flashing card (state lives on the app)."""

    def _flasher(self) -> OfficialFlasher:          # tests replace this with a recording fake
        return OfficialFlasher(bridge=self.bridge)

    def _flash_confirm(self, title: str, message: str) -> bool:
        """The single destructive-action gate. A separate method so tests can allow/deny it deterministically."""
        return bool(messagebox.askyesno(title, message))

    # ---------------------------------------------------------------- building
    def _build_flashing_card(self, wrap) -> None:
        L = self._layout()
        self.flash_card, _ = make_card(wrap, self._L(self._tr("card_flashing")), ACCENT_RED, L)
        small = ctk.CTkFont(family=FONT_FAMILY, size=12)

        ctk.CTkLabel(self.flash_card, text=self._L(self._tr("ui.flash_warning")), font=small,
                     text_color=ACCENT_RED, wraplength=640, anchor=L["anchor"],
                     justify=L["justify"]).pack(fill="x", padx=16, pady=(0, 6))

        mode_row = ctk.CTkFrame(self.flash_card, fg_color="transparent")
        mode_row.pack(fill="x", padx=14, pady=(0, 6))
        self._flash_mode_labels = {self._tr(f"flash.mode.{m}"): m for m in FLASH_MODES}
        self.flash_mode = ctk.CTkSegmentedButton(mode_row, values=list(self._flash_mode_labels),
                                                 command=lambda _=None: self._flash_on_mode_change())
        self.flash_mode.set(self._tr("flash.mode.sideload"))
        self.flash_mode.pack(side=L["lead"])

        path_row = ctk.CTkFrame(self.flash_card, fg_color="transparent")
        path_row.pack(fill="x", padx=14, pady=(0, 6))
        self.flash_path_entry = ctk.CTkEntry(path_row, height=32,
                                             placeholder_text=self._tr("ui.flash_pick_hint"))
        style_entry(self.flash_path_entry)
        self.flash_path_entry.pack(side=L["lead"], fill="x", expand=True, padx=(0, 6))
        make_button(path_row, self._L(self._tr("btn_browse")), self._flash_browse, variant="secondary",
                    height=32).pack(side=L["lead"])

        code_row = ctk.CTkFrame(self.flash_card, fg_color="transparent")
        code_row.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkLabel(code_row, text=self._L(self._tr("ui.flash_expected_codename")), font=small,
                     text_color=TEXT_SECONDARY).pack(side=L["lead"], padx=(0, 6))
        self.flash_codename_entry = ctk.CTkEntry(code_row, width=160, height=30)
        style_entry(self.flash_codename_entry)
        self.flash_codename_entry.pack(side=L["lead"])

        btns = ctk.CTkFrame(self.flash_card, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(0, 6))
        self.flash_check_btn = make_button(btns, self._L(self._tr("btn_flash_check_safety")),
                                           self._flash_check_safety, variant="secondary", height=32)
        self.flash_check_btn.pack(side=L["lead"], padx=(0, 6))
        make_button(btns, self._L(self._tr("btn_flash_run")), self._flash_run, variant="primary",
                    height=32).pack(side=L["lead"], padx=6)

        self.flash_text = make_console_box(self.flash_card, 150, L, self._L(self._tr("ui.flash_hint")))
        self._flash_on_mode_change()

    # ---------------------------------------------------------------- mode / browse
    def _flash_current_mode(self) -> str:
        return self._flash_mode_labels.get(self.flash_mode.get(), "sideload")

    def _flash_on_mode_change(self) -> None:
        # "Check safety" reads fastboot variables, so it only means something in fastboot mode.
        mode = self._flash_current_mode()
        try:
            self.flash_check_btn.configure(state="normal" if mode == "fastboot" else "disabled")
        except Exception:
            pass

    def _flash_browse(self) -> None:
        mode = self._flash_current_mode()
        if mode == "fastboot":
            path = filedialog.askdirectory(title=self._tr("ui.flash_pick_folder"))
        else:
            types = {
                "sideload": [(self._tr("ui.flash_ft_ota"), "*.zip")],
                "mtk": [(self._tr("ui.flash_ft_scatter"), "*.txt")],
                "odin": [(self._tr("ui.flash_ft_odin"), "*.tar *.tar.md5")],
            }[mode]
            path = filedialog.askopenfilename(title=self._tr("card_flashing"),
                                              filetypes=types + [(self._tr("ui.all_files"), "*.*")])
        if path:
            self.flash_path_entry.delete(0, "end")
            self.flash_path_entry.insert(0, path)

    # ---------------------------------------------------------------- fastboot image mapping
    def _flash_fastboot_images(self, folder: str):
        """Map <partition>.img files in a folder to the safe-partition allowlist. Forbidden or unknown-named
        images are left out here; the engine refuses them again as a second guard."""
        images = {}
        skipped = []
        try:
            names = sorted(os.listdir(folder))
        except OSError:
            return images, skipped
        for name in names:
            if not name.lower().endswith(".img"):
                continue
            stem = os.path.splitext(name)[0].lower()
            if stem in SAFE_FASTBOOT_PARTITIONS:
                images[stem] = os.path.join(folder, name)
            else:
                skipped.append(name)
        return images, skipped

    # ---------------------------------------------------------------- safety check (fastboot, read-only)
    def _flash_check_safety(self) -> None:
        serial = self.current_serial.get()
        codename = self.flash_codename_entry.get().strip() or None
        self._set_box(self.flash_text, self._tr("ui.flash_checking"))

        def task():
            flasher = self._flasher()
            ok, safety, err, ev = flasher.check_fastboot_safety(serial=serial, expected_product=codename)
            lines = [self._tr("ui.flash_safety_head")]
            lines.append(self._tr("ui.flash_safety_row", k="product", v=str(safety.get("product") or "-")))
            lines.append(self._tr("ui.flash_safety_row", k="unlocked", v=str(safety.get("unlocked") or "-")))
            lines.append(self._tr("ui.flash_safety_row", k="device_arb", v=str(safety.get("device_arb"))))
            verdict = self._tr("ui.flash_safety_ok") if ok is True else (
                self._tr("ui.flash_safety_unknown") if ok is None else self._m(err))
            lines.append(verdict)
            self.after(0, lambda: self._set_box(self.flash_text, "\n".join(lines)))
        threading.Thread(target=task, daemon=True).start()

    # ---------------------------------------------------------------- flash (destructive)
    def _flash_run(self) -> None:
        mode = self._flash_current_mode()
        path = self.flash_path_entry.get().strip()
        codename = self.flash_codename_entry.get().strip() or None
        if not path or not os.path.exists(path):
            self._set_box(self.flash_text, self._tr("ui.flash_no_path"))
            return

        # Build the engine payload per mode, and a human summary for the confirmation.
        if mode == "fastboot":
            images, skipped = self._flash_fastboot_images(path)
            if not images:
                self._set_box(self.flash_text, self._tr("ui.flash_no_images_found"))
                return
            summary = ", ".join(sorted(images))
            if skipped:
                self._append_box(self.flash_text, self._tr("ui.flash_skipped", names=", ".join(skipped)) + "\n")
        elif mode == "odin":
            summary = os.path.basename(path)
        else:
            summary = os.path.basename(path)

        name = self._tr(f"flash.mode.{mode}")
        if not self._flash_confirm(self._tr("card_flashing"),
                                   self._tr("ui.flash_confirm", mode=name, target=summary)):
            self._set_box(self.flash_text, self._tr("ui.flash_cancelled"))
            return

        self._set_box(self.flash_text, self._tr("ui.flash_running", mode=name))
        serial = self.current_serial.get()

        def task():
            flasher = self._flasher()
            try:
                if mode == "sideload":
                    res = flasher.adb_sideload(path, serial=serial, expected_device=codename)
                elif mode == "fastboot":
                    res = flasher.fastboot_flash(images, serial=serial, expected_product=codename)
                elif mode == "mtk":
                    res = flasher.mtk_brom_flash(path)
                else:
                    res = flasher.odin_flash(self._flash_odin_packages(path))
            except Exception as e:  # the worker must never crash the UI
                self.after(0, lambda: self._set_box(self.flash_text, self._tr("ui.flash_error", error=str(e))))
                return
            self.after(0, lambda: self._flash_show_result(res))
        threading.Thread(target=task, daemon=True).start()

    def _flash_odin_packages(self, path: str):
        """One picked .tar/.tar.md5 -> its Odin slot, guessed from the filename prefix (AP/BL/CP/CSC)."""
        base = os.path.basename(path).upper()
        slot = next((s for s in ODIN_SLOTS if base.startswith(s + "_") or base.startswith(s)), "AP")
        return {slot: path}

    def _flash_show_result(self, res) -> None:
        self.last_flash_result = res
        head = {Status.PASS: self._tr("ui.flash_done"),
                Status.FAIL: self._tr("ui.flash_failed"),
                Status.UNKNOWN: self._tr("ui.flash_unknown")}.get(res.status, str(res.status))
        lines = [f"[{res.status}] {head}", self._m(res.detail)]
        if getattr(res, "flashed_partitions", None):
            lines.append(self._tr("ui.flash_partitions", names=", ".join(res.flashed_partitions)))
        self._set_box(self.flash_text, "\n".join(lines))
