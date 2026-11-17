"""data/fault_signatures.json: every rule is well formed, its patterns match what they mean (and not the common
harmless lines next to it), and its texts give possible causes, never a definite diagnosis."""
import json
import os
import re
import unittest

PATH = os.path.join(os.path.dirname(__file__), "..", "data", "fault_signatures.json")
FIELDS = ("id", "category", "severity", "pattern", "description_en", "description_ar",
          "suspect_components", "action_en", "action_ar")
# One log line cannot show that a part "is failing" or "is dying"; these phrases claimed it.
DEFINITE_CLAIMS = ("is failing", "chip failing", "chip is degrading", "permanent read-only",
                   "تحتضر", "قاربت على التلف", "مؤشر على تلف", "الفحص يشير", "توقف المعالج لحماية العتاد",
                   "تعليق كامل", "إعادة تشغيل متكررة", ">85", "find shorted")


def load():
    with open(PATH, encoding="utf-8") as f:
        return json.load(f)["rules"]


def matching(line):
    return [r["id"] for r in load() if re.search(r["pattern"], line, re.I)]


class TestRulesAreWellFormed(unittest.TestCase):
    def test_fields_ids_and_patterns(self):
        rules = load()
        self.assertEqual(len({r["id"] for r in rules}), len(rules))
        for r in rules:
            for f in FIELDS:
                self.assertTrue(r.get(f), (r.get("id"), f))
            self.assertIn(r["severity"], ("CRITICAL", "HIGH", "MEDIUM", "LOW"), r["id"])
            re.compile(r["pattern"])


BOARD_AREAS = ("storage", "power", "peripherals", "modem", "soc_ram", "thermal", "firmware", "display")


class TestBoardAreas(unittest.TestCase):
    def test_every_rule_names_its_board_areas(self):
        for r in load():
            areas = r.get("board_areas")
            self.assertTrue(areas, r["id"])
            self.assertEqual(len(areas), len(set(areas)), r["id"])
            for a in areas:
                self.assertIn(a, BOARD_AREAS, f"{r['id']}: {a}")

    def test_software_first_where_it_is_the_common_cause(self):
        rules = {r["id"]: r for r in load()}
        self.assertEqual(rules["KERNEL_PANIC_OOPS"]["board_areas"][0], "firmware")
        self.assertEqual(rules["STORAGE_IO_ERROR"]["board_areas"][0], "storage")
        self.assertEqual(rules["UVLO_BROWNOUT"]["board_areas"][0], "power")


ARABIC = re.compile("[" + chr(0x0600) + "-" + chr(0x06FF) + "]")      # the Arabic letters block


class TestArabicPartNames(unittest.TestCase):
    def test_every_part_has_an_arabic_name(self):
        # the Arabic report must not show English part names
        for r in load():
            ar = r.get("suspect_components_ar")
            self.assertTrue(ar, r["id"])
            self.assertEqual(len(ar), len(r["suspect_components"]), r["id"])
            for name in ar:
                self.assertRegex(name, ARABIC, f"{r['id']}: {name}")


class TestWording(unittest.TestCase):
    def test_actions_give_possible_causes(self):
        for r in load():
            self.assertIn("possible cause", r["action_en"].lower(), r["id"])
            self.assertIn("محتمل", r["action_ar"], r["id"])

    def test_no_definite_diagnosis_from_a_log_line(self):
        for r in load():
            text = " ".join([r["description_en"], r["description_ar"], r["action_en"], r["action_ar"]])
            for claim in DEFINITE_CLAIMS:
                self.assertNotIn(claim, text, f"{r['id']}: {claim}")


class TestPatterns(unittest.TestCase):
    def test_probe_defer_and_longer_codes_are_not_i2c_faults(self):
        # -517 (EPROBE_DEFER) is a normal "try again later" at boot; it used to match "error -5"
        self.assertEqual(matching("[ 1.234] i2c i2c-1: probe of 1-0038 failed with error -517"), [])
        self.assertEqual(matching("[ 2.000] i2c i2c-3: error -1100 while reading"), [])

    def test_real_i2c_errors_still_match(self):
        self.assertIn("I2C_IO_ERROR", matching("[ 1.300] i2c_geni 4a84000.i2c: i2c error -5"))
        self.assertIn("I2C_IO_ERROR", matching("[ 1.301] i2c i2c-2: transfer failed"))
        self.assertIn("I2C_BUS_TIMEOUT", matching("[ 3.900] i2c i2c-5: error -110 waiting for bus"))

    def test_other_rules_still_match_their_lines(self):
        cases = {
            "KERNEL_PANIC_OOPS": "[ 4.5] Kernel panic - not syncing: VFS: Unable to mount root fs",
            "WATCHDOG_BITE": "[ 9.0] Watchdog bite! cpu 3",
            "STORAGE_IO_ERROR": "[ 3.2] mmcblk0: error -110 transferring data, sector 2048",
            "UFS_LINK_STARTUP_FAIL": "[ 3.2] ufshcd-qcom 1d84000.ufshc: ufs_init fail, link startup error -110",
            "THERMAL_SHUTDOWN": "[ 8.0] thermal: critical temperature reached, shutting down",
            "UVLO_BROWNOUT": "[ 5.0] pmic: uvlo_detected, vph_pwr collapse",
            "MODEM_RIL_CRASH": "[ 7.0] ModemSSR: modem crashed",
        }
        for rule_id, line in cases.items():
            self.assertIn(rule_id, matching(line), line)


