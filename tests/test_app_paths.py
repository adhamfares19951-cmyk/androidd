"""Writable paths: unchanged when run from source, outside the install folder in a packaged build."""
import os
import sys
import unittest
from unittest.mock import patch

import app_paths

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class SourceModeTests(unittest.TestCase):
    def test_source_paths_are_the_project_folders(self):
        with patch.object(app_paths, "FROZEN", False):
            self.assertEqual(app_paths.default_db_path(), os.path.join(ROOT, "data", "android_doctor.db"))
            self.assertEqual(app_paths.default_reports_dir(), os.path.join(ROOT, "reports"))
            self.assertEqual(app_paths.install_dir(), ROOT)

    def test_modules_use_app_paths(self):
        from database import db_manager
        from ui import report_exporter
        from core import report_signing
        self.assertEqual(db_manager.DEFAULT_DB_PATH, os.path.join(ROOT, "data", "android_doctor.db"))
        self.assertEqual(report_exporter.REPORTS_DIR, os.path.join(ROOT, "reports"))
        self.assertEqual(report_signing.PROJECT_ROOT, ROOT)


class FrozenModeTests(unittest.TestCase):
    def setUp(self):
        self.install = os.path.join("C:\\", "Program Files", "Android Doctor Pro")
        self.env = {"LOCALAPPDATA": os.path.join("C:\\", "Users", "u", "AppData", "Local"),
                    "USERPROFILE": os.path.join("C:\\", "Users", "u")}

    def test_frozen_writes_outside_install_folder(self):
        with patch.object(app_paths, "FROZEN", True), \
                patch.object(sys, "executable", os.path.join(self.install, "AndroidDoctorPro.exe")), \
                patch.dict(os.environ, self.env):
            self.assertEqual(app_paths.install_dir(), self.install)
            self.assertEqual(app_paths.default_db_path(),
                             os.path.join(self.env["LOCALAPPDATA"], "AndroidDoctorPro", "android_doctor.db"))
            self.assertEqual(app_paths.default_reports_dir(),
                             os.path.join(self.env["USERPROFILE"], "Documents", "Android Doctor Pro", "Reports"))
            for p in (app_paths.default_db_path(), app_paths.default_reports_dir()):
                self.assertFalse(os.path.normcase(p).startswith(os.path.normcase(self.install)), p)

    def test_signing_key_inside_install_folder_is_refused_in_a_build(self):
        from core.report_signing import is_inside_project
        key = os.path.join(self.install, "keys", "adp_signing_key.pem")
        self.assertTrue(is_inside_project(key, self.install))
        self.assertFalse(is_inside_project(os.path.join("D:\\", "keys", "k.pem"), self.install))


if __name__ == "__main__":
    unittest.main()
