"""Dead-phone USB scan. A switched-off Redmi Note 9 (MT6768) plugged in shows on Windows as
USB\\VID_0E8D&PID_20FF\\0595E3F00405 (HID, status OK); the scan used to say "nothing detected"."""
import json
import unittest
from unittest.mock import MagicMock, patch

from core.adb_bridge import ADBBridge
from core.board_diag import MotherboardDiagnostics
from ui.i18n import render

REDMI_OFF = {"FriendlyName": "USB Input Device", "InstanceId": "USB\\VID_0E8D&PID_20FF\\0595E3F00405",
             "Class": "HIDClass", "Status": "OK"}
KEYBOARD = {"FriendlyName": "Delux T21 Gaming Keyboard", "InstanceId": "USB\\VID_04D9&PID_1702&MI_00\\6&4B41495&1&0000",
            "Class": "HIDClass", "Status": "OK"}


def scan(items):
    proc = MagicMock(returncode=0, stdout=json.dumps(items), stderr="")
    with patch("core.adb_bridge.sys.platform", "win32"), patch("core.adb_bridge.subprocess.run", return_value=proc):
        return ADBBridge().scan_bootrom_usb_ports()


class TestPhonePresentOnUsb(unittest.TestCase):
    def test_switched_off_mediatek_phone_is_reported(self):
        ports = scan([KEYBOARD, REDMI_OFF])
        self.assertEqual(len(ports), 1)                      # the keyboard is not a phone
        p = ports[0]
        self.assertEqual((p["vendor"], p["vid"], p["pid"], p["serial"]), ("MediaTek", "0E8D", "20FF", "0595E3F00405"))
        self.assertNotIn("MediaTek", p["mode"])              # not mistaken for BROM by the mode check
        text = render(p["diagnosis"], "en")
        self.assertIn("0595E3F00405", text)
        self.assertIn("20FF", text)

    def test_composite_interfaces_count_once(self):
        items = [{"FriendlyName": "USB Composite Device", "InstanceId": "USB\\VID_2717&PID_FF48\\0595E3F00405", "Class": "USB", "Status": "OK"},
                 {"FriendlyName": "Redmi Note 9", "InstanceId": "USB\\VID_2717&PID_FF48&MI_00\\6&1DC9924&0&0000", "Class": "USBDevice", "Status": "OK"},
                 {"FriendlyName": "ADB Interface", "InstanceId": "USB\\VID_2717&PID_FF48&MI_01\\6&1DC9924&0&0001", "Class": "USBDevice", "Status": "OK"}]
        ports = scan(items)
        self.assertEqual([(p["vendor"], p["pid"]) for p in ports], [("Xiaomi", "FF48")])
        self.assertEqual(ports[0]["serial"], "0595E3F00405")

    def test_known_brom_pid_is_still_brom(self):
        ports = scan([{"FriendlyName": "MediaTek USB Port", "InstanceId": "USB\\VID_0E8D&PID_0003\\5&1", "Class": "Ports", "Status": "OK"}])
        self.assertEqual(len(ports), 1)
        self.assertIn("MediaTek BROM", ports[0]["mode"])


class TestDiagnosisWithPhonePresent(unittest.TestCase):
    def diagnose(self, ports):
        bridge = MagicMock()
        bridge.scan_bootrom_usb_ports.return_value = ports
        return MotherboardDiagnostics(bridge, MagicMock()).diagnose_dead_phone_bootrom(check_cycling=False)

    def test_present_phone_is_not_nothing_and_not_brom(self):
        d = self.diagnose(scan([REDMI_OFF]))
        self.assertFalse(d.is_mediatek_brom)
        self.assertEqual(d.summary_diagnosis.key, "bootrom.phone_present")
        self.assertEqual(d.possible_causes, [])               # a powered phone on USB is a fact, not a fault
        self.assertIn("0595E3F00405", render(d.summary_diagnosis, "ar"))

    def test_nothing_on_usb_is_still_none(self):
        self.assertEqual(self.diagnose(scan([KEYBOARD])).summary_diagnosis.key, "bootrom.none")


if __name__ == "__main__":
    unittest.main()
