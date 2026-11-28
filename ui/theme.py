"""Design system, RTL text helpers, and reusable CustomTkinter components for Android Doctor Pro."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence
import customtkinter as ctk

# --- Palette: dark + light. Every colour is a (light, dark) pair; CustomTkinter picks the side that
# matches ctk.get_appearance_mode(). Colour carries meaning: blue = main action / selection,
# green/red/amber = a check's result, red buttons = destructive only. Everything else is neutral.
#                     (light,       dark)
BG_CANVAS        = ("#eef1f6", "#0b0d12")
BG_SIDEBAR       = ("#ffffff", "#0c0e13")
BG_CARD          = ("#ffffff", "#171a22")
BG_CARD_ELEVATED = ("#f6f8fb", "#20242e")
BG_TILE          = ("#eef1f6", "#14161d")
BG_TILE_ALT      = ("#e7ebf2", "#1b1e26")
BG_CONSOLE       = ("#f5f7fb", "#0e1014")
BG_HERO          = BG_CANVAS
BG_INPUT         = ("#ffffff", "#12141a")
BG_HOVER         = ("#e8edf5", "#262b36")
BG_SELECTED      = ("#e6effd", "#1f2937")

BORDER_SUBTLE    = ("#e2e6ee", "#2b3140")
BORDER_ACCENT    = ("#cdd4e0", "#3a4150")
BORDER_CONSOLE   = ("#dfe4ed", "#242a36")

TEXT_PRIMARY     = ("#1a1d24", "#f4f5f8")
TEXT_SECONDARY   = ("#515867", "#b6bbc8")
TEXT_MUTED       = ("#878e9c", "#7a8090")

ACCENT_BLUE        = ("#2f6fed", "#5b93ff")
ACCENT_BLUE_HOVER  = ("#255fd6", "#4a82f5")
ACCENT_GREEN       = ("#1a9d47", "#45c565")
ACCENT_GREEN_HOVER = ("#15833b", "#2ea043")
ACCENT_AMBER       = ("#b9770d", "#e0a92e")
ACCENT_AMBER_HOVER = ("#9c6409", "#c48f1f")
ACCENT_RED         = ("#d92d20", "#f8665e")
ACCENT_RED_HOVER   = ("#b8241a", "#e04b43")
ACCENT_CYAN        = ("#0e97ab", "#24c3d6")
ACCENT_CYAN_HOVER  = ("#0b8193", "#1aa7b8")
ACCENT_PURPLE      = ("#7c54e8", "#a78bfa")
ACCENT_PURPLE_HOVER= ("#6a43d6", "#9168f5")
ACCENT_SLATE       = BG_CARD_ELEVATED
ACCENT_SLATE_HOVER = BG_HOVER


def resolve(color: Any) -> str:
    """Pick the right side of a (light, dark) pair for the current appearance mode. For raw tk widgets
    (e.g. a tk.Canvas) that cannot take a CustomTkinter colour tuple."""
    if isinstance(color, (tuple, list)) and len(color) == 2:
        return color[0] if ctk.get_appearance_mode() == "Light" else color[1]
    return color

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
STATUS_STYLES: dict[str, tuple] = {
    "PASS": (("#e4f6ea", "#12261a"), ("#1a7f37", "#56d364"), ACCENT_GREEN),
    "FAIL": (("#fdeceb", "#2d1517"), ("#c4322a", "#ff7b72"), ACCENT_RED),
    "UNKNOWN": (("#eef1f6", "#1f2125"), ("#6a7180", "#a1a4ab"), ("#d7dce6", "#3a3d44")),
    "NEEDS_ROOT": (("#fbf0d8", "#2b2112"), ("#9a6a07", "#e3b341"), ACCENT_AMBER),
    "--": ("transparent", BG_CARD, BG_CARD),
}

# Semantic button variant styles: (fg_color, hover_color, border_color, border_width, text_color)
_SECONDARY = (BG_CARD_ELEVATED, BG_HOVER, BORDER_ACCENT, 1, TEXT_PRIMARY)
BUTTON_VARIANTS: dict[str, tuple] = {
    "primary": (ACCENT_BLUE, ACCENT_BLUE_HOVER, ACCENT_BLUE, 0, "#ffffff"),
    "secondary": _SECONDARY,
    # Destructive only (erase, flash, uninstall...): quiet red, never a filled red block.
    "danger": (("#fdeceb", "#2a1416"), ("#fbdddb", "#3a1a1d"), ("#f3b8b3", "#5c2427"), 1, ("#c4322a", "#ff7b72")),
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


_ISOLATE_TO_EMBEDDING = {0x2066: 0x202A, 0x2067: 0x202B, 0x2068: 0x202A, 0x2069: 0x202C}   # LRI/RLI/FSI/PDI


def rtl_wrap(text: str, lang: str) -> str:
    """Wrap each non-empty line in RLE/PDF markers when displaying Arabic in Tk 8.6.

    Preserves native Windows DirectWrite letter shaping while preventing trailing
    punctuation and mixed ASCII tokens from flipping to the wrong side.
    """
    if not text or not _rtl(lang):
        return text
    # Tk draws the Unicode 6.3 isolates (LRI, RLI, FSI, PDI) as boxes. The Arabic texts use them for the HTML
    # report; on screen they become the embeddings Tk supports, so numbers and dates still stay left-to-right.
    text = str(text).translate(_ISOLATE_TO_EMBEDDING)
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
        font=ctk.CTkFont(family=FONT_FAMILY, size=26, weight="bold"),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=15),
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
        corner_radius=16,
        border_width=1,
        border_color=BORDER_SUBTLE,
    )
    if pack_card:
        card.pack(fill="x", pady=pady)

    hdr = ctk.CTkFrame(card, width=10, fg_color="transparent")
    hdr.pack(fill="x", padx=18, pady=(16, 12))

    # A small colour dot before the title gives each card a point of life without colouring the whole surface.
    dot = ctk.CTkFrame(hdr, width=10, height=10, corner_radius=5,
                       fg_color=accent_color or ACCENT_BLUE)
    dot.pack(side=layout["lead"], padx=(0, 10))
    dot.pack_propagate(False)

    eff_wrap = title_wrap if title_wrap is not None else (640 if pack_card else 290)
    title_lbl = ctk.CTkLabel(
        hdr,
        text=title,
        font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
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
        corner_radius=10,
        text_color=TEXT_SECONDARY,
        font=ctk.CTkFont(family=FONT_FAMILY, size=14),
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
    height: int = 38,
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
        "corner_radius": 9,
        "height": height,
        "font": ctk.CTkFont(
            family=FONT_FAMILY,
            size=max(font_size, 13),
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
