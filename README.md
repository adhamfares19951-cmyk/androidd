# Android Doctor Pro 🚀
**Advanced Android Hardware Diagnostics, Motherboard Analyzer, Interactive Component Tester, Malware Hunter & Deep System Repair Suite**

---

## 🌟 Executive Overview
**Android Doctor Pro** is a 100% offline-first, professional desktop diagnostic and repair workstation engineered for hardware repair technicians, forensic auditors, and Android power users. It directly bridges with Android device kernels via ADB, Fastboot, and low-latency Scrcpy screen mirroring to deliver true low-level component telemetry without reliance on third-party cloud services or dummy animations.

---

## ⚡ Key Architectural Features & Diagnostic Engines

### 1. Multi-Vendor Motherboard & Charging IC (PMIC) Analyzer (`core/board_diag.py`)
- **Multi-Vendor Sysfs Fallbacks:** Automatically traverses vendor-specific battery and power management kernel nodes in priority order:
  - `/sys/class/power_supply/battery/`
  - `/sys/class/power_supply/bms/`
  - `/sys/class/power_supply/sec-battery/` (Samsung Exynos / Snapdragon)
  - `/sys/class/power_supply/qcom-battery/` (Qualcomm PMIC)
  - `/sys/class/power_supply/mtk-battery/` (MediaTek Helio / Dimensity)
  - `/sys/class/power_supply/charger/`
  - `/sys/class/power_supply/usb/`
  - Graceful fallback to `dumpsys battery`.
- **Unit Normalization Engine:** Auto-detects and normalizes microvolts ($\mu\text{V}$) to millivolts ($\text{mV}$) and microamperes ($\mu\text{A}$) to milliamperes ($\text{mA}$).
- **Hardware Fault Detection Rules:**
  - `Voltage < 3500mV while charging` &rarr; Flags **"Critical Voltage Drop: Potential Charging IC (PMIC) failure or degraded battery cell"**.
  - `Charging Current < 200mA on AC` &rarr; Flags **"High Resistance / Low Current: Dirty charging port, bad cable, or damaged USB board line"**.
  - `True Battery Health %` calculated via `(charge_full / charge_full_design) * 100`.
- **Motherboard Standby Current Leakage Test:**
  - Isolates board standby drain by powering screen off (`input keyevent 223`).
  - Samples current draw 10 times over 30 seconds.
  - If average leakage exceeds **250mA** (or peak > 350mA) with screen off &rarr; Flags **"Board Current Leakage / Partial Short Detected"**.
- **Thermal Mapping Matrix:** Reads all `/sys/class/thermal/thermal_zone*/type` and `temp` sensors. Converts millidegrees to Celsius and alerts when CPU, GPU, PMIC, Modem/PA, or Battery exceeds **45°C**.
- **NAND Flash & Kernel Panic Analyzer:** Scans kernel ring buffer (`dmesg`), `/proc/kmsg`, and `dumpsys dropbox` for:
  - `I/O error`, `mmc0`, `ufs`, `blk_update_request` (NAND controller wear / corruption)
  - `watchdog`, `softlockup`, `hung_task` (hardware lockups)
  - `thermal_shutdown` (emergency heat trip-offs)
  - `Kernel panic`, `fatal exception`, `Oops` (system crash triggers).

---

### 2. Interactive Hardware & Sensor Tester (`core/hardware_tester.py`)
- **Sensor Service Auditor:** Parses `dumpsys sensorservice` to verify active I2C bus sensors (Accelerometer, Gyroscope, Magnetometer, Proximity, Light, Barometer). Identifies dead or un-enumerated I2C sensors.
- **Camera Subsystem Inspector:** Enumerates front and back camera modules, IDs, flash unit availability, and active client connections via `dumpsys media.camera`.
- **Audio & Haptic Feedback:** Triggers 1000ms vibration pulses through multi-tier fallback APIs (`cmd vibrator_manager`, `cmd vibrator`, or sysfs `/sys/class/timed_output/vibrator/enable`) and performs acoustic sound tests.
- **Touch Digitizer Inspection:** Toggles `pointer_location` (`settings put system pointer_location 1/0`) to expose dead touch zones and ghost touches directly on screen.

---

### 3. Malware Hunter, Spyware Auditor & Debloater (`core/malware_hunter.py`)
- **Ghost Apps Detection:** Flags stealth applications that have background services actively executing but hide their icon from the launcher app drawer.
- **RAT & Spyware Permission Risk Scorer:** Detects dangerous permission combinations on non-system apps:
  - `BIND_ACCESSIBILITY_SERVICE` (input hijacking / keyloggers)
  - `BIND_DEVICE_ADMIN` (anti-uninstall locks)
  - `SYSTEM_ALERT_WINDOW` (phishing overlays)
  - `READ_SMS` / `RECEIVE_SMS` / `SEND_SMS` (2FA interception)
  - `RECORD_AUDIO` / `CAMERA` (covert surveillance)
