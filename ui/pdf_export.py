"""
PDF copy of an HTML diagnostic report, printed by the Microsoft Edge (or Google Chrome) that is
already on the PC, in headless mode. Nothing is downloaded or installed.

- The browser runs with its own temporary profile: the user's browser profile, tabs and logins are
  never touched.
- The result is accepted only if the file exists and starts with "%PDF"; otherwise PdfExportError.
- With a signing key the PDF gets its own detached `<report>.pdf.sig`, exactly like the HTML.
Errors carry a Msg so the UI/CLI show them in the user's language.
"""

import os
import pathlib
import subprocess
import tempfile
from typing import Optional, Tuple

from core.result import Msg, M
from setup_bins import CREATE_NO_WINDOW


class PdfExportError(Exception):
    def __init__(self, msg: Msg):
        super().__init__(msg.key)
        self.msg = msg


def browser_candidates() -> list:
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    local = os.environ.get("LOCALAPPDATA", "")
    rel_edge = os.path.join("Microsoft", "Edge", "Application", "msedge.exe")
    rel_chrome = os.path.join("Google", "Chrome", "Application", "chrome.exe")
    paths = [os.path.join(pf86, rel_edge), os.path.join(pf, rel_edge),
             os.path.join(pf, rel_chrome), os.path.join(pf86, rel_chrome)]
    if local:
        paths += [os.path.join(local, rel_edge), os.path.join(local, rel_chrome)]
    return paths


def find_browser() -> Optional[str]:
    for path in browser_candidates():
        if os.path.isfile(path):
            return path
    return None


def default_pdf_path(html_path: str) -> str:
    base, _ = os.path.splitext(html_path)
    return base + ".pdf"


def html_to_pdf(html_path: str, pdf_path: Optional[str] = None, browser: Optional[str] = None,
                timeout: int = 90) -> str:
    """Print `html_path` to PDF. Returns the PDF path; raises PdfExportError."""
    if not os.path.isfile(html_path):
        raise PdfExportError(M("pdf.err.no_report", path=html_path))
    browser = browser or find_browser()
    if not browser:
        raise PdfExportError(M("pdf.err.no_browser"))
    pdf_path = os.path.abspath(pdf_path or default_pdf_path(html_path))
    if os.path.exists(pdf_path):
        os.remove(pdf_path)  # never mistake an old file for this run's result

    uri = pathlib.Path(html_path).resolve().as_uri()
    with tempfile.TemporaryDirectory(prefix="adp_pdf_") as profile:
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--disable-sync", f"--user-data-dir={profile}",
               "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}", uri]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                  creationflags=CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise PdfExportError(M("pdf.err.timeout", seconds=timeout))
        except OSError as e:
            raise PdfExportError(M("pdf.err.failed", error=str(e)))

    try:
        with open(pdf_path, "rb") as f:
            header = f.read(5)
    except OSError:
        header = b""
    if header != b"%PDF-":
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or [f"exit {proc.returncode}"]
        raise PdfExportError(M("pdf.err.failed", error=detail[0][:300]))
    return pdf_path


def export_pdf(html_path: str, signing_key_path: Optional[str] = None,
               pdf_path: Optional[str] = None) -> Tuple[str, Optional[str]]:
    """PDF copy of a report, signed when a key is given. Returns (pdf_path, signature_path or None).
    The key is loaded before printing, so a bad key fails before any file is written."""
    key = None
    if signing_key_path:
        from core.report_signing import load_private_key
        key = load_private_key(signing_key_path)
    pdf = html_to_pdf(html_path, pdf_path)
    sig = None
    if key is not None:
        from core.report_signing import sign_file
        sig = sign_file(pdf, key)
    return pdf, sig
