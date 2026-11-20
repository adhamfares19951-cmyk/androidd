"""Lazy exports.

Importing a ``ui`` submodule (e.g. ``from ui.i18n import t`` in --cli mode) runs this
package initializer first. Importing the GUI class here eagerly would pull in
tkinter/customtkinter for every console run too, so the heavy imports are deferred via
PEP 562 ``__getattr__`` and only happen when the GUI symbol is actually accessed.
"""
__all__ = ["AndroidDoctorApp", "DiagnosticReportExporter"]


def __getattr__(name):
    if name == "AndroidDoctorApp":
        from .main_window import AndroidDoctorApp
        return AndroidDoctorApp
    if name == "DiagnosticReportExporter":
        from .report_exporter import DiagnosticReportExporter
        return DiagnosticReportExporter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