- **Active Network Socket Sniffer:** Inspects `netstat` and `/proc/net/tcp` to correlate remote IPs and non-standard connection ports with app package UIDs.
- **Safe Rollback Center:** Restrict background access, disable, or uninstall malicious packages. Every removed package is recorded in the SQLite `rollback_history` table and can be reinstalled anytime with one click via `cmd package install-existing <pkg>`.

---

### 4. Real System Optimizer (`core/optimizer.py`)
Executes 5 genuine system tuning protocols with live metrics:
1. **Cache & Junk Purge:** Runs `pm trim-caches 999G`, cleans `/sdcard/DCIM/.thumbnails`, and wipes orphaned debug logs.
2. **NAND Flash TRIM:** Executes `sm fstrim` (fallback: `vdc fstrim dotrim`) for SSD wear-leveling and write speed restoration.
3. **Bytecode Compilation:** Runs `cmd package bg-dexopt-job` & `cmd package compile -m speed-profile -a` to pre-compile ART bytecode for smooth execution.
4. **Deep Doze Activation:** Enforces aggressive Doze standby via `dumpsys deviceidle force-idle`.
5. **UI Responsiveness Tuning:** Sets window, transition, and animator scales to `0.5x`.
- Compares and logs Before vs. After RAM, Storage, and Standby current drain to SQLite.

---

### 5. Broken Screen & Bootloop Rescue (`core/rescue_center.py`)
- **Embedded Scrcpy Mirroring:** Low-latency screen mirroring for devices with broken touch screens or displays, with `--turn-screen-off` support to save battery and reduce technician bench heat.
- **Fastboot Recovery Tools:**
  - Reads bootloader variables: `fastboot getvar all` (secure boot, slot, battery mV, product).
  - Fixes bootloops by clearing and formatting corrupted cache (`fastboot erase cache`).
  - Fastboot partition flashing (`boot`, `recovery`, `vbmeta`, etc.).
- **Emergency Data Extraction:** One-click pull of DCIM camera photos, downloads, documents, or WhatsApp databases to the PC Desktop.

---

### 6. Official Diagnostic Certificate Exporter (`ui/report_exporter.py`)
- Generates a dark-themed **Diagnostic Certificate** (HTML & printable to PDF).
- Displays Device Specifications, Battery True Health %, PMIC Status, Motherboard Leakage Verdict, Thermal Map, Security Audit, and Before/After Optimization metrics.
- Complete with Lead Technician sign-off and SHA256 integrity stamp.

---

## 📂 Project Directory Structure

```text
android/
├── bin/                       # Local ADB, Fastboot, and scrcpy binaries (auto-downloaded)
│   ├── adb.exe
│   ├── fastboot.exe
│   └── scrcpy.exe
├── core/                      # 5 Heavy Diagnostic & Repair Engines
│   ├── __init__.py
│   ├── adb_bridge.py          # Subprocess wrapper with CREATE_NO_WINDOW & sysfs fallbacks
│   ├── profiler.py            # Deep device hardware & OS profiler
│   ├── board_diag.py          # PMIC, Motherboard Short/Leakage, Thermals & Panic Analyzer
│   ├── hardware_tester.py     # Sensors, I2C bus, Vibrator, Camera & Touch Digitizer
│   ├── malware_hunter.py      # Ghost apps, RAT permissions, Network sniffer & Rollback
│   ├── optimizer.py           # Real 5-step optimizer with before/after metrics
│   └── rescue_center.py       # Scrcpy mirror, Fastboot repair & Data extraction
├── database/                  # SQLite Persistence Layer
│   ├── __init__.py
│   └── db_manager.py          # SQLite schema (devices, reports, rollbacks, optimization, leakage)
├── data/                      # SQLite database storage (android_doctor.db)
├── ui/                        # User Interface Layer
│   ├── __init__.py
│   ├── main_window.py         # Modern Dark CustomTkinter GUI
│   └── report_exporter.py     # Printable HTML/PDF Certificate Generator
├── reports/                   # Output folder for generated diagnostic certificates
├── main.py                    # Main Application Entrypoint (GUI & CLI)
├── setup_bins.py              # Automated binary verification & downloader
└── requirements.txt           # Python package dependencies
```

---

## 🚀 Installation & Running

### Requirements
- Windows 10/11 (or Linux/macOS with adb installed)
- Python 3.10+
- Enabled **USB Debugging** on target Android devices.

### Quick Start
```bash
# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Verify / Auto-download Google platform tools & scrcpy
python setup_bins.py

# 3. Launch the Modern Dark Desktop GUI
python main.py

# (Optional) Run diagnostics directly via CLI
python main.py --cli
```

---

## 🔒 Security & Subprocess Safety
- **No Console Flashing:** On Windows, all subprocess calls utilize `CREATE_NO_WINDOW` (`0x08000000`). No command prompt windows flash during background diagnostics.
- **Thread Safety:** Every diagnostic scan and repair script executes in background worker threads, preventing GUI unresponsiveness.
