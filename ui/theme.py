"""Design system, RTL text helpers, and reusable CustomTkinter components for Android Doctor Pro."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence
import customtkinter as ctk

# --- Palette: neutral greys, one accent, status colours only for status ---
# Colour carries meaning here: blue = the main action / selection, green/red/amber = a check's
# result, red buttons = destructive actions only. Everything else is neutral.
BG_CANVAS = "#0f1012"
BG_SIDEBAR = "#0b0c0e"
BG_CARD = "#16171a"
BG_CARD_ELEVATED = "#1d1f23"
BG_TILE = "#131417"
BG_TILE_ALT = "#191b1e"
BG_CONSOLE = "#121316"
BG_HERO = BG_CANVAS
BG_INPUT = "#111215"
BG_HOVER = "#22252a"
BG_SELECTED = "#1f2937"

BORDER_SUBTLE = "#24262b"
BORDER_ACCENT = "#30333a"
BORDER_CONSOLE = "#1f2125"

TEXT_PRIMARY = "#ececee"
TEXT_SECONDARY = "#a1a4ab"
TEXT_MUTED = "#6c7079"

ACCENT_BLUE = "#4c8dff"
ACCENT_BLUE_HOVER = "#3b7af0"
ACCENT_GREEN = "#3fb950"
ACCENT_GREEN_HOVER = "#2ea043"
ACCENT_AMBER = "#d29922"
ACCENT_AMBER_HOVER = "#bb8009"
ACCENT_RED = "#f85149"
ACCENT_RED_HOVER = "#da3633"
# Former decorative accents: kept as names for existing callers, now neutral or the one accent.
ACCENT_CYAN = ACCENT_BLUE
ACCENT_CYAN_HOVER = ACCENT_BLUE_HOVER
ACCENT_PURPLE = ACCENT_BLUE
ACCENT_PURPLE_HOVER = ACCENT_BLUE_HOVER
ACCENT_SLATE = BG_CARD_ELEVATED
ACCENT_SLATE_HOVER = BG_HOVER

FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"            # ships with every Windows version
ICON_FONT = "Segoe MDL2 Assets"   # Windows 10/11 system icon font

# Navigation icons (Segoe MDL2 Assets code points)
NAV_ICONS: dict[str, str] = {
    "dashboard": "\uE80F",        # home
    "board_diag": "\uE950",       # chip
    "hardware_tester": "\uE8EA",  # phone
    "malware_hunter": "\uEA18",   # shield
    "optimizer": "\uE90F",        # wrench
    "rescue_center": "\uE777",    # restore
    "reports": "\uE9F9",          # report
}
ICON_REFRESH = "\uE72C"

# Palette lookup for status badge pills (background, text, border)
STATUS_STYLES: dict[str, tuple[str, str, str]] = {
    "PASS": ("#12261a", "#56d364", ACCENT_GREEN),
    "FAIL": ("#2d1517", "#ff7b72", ACCENT_RED),
    "UNKNOWN": ("#1f2125", "#a1a4ab", "#3a3d44"),
    "NEEDS_ROOT": ("#2b2112", "#e3b341", ACCENT_AMBER),
    "--": ("transparent", BG_CARD, BG_CARD),
}

# Semantic button variant styles: (fg_color, hover_color, border_color, border_width, text_color)
_SECONDARY = (BG_CARD_ELEVATED, BG_HOVER, BORDER_ACCENT, 1, TEXT_PRIMARY)
BUTTON_VARIANTS: dict[str, tuple[str, str, str, int, str]] = {
    "primary": (ACCENT_BLUE, ACCENT_BLUE_HOVER, ACCENT_BLUE, 0, "#ffffff"),
    "secondary": _SECONDARY,
    # Destructive only (erase, flash, uninstall...): quiet red, never a filled red block.
    "danger": ("#2a1416", "#3a1a1d", "#5c2427", 1, "#ff7b72"),
    "ghost": ("transparent", BG_HOVER, BG_CANVAS, 0, TEXT_SECONDARY),
    # Old decorative variants render as secondary: colour no longer says nothing.
    "success": _SECONDARY,
    "warning": _SECONDARY,
    "purple": _SECONDARY,
    "cyan": _SECONDARY,
}

# Language-keyed directional layout mapping (avoids language ternaries in UI code)
LAYOUT: dict[str, dict[str, Any]] = {
    "ar": {
        "anchor": "e",
        "justify": "right",
        "lead": "right",
        "trail": "left",
        "is_rtl": True,
    },
    "en": {
        "anchor": "w",
        "justify": "left",
        "lead": "left",
        "trail": "right",
        "is_rtl": False,
    },
}


def _rtl(lang: str) -> bool:
    from ui.i18n import is_rtl
    return is_rtl(lang)


def get_layout(lang: str) -> dict[str, Any]:
    """Directional layout for the language: right-to-left languages use the "ar" layout."""
    return LAYOUT["ar"] if _rtl(lang) else LAYOUT["en"]


def rtl_wrap(text: str, lang: str) -> str:
    """Wrap each non-empty line in RLE/PDF markers when displaying Arabic in Tk 8.6.

    Preserves native Windows DirectWrite letter shaping while preventing trailing
    punctuation and mixed ASCII tokens from flipping to the wrong side.
    """
    if not text or not _rtl(lang):
        return text
    out_lines: list[str] = []
    for line in str(text).splitlines():
        if line and not line.startswith("\u202b"):
            out_lines.append(f"\u202b{line}\u202c")
        else:
            out_lines.append(line)
    suffix = "\n" if str(text).endswith("\n") else ""
    return "\n".join(out_lines) + suffix


def make_hero_header(
    parent: Any,
    title: str,
    subtitle: str,
    layout: dict[str, Any],
    accent_color: str = ACCENT_BLUE,
) -> ctk.CTkFrame:
    """Page title + one-line description, flush with the content (no box, no stripe)."""
    hero = ctk.CTkFrame(parent, width=10, fg_color="transparent", corner_radius=0)
    hero.pack(fill="x", pady=(4, 16))

    body = ctk.CTkFrame(hero, width=10, fg_color="transparent")
    body.pack(fill="x", padx=4, pady=0)

    title_lbl = ctk.CTkLabel(
        body,
        text=title,
        font=ctk.CTkFont(family=FONT_FAMILY, size=22, weight="bold"),
        text_color=TEXT_PRIMARY,
        anchor=layout["anchor"],
        justify=layout["justify"],
        wraplength=700,
    )
    title_lbl.pack(fill="x")

    if subtitle:
        sub_lbl = ctk.CTkLabel(
            body,
            text=subtitle,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=TEXT_SECONDARY,
            anchor=layout["anchor"],
            justify=layout["justify"],
            wraplength=760,
        )
        sub_lbl.pack(fill="x", pady=(4, 0))

    return hero


def make_status_badge(parent: Any, status: str = "--") -> ctk.CTkLabel:
    """Create a compact pill badge representing a diagnostic verdict status."""
    bg, fg, _ = STATUS_STYLES.get(status, STATUS_STYLES["UNKNOWN"])
    badge = ctk.CTkLabel(
        parent,
        text=status,
        fg_color=bg,
        text_color=fg,
        corner_radius=10,
        padx=10,
        pady=1,
        height=22,
        font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
    )
    return badge


def update_status_badge(badge: ctk.CTkLabel | None, status: str) -> None:
    """Safely update an existing status badge pill to reflect a new status."""
    if badge is None:
        return
    try:
        if not badge.winfo_exists():
            return
        norm = str(status).upper() if status else "--"
        bg, fg, _ = STATUS_STYLES.get(norm, STATUS_STYLES["UNKNOWN"])
        badge.configure(text=norm, fg_color=bg, text_color=fg)
    except Exception:
        pass


def make_card(
    parent: Any,
    title: str,
    accent_color: str,
    layout: dict[str, Any],
    show_badge: bool = False,
    pady: tuple[int, int] = (0, 12),
    pack_card: bool = True,
    title_wrap: int | None = None,
) -> tuple[ctk.CTkFrame, ctk.CTkLabel | None]:
    """Card: neutral surface, title and optional status badge. `accent_color` is accepted for old
    callers and ignored: colour is reserved for status."""
    card = ctk.CTkFrame(
        parent,
        width=10,
        fg_color=BG_CARD,
        corner_radius=12,
        border_width=1,
        border_color=BORDER_SUBTLE,
    )
    if pack_card:
        card.pack(fill="x", pady=pady)

    hdr = ctk.CTkFrame(card, width=10, fg_color="transparent")
    hdr.pack(fill="x", padx=16, pady=(14, 10))

    eff_wrap = title_wrap if title_wrap is not None else (640 if pack_card else 290)
    title_lbl = ctk.CTkLabel(
        hdr,
        text=title,
        font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
        text_color=TEXT_PRIMARY,
        anchor=layout["anchor"],
        justify=layout["justify"],
        wraplength=eff_wrap,
    )
    title_lbl.pack(side=layout["lead"], fill="x", expand=True)

    badge: ctk.CTkLabel | None = None
    if show_badge:
        badge = make_status_badge(hdr, "--")
        badge.pack(side=layout["trail"])

    return card, badge


def make_console_box(
    parent: Any,
    height: int,
    layout: dict[str, Any],
    initial_text: str = "",
    padx: int = 14,
    pady: tuple[int, int] = (0, 12),
) -> ctk.CTkTextbox:
    """Create a bordered, dark-slate console CTkTextbox with clean font and RTL justification."""
    box = ctk.CTkTextbox(
        parent,
        height=height,
        fg_color=BG_CONSOLE,
        border_width=1,
        border_color=BORDER_CONSOLE,
        corner_radius=8,
        text_color=TEXT_SECONDARY,
        font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        wrap="word",
        scrollbar_button_color=BORDER_ACCENT,
        scrollbar_button_hover_color=TEXT_MUTED,
    )
    box.pack(fill="x", padx=padx + 2, pady=pady)
    try:
        box._textbox.tag_configure("rtl", justify=layout["justify"])
    except Exception:
        pass
    if initial_text:
        box.insert("1.0", initial_text)
        if layout["is_rtl"]:
            try:
                box._textbox.tag_add("rtl", "1.0", "end")
            except Exception:
                pass
    return box


def set_console_text(box: ctk.CTkTextbox | None, text: str, lang: str) -> None:
    """Replace console textbox contents with RTL-safe text and proper alignment."""
    if box is None:
        return
    try:
        if not box.winfo_exists():
            return
        box.delete("1.0", "end")
        wrapped = rtl_wrap(text, lang)
        box.insert("1.0", wrapped)
        if _rtl(lang):
            box._textbox.tag_configure("rtl", justify="right")
            box._textbox.tag_add("rtl", "1.0", "end")
        else:
            box._textbox.tag_configure("rtl", justify="left")
            box._textbox.tag_add("rtl", "1.0", "end")
    except Exception:
        pass


def make_button(
    parent: Any,
    text: str,
    command: Callable[[], Any] | None,
    variant: str = "secondary",
    height: int = 34,
    width: int | None = None,
    font_size: int = 12,
    bold: bool = True,
) -> ctk.CTkButton:
    """Create a themed button adhering to the unified variant hierarchy."""
    fg, hover, border, bw, tc = BUTTON_VARIANTS.get(variant, BUTTON_VARIANTS["secondary"])
    kwargs: dict[str, Any] = {
        "text": text,
        "command": command,
        "fg_color": fg,
        "hover_color": hover,
        "border_color": border,
        "border_width": bw,
        "text_color": tc,
        "corner_radius": 7,
        "height": height,
        "font": ctk.CTkFont(
            family=FONT_FAMILY,
            size=max(font_size, 12),
            weight="bold" if (bold and variant == "primary") else "normal",
        ),
    }
    if width is not None:
        kwargs["width"] = width
    return ctk.CTkButton(parent, **kwargs)


def pack_button_grid(
    parent: Any,
    specs: Sequence[tuple[str, Callable[[], Any], str]],
    cols: int = 4,
    is_rtl: bool = False,
    pady: tuple[int, int] = (0, 10),
    height: int = 36,
) -> tuple[ctk.CTkFrame, list[ctk.CTkButton]]:
    """Build a responsive, zero-clipping grid toolbar of uniform action buttons.

    Returns (container_frame, list_of_buttons_in_spec_order).
    """
    container = ctk.CTkFrame(parent, width=10, fg_color="transparent", corner_radius=0)
    container.pack(fill="x", pady=pady)

    inner = ctk.CTkFrame(container, width=10, fg_color="transparent")
    inner.pack(fill="x", padx=0, pady=0)

    eff_cols = max(1, min(cols, len(specs))) if specs else 1
    for c in range(eff_cols):
        inner.grid_columnconfigure(c, weight=1, uniform="btn_col")

    wrap_w = max(130, int(680 // eff_cols) - 20)
    buttons: list[ctk.CTkButton] = []
    for idx, (label, cmd, variant) in enumerate(specs):
        row = idx // eff_cols
        col_offset = idx % eff_cols
        col = (eff_cols - 1 - col_offset) if is_rtl else col_offset
        btn = make_button(inner, label, cmd, variant=variant, height=height, font_size=11)
        try:
            btn._text_label.configure(wraplength=wrap_w, justify="center")
        except Exception:
            pass
        btn.grid(row=row, column=col, padx=4, pady=4, sticky="ew")
        buttons.append(btn)

    return container, buttons


def make_telemetry_tile(
    parent: Any,
    title: str,
    value_text: str,
    accent_color: str,
    layout: dict[str, Any],
) -> tuple[ctk.CTkFrame, ctk.CTkLabel]:
    """Metric tile: small caption above a large neutral number (`accent_color` kept for old callers)."""
    tile = ctk.CTkFrame(
        parent,
        width=10,
        fg_color=BG_TILE,
        corner_radius=10,
        border_width=1,
        border_color=BORDER_SUBTLE,
    )
    lbl_title = ctk.CTkLabel(
        tile,
        text=title,
        font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        text_color=TEXT_SECONDARY,
        anchor=layout["anchor"],
        justify=layout["justify"],
        wraplength=170,
    )
    lbl_title.pack(fill="x", padx=14, pady=(12, 0))

    lbl_val = ctk.CTkLabel(
        tile,
        text=value_text,
        font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
        text_color=TEXT_PRIMARY,
        anchor=layout["anchor"],
        justify=layout["justify"],
        wraplength=180,
    )
    lbl_val.pack(fill="x", padx=14, pady=(2, 12))

    return tile, lbl_val


def style_entry(entry: ctk.CTkEntry, layout: dict[str, Any] | None = None) -> None:
    """Apply consistent obsidian styling to a CTkEntry widget."""
    kwargs: dict[str, Any] = {
        "fg_color": BG_INPUT,
        "border_color": BORDER_ACCENT,
        "border_width": 1,
        "text_color": TEXT_PRIMARY,
        "corner_radius": 7,
    }
    if layout is not None:
        kwargs["justify"] = layout["justify"]
    entry.configure(**kwargs)


_ICON_CACHE: dict[tuple[str, str, int], Any] = {}  # drawn PIL images (not Tk objects)


def icon_image(glyph: str, color: str, size: int = 18) -> Any:
    """A CTkImage of one Segoe MDL2 Assets glyph, or None when the icon font is not installed.

    Drawn at 2x and scaled down by CustomTkinter, so it stays sharp on high-DPI screens. Only the
    PIL image is cached: a CTkImage holds Tk images that die with their window.
    """
    key = (glyph, color, size)
    if key not in _ICON_CACHE:
        img = None
        try:
            import os
            from PIL import Image, ImageDraw, ImageFont

            font_path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", "segmdl2.ttf")
            if os.path.isfile(font_path):
                px = size * 2
                font = ImageFont.truetype(font_path, int(px * 0.9))
                img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
                draw = ImageDraw.Draw(img)
                left, top, right, bottom = draw.textbbox((0, 0), glyph, font=font)
                draw.text(((px - (right - left)) / 2 - left, (px - (bottom - top)) / 2 - top), glyph, font=font, fill=color)
        except Exception:
            img = None
        _ICON_CACHE[key] = img
    img = _ICON_CACHE[key]
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size)) if img is not None else None
