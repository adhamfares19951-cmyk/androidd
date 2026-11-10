"""Design system, RTL text helpers, and reusable CustomTkinter components for Android Doctor Pro."""

from __future__ import annotations

from typing import Any, Callable, Iterable, Sequence
import customtkinter as ctk

# --- Dark-Obsidian Hardware Bench Palette ---
BG_CANVAS = "#090d16"
BG_SIDEBAR = "#0d1320"
BG_CARD = "#111927"
BG_CARD_ELEVATED = "#162033"
BG_TILE = "#0c121e"
BG_TILE_ALT = "#0f1726"
BG_CONSOLE = "#070b12"
BG_HERO = "#101829"
BG_INPUT = "#0c1322"

BORDER_SUBTLE = "#1e2a3f"
BORDER_ACCENT = "#2b3e5e"
BORDER_CONSOLE = "#1a2436"

TEXT_PRIMARY = "#f1f5f9"
TEXT_SECONDARY = "#94a3b8"
TEXT_MUTED = "#64748b"

ACCENT_BLUE = "#3b82f6"
ACCENT_BLUE_HOVER = "#2563eb"
ACCENT_CYAN = "#06b6d4"
ACCENT_CYAN_HOVER = "#0891b2"
ACCENT_GREEN = "#10b981"
ACCENT_GREEN_HOVER = "#059669"
ACCENT_AMBER = "#f59e0b"
ACCENT_AMBER_HOVER = "#d97706"
ACCENT_RED = "#ef4444"
ACCENT_RED_HOVER = "#dc2626"
ACCENT_PURPLE = "#8b5cf6"
ACCENT_PURPLE_HOVER = "#7c3aed"
ACCENT_SLATE = "#1e293b"
ACCENT_SLATE_HOVER = "#334155"

FONT_FAMILY = "Segoe UI"
FONT_MONO = "Cascadia Mono"

# Palette lookup for status badge pills (background, text, border)
STATUS_STYLES: dict[str, tuple[str, str, str]] = {
    "PASS": ("#064e3b", "#6ee7b7", "#10b981"),
    "FAIL": ("#7f1d1d", "#fca5a5", "#ef4444"),
    "UNKNOWN": ("#1e293b", "#cbd5e1", "#475569"),
    "NEEDS_ROOT": ("#78350f", "#fcd34d", "#f59e0b"),
    "--": ("#111827", "#64748b", "#1e293b"),
}

