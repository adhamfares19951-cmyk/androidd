"""
Translations.

All user-facing text lives in locales/<lang>.json (one file per language, same keys in each).
Code refers to text only by key:
    t("key", lang, name=value)      -> formatted string
    render(msg_or_value, lang)      -> string for a core Msg, a serialized {"key", "args"} dict, or a plain value
Placeholders use str.format syntax ({name}, {value:.1f}). Arguments that are themselves messages
are rendered in the same language before formatting.
"""

import json
import logging
import os
from typing import Any, Dict, List

import arabic_reshaper
from bidi.algorithm import get_display

from core.result import Msg, Lines, set_message_renderer

logger = logging.getLogger("i18n")

LOCALES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "locales")
DEFAULT_LANG = "ar"
FALLBACK_LANG = "en"

# Configure Arabic reshaper
reshaper = arabic_reshaper.ArabicReshaper(
    configuration={
        'delete_harakat': False,
        'support_ligatures': True,
        'RIAL SIGN': True,
    }
)


def fix_arabic(text: str) -> str:
    """
    Reshapes Arabic letters and reorders bidirectional text for Tkinter LTR rendering engine.
    Ensures letters are properly connected and sentences read in natural Right-to-Left order.
    """
    if not text or not isinstance(text, str):
        return text if text is not None else ""

    ranges = ((0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF), (0xFB50, 0xFDFF), (0xFE70, 0xFEFF))
    has_arabic = any(lo <= ord(c) <= hi for c in text for lo, hi in ranges)
    if not has_arabic:
        return text

    try:
        if "\n" in text:
            lines = text.split("\n")
            processed = [get_display(reshaper.reshape(line)) if line.strip() else line for line in lines]
            return "\n".join(processed)
        return get_display(reshaper.reshape(text))
    except Exception as e:
        logger.debug(f"Arabic reshape error: {e}")
        return text


def load_locales(directory: str = LOCALES_DIR) -> Dict[str, Dict[str, str]]:
    strings: Dict[str, Dict[str, str]] = {}
    for name in sorted(os.listdir(directory)):
        if name.endswith(".json"):
            with open(os.path.join(directory, name), "r", encoding="utf-8") as f:
                strings[name[:-5]] = json.load(f)
    return strings


STRINGS: Dict[str, Dict[str, str]] = load_locales()


def available_languages() -> List[str]:
    return sorted(STRINGS.keys())


def _is_msg_dict(value: Any) -> bool:
    return isinstance(value, dict) and "key" in value and set(value) <= {"key", "args"}


def t(key: str, lang: str = DEFAULT_LANG, **kwargs: Any) -> str:
    """Translated text for `key`, formatted with kwargs. A missing key returns the key itself (and is logged)."""
    lang_dict = STRINGS.get(lang) or STRINGS.get(DEFAULT_LANG, {})
    template = lang_dict.get(key)
    if template is None:
        template = STRINGS.get(FALLBACK_LANG, {}).get(key)
        if template is None:
            logger.warning(f"Missing translation key: {key}")
            return key
    if not kwargs:
        return template
    args = {k: _render_arg(v, lang) for k, v in kwargs.items()}
    try:
        return template.format(**args)
    except (KeyError, ValueError, IndexError) as e:
        logger.warning(f"Bad placeholders for key {key}: {e}")
        return template


def _render_arg(value: Any, lang: str) -> Any:
    """Format argument: messages are rendered, lists joined, None shown as UNKNOWN, numbers kept for format specs."""
    if value is None:
        return "UNKNOWN"
    if isinstance(value, Msg) or _is_msg_dict(value):
        return render(value, lang)
    if isinstance(value, Lines):
        return "\n".join(render(v, lang) for v in value)
    if isinstance(value, (list, tuple)):
        return ", ".join(render(v, lang) for v in value) or "-"
    return value


CURRENT_LANG = DEFAULT_LANG


def set_current_language(lang: str) -> None:
    """Language used when a core Msg is turned into text with str() / f-strings."""
    global CURRENT_LANG
    CURRENT_LANG = lang if lang in STRINGS else DEFAULT_LANG
    set_message_renderer(lambda m: render(m, CURRENT_LANG))


def render(value: Any, lang: str = DEFAULT_LANG) -> str:
    """Render a core Msg / {"key", "args"} dict / list of them / plain value as text."""
    if value is None:
        return "UNKNOWN"
    if isinstance(value, Msg):
        return t(value.key, lang, **value.args)
    if _is_msg_dict(value):
        return t(value["key"], lang, **(value.get("args") or {}))
    if isinstance(value, (list, tuple)):
        return "\n".join(render(v, lang) for v in value)
    return str(value)
