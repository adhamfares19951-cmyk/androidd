"""
Where Android Doctor Pro reads resources and writes user data.

Run from source: everything stays in the project folder (data/android_doctor.db, reports/), exactly
as before. Packaged build (PyInstaller, sys.frozen): the install folder may be read-only
(Program Files), so the database goes to %LOCALAPPDATA%\\AndroidDoctorPro\\ and reports to
Documents\\Android Doctor Pro\\Reports\\. Read-only resources (locales/, data/*.json, bin/) are always
next to the code (sys._MEIPASS inside a build).
"""

import os
import sys

APP_NAME = "AndroidDoctorPro"
FROZEN = bool(getattr(sys, "frozen", False))
SOURCE_ROOT = os.path.dirname(os.path.abspath(__file__))


def install_dir() -> str:
    """The folder the program runs from (the exe's folder in a build). Signing keys must not live here."""
    return os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else SOURCE_ROOT


def user_data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, APP_NAME)


def documents_dir() -> str:
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(profile, "Documents")


def default_db_path() -> str:
    if FROZEN:
        return os.path.join(user_data_dir(), "android_doctor.db")
    return os.path.join(SOURCE_ROOT, "data", "android_doctor.db")


def default_reports_dir() -> str:
    if FROZEN:
        return os.path.join(documents_dir(), "Android Doctor Pro", "Reports")
    return os.path.join(SOURCE_ROOT, "reports")


def default_uart_dir() -> str:
    """Where live UART captures are saved (uart_logs/ from source, Documents\\Android Doctor Pro\\UART Logs built)."""
    if FROZEN:
        return os.path.join(documents_dir(), "Android Doctor Pro", "UART Logs")
    return os.path.join(SOURCE_ROOT, "uart_logs")


def safe_name(text: str) -> str:
    """A file/folder name part that is valid on Windows: wireless serials like 192.168.1.5:5555 contain ':'."""
    import re
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "_", str(text or "")).strip("._")
    return cleaned or "device"


def desktop_dir() -> str:
    profile = os.environ.get("USERPROFILE") or os.path.expanduser("~")
    return os.path.join(profile, "Desktop")