class TestMoreKernelSignatures(unittest.TestCase):
    def test_at_least_twenty_rules(self):
        self.assertGreaterEqual(len(load()), 20)

    def test_new_rules_match_their_own_lines(self):
        cases = {
            "KERNEL_NULL_DEREF": "[ 6.1] Unable to handle kernel NULL pointer dereference at virtual address 0000000000000008",
            "ARM_SERROR": "[ 6.2] SError Interrupt on CPU3, code 0xbe000011 -- SError",
            "SOFT_LOCKUP": "[ 30.0] watchdog: BUG: soft lockup - CPU#2 stuck for 22s! [kworker/2:1:77]",
            "RCU_STALL": "[ 40.0] rcu: INFO: rcu_preempt detected stalls on CPUs/tasks:",
            "HUNG_TASK": "[ 240.0] INFO: task kworker/0:1:123 blocked for more than 120 seconds.",
            "OOM_KILL": "[ 90.0] Out of memory: Killed process 4321 (com.example) total-vm:123kB",
            "EXT4_FS_ERROR": "[ 5.5] EXT4-fs error (device mmcblk0p40): ext4_lookup:1601: inode #2: comm init: deleted inode referenced",
            "F2FS_FS_ERROR": "[ 5.6] F2FS-fs (dm-8): Found inconsistent data, Need to run fsck",
            "DM_VERITY_CORRUPTION": "[ 5.7] device-mapper: verity: 253:2: data block 1234 is corrupted",
            "EMMC_INIT_FAIL": "[ 1.9] mmc0: error -110 whilst initialising MMC card",
            "CHARGER_OVERVOLTAGE": "[ 12.0] smb5: usbin-ov irq triggered, vbus over voltage",
            "SUBSYSTEM_CRASH": "[ 15.0] subsys-restart: subsystem_restart_dev(): Restart sequence requested for adsp, restart_level = SYSTEM.",
            "GPU_FAULT": "[ 50.1] kgsl kgsl-3d0: |adreno_hang_int_callback| GPU hang detected",
            "MODEM_RIL_CRASH": "[ 15.1] subsys-restart: subsystem_restart_dev(): Restart sequence requested for modem, restart_level = RELATED.",
        }
        for rule_id, line in cases.items():
            self.assertIn(rule_id, matching(line), line)

    def test_common_harmless_lines_match_nothing(self):
        harmless = [
            "[ 3.0] EXT4-fs (mmcblk0p40): mounted filesystem with ordered data mode. Opts: barrier=1",
            "[ 3.1] F2FS-fs (dm-8): Mounted with checkpoint version = 1b3d",
            '[ 3.2] device-mapper: verity: sha256 using implementation "sha256-ce"',
            "[ 60.0] lowmemorykiller: Kill 'com.example' (1234), adj 900",
            "01-01 10:00:00.000  600  600 I lmkd: Kill 'com.example' (1234), uid 10100, oom_score_adj 900",
            "[ 2.0] mmc1: error -110 whilst initialising SD card",          # a bad or missing SD card, not the eMMC
            "[ 8.0] healthd: battery l=80 v=4100 t=30.0 h=2 st=2 chg=u",
            "[ 9.0] smb5: OVP threshold set to 14V",
            "[ 4.0] kgsl kgsl-3d0: Initialized kgsl-3d0: mmu=on",
            "[ 7.0] rcu: Hierarchical SRCU implementation.",
        ]
        for line in harmless:
            self.assertEqual(matching(line), [], line)


