"""Diode-mode notebook card (Motherboard tab), on top of core.diode_notes.

The technician types readings measured by hand; the card stores them per model and test point and compares the
board under test with a known-good reference board. Marks are hints to look closer, never a PASS/FAIL."""

import customtkinter as ctk

from core.diode_notes import BOARDS, DIFF_MARK_PCT, NEAR_SHORT_MV, UNMARKED, DiodeNotebook, format_reading
from ui.theme import ACCENT_CYAN, FONT_FAMILY, TEXT_SECONDARY, make_button, make_card, make_console_box


class DiodeNotesMixin:
    """Methods of AndroidDoctorApp for the diode-mode notebook card (state lives on the app)."""

    def _diode_notebook(self) -> DiodeNotebook:          # tests replace this with a notebook in a temp folder
        return DiodeNotebook()

    def _build_diode_card(self, wrap) -> None:
        L = self._layout()
        self.diode_card, _ = make_card(wrap, self._L(self._tr("card_diode")), ACCENT_CYAN, L)
        small = ctk.CTkFont(family=FONT_FAMILY, size=12)

        row = ctk.CTkFrame(self.diode_card, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=(0, 6))
        self.diode_entries = {}
        for key, width in (("ui.diode_model", 120), ("ui.diode_point", 110), ("ui.diode_reading", 80)):
            ctk.CTkLabel(row, text=self._L(self._tr(key)), font=small,
                         text_color=TEXT_SECONDARY).pack(side=L["lead"], padx=(0, 4))
            entry = ctk.CTkEntry(row, width=width)
            entry.pack(side=L["lead"], padx=(0, 10))
            self.diode_entries[key] = entry
        self._diode_board_labels = {self._tr(f"diode.board.{b}"): b for b in BOARDS}
        self.diode_board = ctk.CTkSegmentedButton(row, values=list(self._diode_board_labels))
        self.diode_board.set(self._tr("diode.board.test"))
        self.diode_board.pack(side=L["lead"], padx=(0, 4))

        btns = ctk.CTkFrame(self.diode_card, fg_color="transparent")
        btns.pack(fill="x", padx=14, pady=(0, 6))
        make_button(btns, self._L(self._tr("btn_diode_save")), self._diode_save, variant="success",
                    height=32).pack(side=L["lead"], padx=(0, 6))
        make_button(btns, self._L(self._tr("btn_diode_compare")), self._diode_compare, variant="cyan",
                    height=32).pack(side=L["lead"], padx=6)
        self.diode_text = make_console_box(self.diode_card, 200, L, self._L(self._tr("ui.diode_hint")))

    def _diode_model(self) -> str:
        model = self.diode_entries["ui.diode_model"].get().strip()
        if not model and hasattr(self, "schem_model_entry"):      # the phone matched in the schematics card
            model = self.schem_model_entry.get().strip()
            if model:
                self.diode_entries["ui.diode_model"].insert(0, model)
        return model

    def _diode_save(self) -> None:
        board = self._diode_board_labels.get(self.diode_board.get(), "")
        ok, msg = self._diode_notebook().save(self._diode_model(), self.diode_entries["ui.diode_point"].get(),
                                              self.diode_entries["ui.diode_reading"].get(), board)
        if ok:
            reading = self.diode_entries["ui.diode_reading"]
            reading.delete(0, "end")                           # ready for the next point
            self._diode_compare(first_line=self._m(msg))
        else:
            self._set_box(self.diode_text, self._m(msg))

    def _diode_compare(self, first_line: str = "") -> None:
        model = self._diode_model()
        lines = [first_line] if first_line else []
        if not model:
            lines.append(self._tr("diode.no_model"))
            self._set_box(self.diode_text, "\n".join(lines))
            return
        rows = self._diode_notebook().compare(model)
        self.last_diode_rows = rows
        if not rows:
            lines.append(self._tr("ui.diode_none", model=model))
        else:
            lines.append(self._tr("ui.diode_header", model=model, count=len(rows)))
            for r in rows:
                mark = "[ ]" if r.kind in UNMARKED else "[!]"
                # no signs: "80 mV lower" reads the same way in every language (a minus sign jumps sides in Arabic)
                diff = r.diff_mv or 0.0
                direction = self._tr("diode.dir.lower" if diff < 0 else "diode.dir.higher")
                kind = self._tr(f"diode.kind.{r.kind}", diff=f"{abs(diff):g}", dir=direction,
                                pct=f"{abs(r.diff_pct):g}" if r.diff_pct is not None else "-")
                lines.append(self._tr("ui.diode_row", mark=mark, point=r.point, ref=format_reading(r.reference),
                                      test=format_reading(r.test), kind=kind))
            lines.append(self._tr("ui.diode_footer", pct=f"{DIFF_MARK_PCT:g}", mv=f"{NEAR_SHORT_MV:g}"))
        self._set_box(self.diode_text, "\n".join(lines))