# Semantic button variant styles: (fg_color, hover_color, border_color, border_width, text_color)
BUTTON_VARIANTS: dict[str, tuple[str, str, str, int, str]] = {
    "primary": (ACCENT_BLUE, ACCENT_BLUE_HOVER, "#60a5fa", 1, "#ffffff"),
    "secondary": (ACCENT_SLATE, ACCENT_SLATE_HOVER, BORDER_ACCENT, 1, TEXT_PRIMARY),
    "success": ("#059669", "#047857", "#34d399", 1, "#ffffff"),
    "warning": ("#b45309", "#92400e", "#f59e0b", 1, "#ffffff"),
    "danger": ("#b91c1c", "#991b1b", "#f87171", 1, "#ffffff"),
    "purple": ("#6d28d9", "#5b21b6", "#a78bfa", 1, "#ffffff"),
    "cyan": ("#0e7490", "#155e75", "#22d3ee", 1, "#ffffff"),
    "ghost": ("transparent", "#162033", BORDER_SUBTLE, 1, TEXT_SECONDARY),
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


def get_layout(lang: str) -> dict[str, Any]:
    """Return directional layout attributes for the requested language."""
    return LAYOUT.get(lang, LAYOUT["en"])


def rtl_wrap(text: str, lang: str) -> str:
    """Wrap each non-empty line in RLE/PDF markers when displaying Arabic in Tk 8.6.

    Preserves native Windows DirectWrite letter shaping while preventing trailing
    punctuation and mixed ASCII tokens from flipping to the wrong side.
    """
    if lang != "ar" or not text:
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
    """Create a bordered top banner header for a tab with optional accent stripe."""
    hero = ctk.CTkFrame(
        parent,
        width=10,
        fg_color=BG_HERO,
        corner_radius=12,
        border_width=1,
        border_color=BORDER_ACCENT,
    )
    hero.pack(fill="x", pady=(0, 12))

    # Top subtle accent bar
    stripe = ctk.CTkFrame(hero, width=10, height=3, fg_color=accent_color, corner_radius=0)
    stripe.pack(fill="x", padx=12, pady=(0, 8))

    body = ctk.CTkFrame(hero, width=10, fg_color="transparent")
    body.pack(fill="x", padx=16, pady=(2, 12))

    title_lbl = ctk.CTkLabel(
        body,
        text=title,
        font=ctk.CTkFont(family=FONT_FAMILY, size=19, weight="bold"),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=TEXT_SECONDARY,
            anchor=layout["anchor"],
            justify=layout["justify"],
            wraplength=700,
        )
        sub_lbl.pack(fill="x", pady=(2, 0))

    return hero


def make_status_badge(parent: Any, status: str = "--") -> ctk.CTkLabel:
    """Create a compact pill badge representing a diagnostic verdict status."""
    bg, fg, _ = STATUS_STYLES.get(status, STATUS_STYLES["UNKNOWN"])
    badge = ctk.CTkLabel(
        parent,
        text=status,
        fg_color=bg,
        text_color=fg,
        corner_radius=6,
        padx=10,
        pady=2,
        font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
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
    """Create a modern bordered card container with colored accent pill and optional status badge."""
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
    hdr.pack(fill="x", padx=14, pady=(12, 8))

    accent_pill = ctk.CTkFrame(hdr, width=4, height=18, corner_radius=2, fg_color=accent_color)
    accent_pill.pack(side=layout["lead"], padx=(0, 8) if layout["lead"] == "left" else (8, 0))

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

    divider = ctk.CTkFrame(card, width=10, height=1, fg_color=BORDER_SUBTLE)
    divider.pack(fill="x", padx=14, pady=(0, 8))

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
        text_color=TEXT_PRIMARY,
        font=ctk.CTkFont(family=FONT_FAMILY, size=12),
        wrap="word",
    )
    box.pack(fill="x", padx=padx, pady=pady)
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
        if lang == "ar":
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
        "corner_radius": 8,
        "height": height,
        "font": ctk.CTkFont(
            family=FONT_FAMILY,
            size=font_size,
            weight="bold" if bold else "normal",
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
    container = ctk.CTkFrame(
        parent,
        width=10,
        fg_color=BG_CARD,
        corner_radius=10,
        border_width=1,
        border_color=BORDER_SUBTLE,
    )
    container.pack(fill="x", pady=pady)

    inner = ctk.CTkFrame(container, width=10, fg_color="transparent")
    inner.pack(fill="x", padx=10, pady=10)

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
    """Create a dark KPI/telemetry metric tile with top accent stripe and large readout."""
    tile = ctk.CTkFrame(
        parent,
        width=10,
        fg_color=BG_TILE,
        corner_radius=10,
        border_width=1,
        border_color=BORDER_SUBTLE,
    )
    top_bar = ctk.CTkFrame(tile, width=10, height=3, fg_color=accent_color, corner_radius=2)
    top_bar.pack(fill="x", padx=12, pady=(0, 6))

    lbl_title = ctk.CTkLabel(
        tile,
        text=title,
        font=ctk.CTkFont(family=FONT_FAMILY, size=11),
        text_color=TEXT_SECONDARY,
        anchor="center",
        justify="center",
        wraplength=150,
    )
    lbl_title.pack(fill="x", padx=8, pady=(2, 2))

    lbl_val = ctk.CTkLabel(
        tile,
        text=value_text,
        font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
        text_color=accent_color,
        anchor="center",
        justify="center",
        wraplength=160,
    )
    lbl_val.pack(fill="x", padx=8, pady=(0, 12))

    return tile, lbl_val


def style_entry(entry: ctk.CTkEntry, layout: dict[str, Any] | None = None) -> None:
    """Apply consistent obsidian styling to a CTkEntry widget."""
    kwargs: dict[str, Any] = {
        "fg_color": BG_INPUT,
        "border_color": BORDER_ACCENT,
        "border_width": 1,
        "text_color": TEXT_PRIMARY,
        "corner_radius": 8,
    }
    if layout is not None:
        kwargs["justify"] = layout["justify"]
    entry.configure(**kwargs)