class TestEveryChipMaker(unittest.TestCase):
    """Lines in the wording of each chip maker's own drivers (MediaTek, Samsung Exynos, Unisoc, HiSilicon Kirin,
    Qualcomm) and of Android itself, so a log from any phone is recognised, not only from Qualcomm phones."""

    def test_chip_maker_lines_match_their_rule(self):
        cases = {
            # MediaTek
            "MTK_MSDC_ERROR": "[ 2.1] [msdc]msdc0 -> CMD<17> Arg<0x00001000> resp_CRC <-84>",
            "MTK_MODEM_EXCEPTION": "[ 20.0] [ccci1/mcd]md1: MD exception happened, type 0x3",
            "I2C_BUS_TIMEOUT": "[ 3.0] i2c i2c-3: addr: 0x38, transfer timeout",               # i2c-mt65xx
            "I2C_IO_ERROR": "[ 3.1] i2c i2c-6: addr: 0x5d, transfer ACK error",                  # i2c-mt65xx
            # Samsung Exynos / Unisoc
            "BASEBAND_CP_CRASH": "[ 30.0] cpif: modem_ctrl: CP Crash! (reason: watchdog)",
            # HiSilicon Kirin
            "KIRIN_RDR_SYSERR": "[ 40.0] rdr_syserr_process_for_ap: modid 0x81fffdfe, arg1 0x0",
            # Qualcomm power-on / power-off reasons (qpnp-power-on)
            "SMPL_POWER_LOSS": "[ 0.5] qcom,qpnp-power-on: PMIC@SID0 Power-on reason: Triggered from SMPL (Sudden Momentary Power Loss)",
            "UVLO_BROWNOUT": "[ 0.5] qcom,qpnp-power-on: PMIC@SID0: Power-off reason: Triggered from UVLO (Under Voltage Lock Out)",
            "THERMAL_SHUTDOWN": "[ 0.5] qcom,qpnp-power-on: PMIC@SID0: Power-off reason: Triggered from OTST3 (Overtemp)",
            # any chip: kernel and Android
            "ARM_SERROR": "[ 6.0] Internal error: synchronous external abort: 96000210 [#1] PREEMPT SMP",
            "STORAGE_IO_ERROR": "[ 3.3] mmcblk0: error -84 transferring data, sector 4096, nr 8, cmd response 0x900",
            "SDHCI_HW_TIMEOUT": "[ 3.4] mmc0: Timeout waiting for hardware interrupt.",
            "BAD_PAGE_STATE": "[ 9.0] BUG: Bad page state in process kswapd0  pfn:8f3a1",
            "CRITICAL_SERVICE_CRASH": "[ 25.0] init: Critical process 'zygote' exited 4 times in 4 minutes; rebooting into bootloader",
            "FIRST_STAGE_MOUNT_FAIL": "[ 1.2] init: Failed to mount required partitions early ...",
            "BATTERY_NOT_DETECTED": "[ 2.5] qcom,fg: battery not detected, using default profile",
            "PMIC_OVERCURRENT": "[ 4.4] regulator: ldo15: over current detected, disabling",
            "DISPLAY_DSI_ERROR": "[ 5.0] dsi_panel: [dsi_panel_tx_cmd_set] cmd tx failed, rc=-110",
        }
        for rule_id, line in cases.items():
            self.assertIn(rule_id, matching(line), line)

    def test_normal_lines_of_those_drivers_match_nothing(self):
        harmless = [
            "[ 0.5] qcom,qpnp-power-on: PMIC@SID0 Power-on reason: Triggered from PON1 (Secondary PMIC) and 'warm' boot",
            "[ 0.5] qcom,qpnp-power-on: PMIC@SID0: Power-off reason: Triggered from PS_HOLD (PS_HOLD/MSM controlled shutdown)",
            "[ 0.6] qcom,qpnp-power-on: smpl enabled",
            "[ 2.0] [msdc]msdc0: set mclk to 200000000Hz",
            "[ 2.0] [msdc]msdc_set_timeout: data timeout ns 100000000",
            "[ 9.0] [ccci1/fsm]md1 boot ready",
            "[ 9.1] cpif: cp_crash_cnt=0",
            "01-01 10:00:00.000 1 1 I init: Service 'vendor.qti.hardware.perf' (pid 712) exited with status 0",
            "[ 2.6] healthd: battery present=1 health=Good",
            "[ 4.5] regulator: ldo15: over current protection enabled",
            "[ 5.1] dsi_panel: dsi_panel_init: Panel initialized",
            "[ 6.0] audit: type=1400 avc: denied { read } for comm=\"init\" scontext=u:r:init:s0",
        ]
        for line in harmless:
            self.assertEqual(matching(line), [], line)

    def test_more_than_thirty_rules(self):
        self.assertGreater(len(load()), 30)


if __name__ == "__main__":
    unittest.main()
