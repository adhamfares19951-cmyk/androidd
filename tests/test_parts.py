"""
Parts reference and parts indicators (core/part_names.py, core/parts_db.py, core/parts_check.py,
tools/build_parts_db.py, and the battery fallback in core/model_db.py).

The parts check never gives PASS or FAIL: it compares what the phone reports with what its model's
factory firmware is built for and lists indicators. A codename shared by two makers' phones must never
resolve to the wrong one.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from core import part_names as pn
from core.model_db import ModelDatabase, default_db
from core.parts_check import MTK_CAMERA_INFO, PartsCheck
from core.parts_db import PartsDatabase, default_parts_db
from tests.fakes import FakeBridge, PERMISSION
from ui.i18n import render

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import build_parts_db as builder  # noqa: E402


class NameRulesTests(unittest.TestCase):
    def test_mediatek_tuning_files(self):
        text = ("vendor/bin/libccu_ov13b10_ofilm_mipi_raw.dm\nvendor/lib64/gc5035_qtech_mipi_raw_india_tuning.so\n"
                "vendor/lib64/libcam.halsensor.so\n")
        self.assertEqual(pn.sensors_in(text), [("gc5035", "qtech"), ("ov13b10", "ofilm")])

    def test_qualcomm_and_samsung_names(self):
        text = ("vendor/lib64/camera/com.qti.sensormodule.semco_imx586.bin\n"
                "vendor/lib64/camera/com.qti.sensor.s5k3m5.so\n"
                "vendor/lib/camera/com.samsung.sensormodule.0_1_lsi_s5kgw1p_otp.bin\n"
                "vendor/lib/camera/com.samsung.tuned.hynix_hi1336.bin\n"
                "vendor/firmware/setfile_2l4.bin\n"
                "vendor/etc/camera/lavender_imx486_ofilm_i_chromatix.xml\n")
        self.assertEqual(pn.sensors_in(text), [("hi1336", "sk hynix"), ("imx486", "ofilm"), ("imx586", "semco"),
                                               ("s5k2l4", "samsung"), ("s5k3m5", ""), ("s5kgw1p", "samsung")])

    def test_chip_and_library_names_are_not_sensors(self):
        text = ("vendor/lib64/camera/com.qti.sensormodule.qti_tpg0.bin\nvendor/lib64/libSonyIMX230PdafLibrary.so\n"
                "vendor/etc/camera/sc7180_camera_config.xml\nvendor/lib/camera/hi3660_sensor.so\n"
                "vendor/lib64/libimx258.so\n")       # not a camera line
        self.assertEqual(pn.sensors_in(text), [])

    def test_glued_names_are_split(self):
        self.assertEqual(pn.sensors_in("vendor/lib64/camera/ov13b10ofilmmipiraw_tuning.so"), [("ov13b10", "ofilm")])

    def test_phone_readings(self):
        self.assertEqual(pn.sensors_in("CAM[1]:s5kgm1sp_mipi_raw;CAM[2]:ov13b10_mipi_raw;", camera_lines_only=False),
                         [("ov13b10", ""), ("s5kgm1sp", "")])
        self.assertEqual(pn.sensors_in("SONY_IMX555 N", camera_lines_only=False), [("imx555", "sony")])

    def test_fingerprint_and_nfc(self):
        text = "# Fingerprint\nvendor/lib64/hw/fingerprint.fpc.default.so\nvendor/lib64/libgoodixhwfingerprint.so\n"
        self.assertEqual(pn.fingerprint_makers(text), ["FPC", "Goodix"])
        self.assertEqual(pn.fingerprint_makers("vendor/lib64/libgoodix_touch.so\n"), [])   # touch, not fingerprint
        self.assertEqual(pn.fingerprint_maker_of("goodix_fod"), "Goodix")
        self.assertIsNone(pn.fingerprint_maker_of("none"))
        self.assertEqual(pn.nfc_chips("vendor/etc/libnfc-nxp-sn100.conf"), ["NXP SN1xx"])

    def test_factory_codename_only_for_the_same_phone(self):
        self.assertEqual(builder.stock_codenames("# Unpinned blobs from merlin-user 12 SP1A", "merlinx"), ["merlin"])
        self.assertEqual(builder.stock_codenames("# Camera - from alioth_global V12.5", "alioth"), ["alioth_global"])
        self.assertEqual(builder.stock_codenames("# from sagit-user 9", "dumpling"), [])   # a sibling's blobs
        self.assertEqual(builder.stock_codenames("# from guamp_retail-user 11", "guam"), [])


class BundledDatabaseTests(unittest.TestCase):
    db = default_parts_db()

    def test_file_is_complete_and_sourced(self):
        doc = json.loads(Path(ROOT, "data", "parts_db.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["schema"], 1)
        self.assertGreater(len(doc["devices"]), 700)
        for d in doc["devices"]:
            self.assertTrue(d["source"]["wiki"].startswith("https://wiki.lineageos.org/devices/"), d["key"])
            if d["battery_mah"] is not None:
                self.assertTrue(500 <= d["battery_mah"] <= 30000, d["key"])
        self.assertIn("MIT", doc["sources"]["lineage_wiki"]["license"])

    def test_redmi_note_9_by_factory_codename_and_maker(self):
        e = self.db.lookup("M2003J15SS", "merlin", "Xiaomi")
        self.assertEqual((e.display_name, e.codename, e.battery_mah), ("Xiaomi Redmi Note 9", "merlinx", 5020))
        self.assertIn("s5kgm1sp", e.sensor_names())          # the 48 MP main camera
        self.assertIn("sunny", e.modules_of("s5kgm1sp"))

    def test_one_codename_two_makers(self):
        self.assertEqual(self.db.lookup("", "merlin", "motorola").display_name, "Motorola moto g3 turbo")
        self.assertIsNone(self.db.lookup("", "merlin", ""))  # ambiguous without the maker: no entry, never a pick
        self.assertIsNone(self.db.lookup("", "unknown-phone", "Xiaomi"))

    def test_model_number_first(self):
        e = self.db.lookup("sm-g973f/ds", "", "")
        self.assertEqual((e.name, e.matched_by), ("Galaxy S10", "model number"))

    def test_region_variants_of_one_tree_resolve(self):
        e = self.db.lookup("", "guamp", "motorola")
        self.assertEqual(e.codename, "guamp")
        self.assertTrue(e.variant_names)                     # the other name is shown, not hidden


class BatteryFallbackTests(unittest.TestCase):
    def test_curated_entry_wins(self):
        self.assertEqual(default_db().lookup("SM-A525F").design_capacity(), (4370, "rated"))

    def test_wiki_typical_capacity_with_its_source(self):
        info = default_db().lookup("M2003J15SS", "merlin", "Xiaomi")
        self.assertEqual(info.design_capacity(), (5020, "typical"))
        self.assertEqual(info.source, "https://wiki.lineageos.org/devices/merlinx/")
        self.assertIn("LineageOS", info.origin)

    def test_no_fallback_outside_the_default_database(self):
        self.assertIsNone(ModelDatabase(paths=[]).lookup("M2003J15SS", "merlin", "Xiaomi"))
        self.assertIsNone(default_db().lookup("XYZ-1", "nothing", "Nobody"))


def small_db(**over):
    dev = {"key": "merlinx", "codename": "merlinx", "name": "Redmi Note 9", "vendor": "Xiaomi", "models": [],
           "stock_codenames": ["merlin"], "soc": "MediaTek Helio G85", "battery_mah": 5020,
           "camera_sensors": [{"sensor": "s5kgm1sp", "module": "sunny"}, {"sensor": "ov13b10", "module": "sunny"}],
           "platform_camera_sensors": [{"sensor": "gc02m1", "module": None}],
           "fingerprint_makers": ["FPC", "Goodix"], "fingerprint_from": "device",
           "source": {"wiki": "https://wiki.lineageos.org/devices/merlinx/",
                      "tree": "https://github.com/LineageOS/android_device_xiaomi_merlinx/tree/lineage-24.0"}}
    dev.update(over)
    d = tempfile.mkdtemp()
    p = os.path.join(d, "parts.json")
    Path(p).write_text(json.dumps({"schema": 1, "devices": [dev]}), encoding="utf-8")
    return PartsDatabase(p)


PHONE = {
    "getprop ro.product.model": (0, "M2003J15SS", ""),
    "getprop ro.product.device": (0, "merlin", ""),
    "getprop ro.product.manufacturer": (0, "Xiaomi", ""),
}


def check(routes, **db):
    return PartsCheck(FakeBridge(dict(PHONE, **routes)), db=small_db(**db)).run("s")


def effects(r):
    return {i.key: i.effect for i in r.indicators}


class PartsCheckTests(unittest.TestCase):
    def test_original_cameras_and_fingerprint_are_consistent(self):
        r = check({MTK_CAMERA_INFO: (0, "CAM[1]:s5kgm1sp_mipi_raw;CAM[2]:ov13b10_mipi_raw;", ""),
                   "getprop": (0, "[persist.vendor.sys.fp.vendor]: [goodix]\n[ro.boot.hwc]: [GLOBAL]", "")})
        e = effects(r)
        self.assertEqual(e["camera.s5kgm1sp"], "supports")
        self.assertEqual(e["camera.ov13b10"], "supports")
        self.assertEqual(e["fingerprint_maker"], "supports")
        self.assertEqual(r.assessment, "consistent")
        self.assertEqual(r.status, "UNKNOWN")                    # never PASS: software cannot prove it
        self.assertEqual(r.reference, "Xiaomi Redmi Note 9 (merlinx)")
        self.assertTrue(any(ev.source == MTK_CAMERA_INFO for ev in r.evidence))

    def test_a_sensor_the_firmware_is_not_built_for_is_against(self):
        r = check({MTK_CAMERA_INFO: (0, "CAM[1]:imx582_mipi_raw;CAM[2]:ov13b10_mipi_raw;", "")})
        self.assertEqual(effects(r)["camera.imx582"], "against")
        self.assertEqual(r.assessment, "against")
        text = render(next(i.text for i in r.indicators if i.key == "camera.imx582"), "en")
        self.assertIn("replaced camera module", text)
        self.assertIn("production variant", text)               # the other possible cause is named too

    def test_platform_only_sensor_is_not_judged(self):
        r = check({MTK_CAMERA_INFO: (0, "CAM[3]:gc02m1_mipi_mono;", "")})
        self.assertEqual(effects(r)["camera.gc02m1"], "info")

    def test_other_fingerprint_maker(self):
        r = check({"getprop": (0, "[persist.vendor.sys.fp.vendor]: [silead]", "")})
        self.assertEqual(effects(r)["fingerprint_maker"], "against")
        r = check({"getprop": (0, "[persist.vendor.sys.fp.vendor]: [silead]", "")}, fingerprint_from="platform")
        self.assertEqual(effects(r)["fingerprint_maker"], "info")   # a shared tree's list is weaker evidence

    def test_nothing_readable_says_so(self):
        r = check({MTK_CAMERA_INFO: PERMISSION})
        self.assertEqual(effects(r)["camera_sensors"], "info")
        self.assertEqual(r.assessment, "insufficient")
        self.assertTrue(any(ev.source == MTK_CAMERA_INFO for ev in r.evidence))   # the refused read is evidence

    def test_samsung_camera_nodes(self):
        r = check({"rear/rear_camtype": (0, "SONY_IMX555", ""), "getprop ro.product.model": (0, "SM-X1", ""),
                   "getprop ro.product.manufacturer": (0, "samsung", "")},
                  vendor="Samsung", codename="x", stock_codenames=[], models=["SM-X1"],
                  camera_sensors=[{"sensor": "imx555", "module": "sony"}])
        self.assertEqual(effects(r)["camera.imx555"], "supports")

    def test_unknown_phone_lists_what_it_reads(self):
        r = PartsCheck(FakeBridge(dict(PHONE, **{MTK_CAMERA_INFO: (0, "CAM[1]:s5kgm1sp_mipi_raw;", ""),
                                                 "getprop ro.product.device": (0, "other", "")})),
                       db=small_db()).run("s")
        e = effects(r)
        self.assertEqual(e["reference"], "info")
        self.assertEqual(e["camera_sensors"], "info")
        self.assertIsNone(r.reference)

    def test_camera_log_lines_are_not_used(self):
        # drivers log every sensor they probe: a sensor only in the log must not appear as found
        r = check({"logcat": (0, "imgsensor: probing imx582_mipi_raw failed", "")})
        self.assertEqual([f.sensor for f in r.found_sensors], [])


class PackagingTests(unittest.TestCase):
    def test_every_data_file_is_in_the_windows_package(self):
        spec = Path(ROOT, "packaging", "android_doctor.spec").read_text(encoding="utf-8")
        for f in sorted(os.listdir(os.path.join(ROOT, "data"))):
            if f.endswith(".json"):
                self.assertIn(f'"{f}"', spec, f"data/{f} missing from packaging/android_doctor.spec")


class ReportTests(unittest.TestCase):
    def test_report_section(self):
        from ui.report_exporter import DiagnosticReportExporter
        from tests.test_report_sections import BOARD_PASS
        r = check({MTK_CAMERA_INFO: (0, "CAM[1]:imx582_mipi_raw;", "")})
        with tempfile.TemporaryDirectory() as d:
            path = DiagnosticReportExporter.generate_html_report(device_profile={"serial": "S"}, board_diag=BOARD_PASS,
                                                                 output_dir=d, lang="ar", parts_check=r.to_dict())
            html = Path(path).read_text(encoding="utf-8")
        self.assertIn("مؤشرات أصلية القطع", html)
        self.assertIn("IMX582", html)
        self.assertIn("LineageOS", html)                         # the reference is named


if __name__ == "__main__":
    unittest.main()
