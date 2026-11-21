"""Schematics / boardview / datasheet / pinout panel (Motherboard tab), on top of core.schematics_db.

Nothing is invented: only documents in the technician's own folder, the cache, or an index entry with a source are
listed, each with its real status (PASS = a real file on disk, UNKNOWN = not on disk, FAIL = a fetched file that
did not match its published checksum). The program ships no schematics of its own."""

import logging
import os
import threading
import webbrowser
from tkinter import filedialog

import customtkinter as ctk

from core.result import Status
from core.schematics_db import (BUNDLED_PATH, DOC_TYPES, SRC_ON_DEMAND, SchematicsDatabase, add_user_document,
                                user_index_path, user_schematics_dir)
from ui.theme import (ACCENT_CYAN, FONT_FAMILY, TEXT_SECONDARY, TEXT_PRIMARY, make_button, make_card,
                      make_console_box, make_status_badge)

logger = logging.getLogger("MainWindow")

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")


class SchematicsMixin:
    """Methods of AndroidDoctorApp for the schematics panel (state lives on the app)."""

    # ---------------------------------------------------------------- where the documents live (tests replace these)
    def _schematics_index_path(self) -> str:
        return user_index_path()

    def _schematics_local_dir(self) -> str:
        return user_schematics_dir()

    def _schematics_cache_dir(self):
        return None                      # SchematicsDatabase's default cache folder

    def _schematics_db(self) -> SchematicsDatabase:
        from app_paths import documents_dir
        return SchematicsDatabase(
            index_paths=[BUNDLED_PATH, self._schematics_index_path()],
            local_dirs=[self._schematics_local_dir(),
                        os.path.join(documents_dir(), "Android Doctor Pro", "Schematics")],
            cache_dir=self._schematics_cache_dir())

    # ---------------------------------------------------------------- building
    def _build_schematics_card(self, wrap) -> None:
        L = self._layout()
        self.schem_card, _ = make_card(wrap, self._L(self._tr("card_schematics")), ACCENT_CYAN, L)
        small = ctk.CTkFont(family=FONT_FAMILY, size=12)

        top = ctk.CTkFrame(self.schem_card, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(0, 6))
        self.btn_schem_phone = make_button(top, self._L(self._tr("btn_schem_for_phone")),
                                           self._schematics_find_for_phone, variant="primary", height=34)
        self.btn_schem_phone.pack(side=L["lead"], padx=(0, 6))
        make_button(top, self._L(self._tr("btn_schem_add")), self._schematics_add_dialog,
                    variant="success", height=34).pack(side=L["lead"], padx=6)
        make_button(top, self._L(self._tr("btn_schem_folder")), self._schematics_open_folder,
                    variant="secondary", height=34).pack(side=L["lead"], padx=6)

        search = ctk.CTkFrame(self.schem_card, fg_color="transparent")
        search.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkLabel(search, text=self._L(self._tr("ui.schem_model")), font=small,
                     text_color=TEXT_SECONDARY).pack(side=L["lead"], padx=(0, 6))
        self.schem_model_entry = ctk.CTkEntry(search, width=130)
        self.schem_model_entry.pack(side=L["lead"], padx=4)
        ctk.CTkLabel(search, text=self._L(self._tr("ui.schem_component")), font=small,
                     text_color=TEXT_SECONDARY).pack(side=L["lead"], padx=(10, 6))
        self.schem_component_entry = ctk.CTkEntry(search, width=130)
        self.schem_component_entry.pack(side=L["lead"], padx=4)
        self._schem_type_labels = {self._tr("ui.schem_type_all"): ""}
        self._schem_type_labels.update({self._tr(f"schem.type.{d}"): d for d in DOC_TYPES})
        self.schem_type_combo = ctk.CTkComboBox(search, values=list(self._schem_type_labels), width=130,
                                                state="readonly")
        self.schem_type_combo.set(self._tr("ui.schem_type_all"))
        self.schem_type_combo.pack(side=L["lead"], padx=(10, 4))
        make_button(search, self._L(self._tr("btn_schem_search")), self._schematics_search,
                    variant="cyan", height=32).pack(side=L["lead"], padx=6)

        self.schem_text = make_console_box(self.schem_card, 80, L, self._L(self._tr("ui.schem_hint")))
        self.schem_rows = ctk.CTkFrame(self.schem_card, fg_color="transparent")
        self.schem_rows.pack(fill="x", padx=14, pady=(0, 12))
        self._schem_last_query = None
        self.last_schematics = []

    # ---------------------------------------------------------------- searching
    def _schematics_find_for_phone(self) -> None:
        """Match on the connected phone's own model, codename and maker (read from the phone)."""
        serial = self.current_serial.get()
        if not serial:
            self._set_box(self.schem_text, self._tr("ui.schem_no_phone"))
            return
        self._set_box(self.schem_text, self._tr("ui.schem_reading_phone"))

        def task():
            try:
                model = (self.bridge.get_prop("ro.product.model", serial=serial) or "").strip()
                codename = (self.bridge.get_prop("ro.product.device", serial=serial) or "").strip()
                maker = (self.bridge.get_prop("ro.product.manufacturer", serial=serial) or "").strip()
            except Exception as e:
                logger.error(f"Schematics: reading the phone failed: {e}")
                model = codename = maker = ""
            query = dict(model=model, codename=codename, manufacturer=maker)
            header = [self._tr("ui.schem_device", model=model or "-", codename=codename or "-", maker=maker or "-")]
            if not (model or codename):
                self.after(0, lambda: self._set_box(self.schem_text, "\n".join(
                    header + [self._tr("ui.schem_no_phone_props")])))
                return
            self._schematics_run(query, header)

            def fill():
                self.schem_model_entry.delete(0, "end")
                self.schem_model_entry.insert(0, model)
            self.after(0, fill)
        threading.Thread(target=task, daemon=True).start()

    def _schematics_search(self) -> None:
        model = self.schem_model_entry.get().strip()
        component = self.schem_component_entry.get().strip()
        doc_type = self._schem_type_labels.get(self.schem_type_combo.get(), "")
        if not model and not component:
            self._set_box(self.schem_text, self._tr("ui.schem_need_query"))
            return
        query = dict(model=model, component=component, doc_type=doc_type)
        threading.Thread(target=lambda: self._schematics_run(query, []), daemon=True).start()

    def _schematics_run(self, query, header) -> None:
        """Look up and resolve (runs in a worker thread), then show the results on the UI thread."""
        try:
            db = self._schematics_db()
            results = db.find(**query)
            errors = list(db.errors)
        except Exception as e:
            logger.error(f"Schematics lookup failed: {e}")
            results, errors = [], [str(e)]
        self._schem_last_query = (query, header)
        self.after(0, lambda: self._schematics_show(results, header, query, errors))

    # ---------------------------------------------------------------- showing
    def _schematics_show(self, results, header, query, errors) -> None:
        self.last_schematics = list(results)
        for child in self.schem_rows.winfo_children():
            child.destroy()
        lines = list(header)
        if errors:
            lines.append(self._tr("ui.schem_index_errors", count=len(errors), first=errors[0]))
        what = " / ".join(v for v in (query.get("model"), query.get("codename"), query.get("component")) if v) or "-"
        if not results:
            lines.append(self._tr("ui.schem_none", query=what))
        else:
            lines.append(self._tr("ui.schem_count", count=len(results)))
            lines += [f"  [{r.status}] {self._m(r.detail)}" for r in results]
        self._set_box(self.schem_text, "\n".join(lines))
        L = self._layout()
        small = ctk.CTkFont(family=FONT_FAMILY, size=12)
        for r in results:
            row = ctk.CTkFrame(self.schem_rows, fg_color="transparent")
            row.pack(fill="x", pady=2)
            make_status_badge(row, str(r.status)).pack(side=L["lead"], padx=(0, 8))
            text = " | ".join([r.doc.component, self._tr(f"schem.type.{r.doc.doc_type}"),
                               self._tr(f"schem.origin.{r.origin}")])
            ctk.CTkLabel(row, text=self._L(text), font=small, text_color=TEXT_PRIMARY,
                         anchor=L["anchor"]).pack(side=L["lead"], fill="x", expand=True)
            if r.status == Status.PASS and r.path:
                make_button(row, self._L(self._tr("btn_schem_open")), lambda p=r.path: self._schematics_open(p),
                            variant="success", height=28).pack(side=L["trail"], padx=4)
            elif r.origin == SRC_ON_DEMAND:
                make_button(row, self._L(self._tr("btn_schem_fetch")), lambda res=r: self._schematics_fetch(res),
                            variant="cyan", height=28).pack(side=L["trail"], padx=4)

    def _schematics_refresh(self, note: str = "") -> None:
        """Run the last search again; `note` (e.g. a fetch outcome) stays at the top so a FAIL is not lost."""
        if self._schem_last_query is not None:
            query, header = self._schem_last_query
            first = [h for h in header if h != note]
            lines = first + ([note] if note else [])
            threading.Thread(target=lambda: self._schematics_run(query, lines), daemon=True).start()

    # ---------------------------------------------------------------- opening, fetching, adding
    def _schematics_open(self, path: str) -> None:
        """Images are shown inside the program; PDFs and other files open in the system's own viewer."""
        if os.path.splitext(path)[1].lower() in IMAGE_EXTS:
            try:
                self._schematics_show_image(path)
                return
            except Exception as e:
                logger.warning(f"Schematics: image viewer failed, opening externally: {e}")
        try:
            opener = getattr(os, "startfile", None)
            if opener is not None:
                opener(path)
            else:
                webbrowser.open("file:///" + os.path.abspath(path).replace("\\", "/"))
        except OSError as e:
            self._append_box(self.schem_text, self._tr("ui.schem_open_failed", error=str(e)))

    def _schematics_show_image(self, path: str) -> None:
        from PIL import Image
        img = Image.open(path)
        img.load()
        w, h = img.size
        scale = min(1.0, 1100 / max(w, 1), 760 / max(h, 1))
        size = (max(1, int(w * scale)), max(1, int(h * scale)))
        win = ctk.CTkToplevel(self)
        win.title(os.path.basename(path))
        self._schem_viewer = win
        self._schem_viewer_image = ctk.CTkImage(light_image=img, dark_image=img, size=size)
        ctk.CTkLabel(win, text="", image=self._schem_viewer_image).pack(padx=8, pady=8)

    def _schematics_fetch(self, result) -> None:
        """Download on the technician's request only, checked against the published sha256."""
        self._append_box(self.schem_text, self._tr("ui.schem_fetching", component=result.doc.component))

        def task():
            try:
                res = self._schematics_db().fetch(result.doc)
            except Exception as e:
                logger.error(f"Schematics fetch failed: {e}")
                self.after(0, lambda: self._append_box(self.schem_text, str(e)))
                return

            def done():
                outcome = f"[{res.status}] {self._m(res.detail)}"
                self._append_box(self.schem_text, outcome)
                self._schematics_refresh(note=outcome)
            self.after(0, done)
        threading.Thread(target=task, daemon=True).start()

    def _schematics_add_from(self, path, doc_type, component, model="", codename="", manufacturer=""):
        """Add one file to the technician's index (no dialog); returns the SchematicDoc or None."""
        doc, msg = add_user_document(path, doc_type, component,
                                     models=[model] if model else [], codenames=[codename] if codename else [],
                                     manufacturer=manufacturer, index_path=self._schematics_index_path(),
                                     local_dir=self._schematics_local_dir())
        self._set_box(self.schem_text, self._m(msg))
        if doc is not None:
            query = dict(model=model, codename=codename) if (model or codename) else dict(component=component)
            threading.Thread(target=lambda: self._schematics_run(query, [self._m(msg)]), daemon=True).start()
        return doc

    def _schematics_add_dialog(self) -> None:
        L = self._layout()
        win = ctk.CTkToplevel(self)
        win.title(self._tr("ui.schem_add_title"))
        win.transient(self)
        fields = {}
        for key, initial in (("ui.schem_model", self.schem_model_entry.get().strip()), ("ui.schem_codename", ""),
                             ("ui.schem_component", self.schem_component_entry.get().strip())):
            row = ctk.CTkFrame(win, fg_color="transparent")
            row.pack(fill="x", padx=14, pady=4)
            ctk.CTkLabel(row, text=self._L(self._tr(key)), width=140, anchor=L["anchor"]).pack(side=L["lead"])
            entry = ctk.CTkEntry(row, width=240)
            entry.insert(0, initial)
            entry.pack(side=L["lead"], padx=6)
            fields[key] = entry
        types = {self._tr(f"schem.type.{d}"): d for d in DOC_TYPES}
        type_combo = ctk.CTkComboBox(win, values=list(types), state="readonly", width=240)
        type_combo.set(self._tr("schem.type.schematic"))
        type_combo.pack(padx=14, pady=4)
        ctk.CTkLabel(win, text=self._L(self._tr("ui.schem_add_note")), text_color=TEXT_SECONDARY,
                     wraplength=420, justify=L["justify"]).pack(padx=14, pady=4)

        def pick():
            path = filedialog.askopenfilename(
                parent=win, title=self._tr("ui.schem_add_title"),
                filetypes=[(self._tr("ui.schem_files"), "*.pdf *.png *.jpg *.jpeg *.bmp *.tvw *.brd *.bdv *.fz *.cad"),
                           (self._tr("ui.all_files"), "*.*")])
            if not path:
                return
            doc = self._schematics_add_from(path, types.get(type_combo.get(), ""),
                                            fields["ui.schem_component"].get().strip(),
                                            fields["ui.schem_model"].get().strip(),
                                            fields["ui.schem_codename"].get().strip())
            if doc is not None:
                win.destroy()
        make_button(win, self._L(self._tr("btn_schem_pick_add")), pick, variant="success",
                    height=34).pack(padx=14, pady=(6, 14))

    def _schematics_open_folder(self) -> None:
        folder = self._schematics_local_dir()
        try:
            os.makedirs(folder, exist_ok=True)
            self._schematics_open(folder)
        except OSError as e:
            self._set_box(self.schem_text, self._tr("ui.schem_open_failed", error=str(e)))
