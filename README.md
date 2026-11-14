# Android Doctor Pro

Android Doctor Pro is an offline-first Windows desktop application (Python 3.13, CustomTkinter, SQLite) and console diagnostic tool that inspects Android devices over ADB, fastboot, and scrcpy. It uses a private ADB server port (`5038`), enforces a strict four-state result model so missing or blocked reads remain explicit rather than fabricated, externalizes all user-facing strings to locale files, and stores raw command and sysfs evidence alongside every check.

---

## How Results Work

Every check returns one of four statuses defined in `core/result.py` (`Status`):

- `PASS`: a real value was parsed within its defined healthy bounds, or a tested subsystem command succeeded with confirmed output.
- `FAIL`: a parsed reading exceeded fault thresholds, critical kernel or storage error patterns matched, or a fault condition was directly observed.
- `UNKNOWN`: the default status. Returned when a node or path does not exist, command output is empty or malformed, a required precondition is missing (for example, running the screen-off drain test over USB), or a stage errors out.
- `NEEDS_ROOT`: access was denied by permissions or SELinux (`permission denied`, `avc: denied`, or `operation not permitted` detected on `stderr` or command `stdout`). When reading kernel logs (`dmesg`, `pstore`), `log_content=True` prevents audit log lines inside the stream from being misclassified as a denied command.

### Evidence Rule

- Every check attaches one or more `Evidence` records (`source`, `raw_excerpt` trimmed at 2048 characters, and `exit_code`).
- `exit_code` defaults to `None` when no real process exit code is known (such as legacy ADB connections without `shell_v2` or host-side ratio calculations) so an unknown exit state never looks like `0`.
- Raw evidence is shown in the GUI and rendered inside expandable `<details>` blocks in exported HTML reports. The CLI prints statuses and details; the raw evidence is in the HTML report it writes.
- `aggregate_status()` combines check statuses as `FAIL` if any key check failed, `UNKNOWN` if any key check is `UNKNOWN` or `NEEDS_ROOT` (or there are none), and `PASS` only when all key checks pass. Any unrecognised status string is treated as `UNKNOWN` (`coerce_status()`), never `PASS`.

### One Result Type: `CheckResult`

- Every judged check produces a `CheckResult` (`check_id`, `status`, `value`/`unit`, `detail`, `possible_causes`, `evidence`). Per-check classes (battery, thermal, boot reason, kernel log, charger input, storage, stress test, root forensics and all hardware subsystem checks) get `to_check_result()` from the `ProducesCheckResult` mixin; their domain fields stay available to the GUI.
- `BoardDiagnosticResult.checks` and `HardwareAuditReport.checks` hold the key checks (`check_statuses` mirrors them); `SecurityAuditScan.check_results()` gives the user-CA check.
- The CLI status table, the CLI overall status and the HTML report table are built from these lists. CLI stages that crash become `UNKNOWN` checks and are counted in the report's overall status too.
- The GUI status pills use the same rules (for example, the maintenance pill uses `aggregate_status()`; the BootROM port scan reports USB modes and never shows `PASS`/`FAIL`).

### "Possible Causes" Wording

Board-level anomalies (abnormal boot reasons such as `THERMAL`, `UVLO`, or `WATCHDOG_PANIC`, high screen-off battery current, battery voltage sag under load, low charging voltage/current, high NAND wear, and BootROM USB modes) attach structured `possible_causes` entries rather than stating a definitive hardware diagnosis. Software cannot prove electrical motherboard faults on its own.

---

## Features

### Device Profiler (`core/profiler.py`)
Reads device properties (`getprop`), CPU core count (`/proc/cpuinfo`), kernel string (`uname -a`, `/proc/version`), uptime (`/proc/uptime`), RAM (`/proc/meminfo`), `/data` capacity (`df -k /data`), battery overview (`dumpsys battery`), display dimensions and refresh rate (`wm`, `dumpsys display`), SELinux mode (`getenforce`), and root availability (`su -c id`: root only when it really runs as `uid=0`; a `su` binary that is present but denied, e.g. by Magisk, is not root. The `su` exit status is echoed back so it is known even on ADB transports without `shell_v2`).

### Board, Power, Battery & Storage Diagnostics (`core/board_diag.py`, `core/storage_benchmark.py`, `core/jedec.py`)
- **Battery voltage (fuel gauge)** & **Battery current**: Reads kernel `power_supply` sysfs directories (`battery`, `bms`, `sec-battery`, `qcom-battery`, `mtk-battery`) with `dumpsys battery` fallback. Distinguishes transport context in notes: over USB, `"Battery current"` explicitly notes that it includes charging current while USB is connected; over wireless ADB, it notes wireless transport without USB charging. Battery capacity vs. design ratio (`charge_full / charge_full_design`) is computed only when both values are parsed and plausible; otherwise it reports `NEEDS_ROOT` or `UNKNOWN`.
- **Reset battery statistics**: Calls `dumpsys batterystats --reset` to clear Android's OS battery usage history only; it does not alter hardware fuel-gauge registers.
- **Battery current, screen off**: Measures discharge current across 10 samples at 3-second intervals after turning the screen off (`input keyevent 223`). Executes **only** over wireless ADB (`host:port` or `_adb-tls-connect`) when external chargers (`AC`, `USB`, `Wireless`, `Dock`) are verified disconnected both before and after sampling; otherwise it returns `UNKNOWN`. Averages over unreadable samples are never fabricated. Readings above `250.0 mA` average or `350.0 mA` peak yield `FAIL` with `possible_causes`.
- **Charger Input (`diagnose_fast_charge`)**: Reads USB `real_type`/`type`, `charge_type`, measured `usb/voltage_now` and `usb/current_now` (keeping reported `current_max` limits separate from measured current), and derives wattage when both voltage and current are measured.
- **Thermal Zones (`read_thermal`)**: Queries `dumpsys thermalservice` (excluding non-temperature BCL HAL types `6, 7, 8`) with fallback to `/sys/class/thermal/thermal_zone*`. Applies per-category thresholds loaded from `data/thermal_thresholds.json` (`battery`: `50.0°C`, `skin`: `45.0°C`, `usb_port`: `60.0°C`, `cpu`/`gpu`/`npu`: `95.0°C`, `modem`/`pmic`: `85.0°C`). Zones that match no category (`other`) are displayed as not judged.
- **Boot Reason & Kernel Logs (`run_blackbox_forensics`, `inspect_panic_and_emmc`)**: Reads `sys.boot.reason`, `ro.boot.bootreason`, `dumpsys dropbox`, `/sys/fs/pstore/console-ramoops*`, and `dmesg` (retrying with `su` when rooted) to categorize thermal, undervoltage, watchdog, kernel panic, and storage I/O errors.
- **Storage Health & Benchmark (`read_emmc_ufs_lifespan`, `core/storage_benchmark.py`, `core/jedec.py`)**: Reads eMMC `life_time` / `pre_eol_info` and UFS `health_descriptor` nodes through one shared JEDEC parser (`FAIL` at wear register >= `0x09` or pre-EOL >= `0x02`). Optional sequential `dd` write/read test in `/data/local/tmp` flags slow writes (`< 25 MB/s`) and cleans up temp files (`benchmark_storage_speed` / `run_speed_benchmark`). Without root, Linux page cache is not dropped, so sequential read speeds may come from RAM cache.
- **CPU Stress Test (`run_cpu_pmic_stress_test`)**: Spawns 4 time-bounded shell loops (default 30s), sampling `scaling_cur_freq`, `battery/voltage_now`, and `thermal_zone0/temp` every 2s to measure voltage sag and frequency drop, then terminates its tracked PIDs.
- **Dead-Phone BootROM Port Scan (`diagnose_dead_phone_bootrom`)**: On Windows, queries `Get-PnpDevice` via PowerShell (`CREATE_NO_WINDOW`) for Qualcomm EDL (`9008`, `900E`), MediaTek BROM/Preloader, Samsung Odin, Android Fastboot, MTP interfaces, or USB port cycling, returning `possible_causes`.
- **Root Forensics & EFS Backup (`run_root_forensics_pro`, `backup_efs_nvram_partitions`)**: Requires root (`su -c id`). Matches kernel logs against `data/fault_signatures.json`, collects I2C bus error lines, reads JEDEC registers, and dumps critical NVRAM/EFS/modem/persist block nodes via `dd` with SHA-256 checksums saved to `manifest.json`.

### Model Database (`core/model_db.py`, `data/device_models.json`)
Manufacturer specifications per model number (`ro.product.model`, dual-SIM suffixes such as `/DS` ignored). The database never produces a reading: it only supplies the battery's design capacity when the kernel does not expose `charge_full_design` (often blocked without root). The measured `charge_full` always comes from the phone; without it the check stays `UNKNOWN`/`NEEDS_ROOT`.
- The manufacturer's **rated (minimum)** capacity is used when known, otherwise the typical one, so a battery is never judged against more than the manufacturer guarantees. The evidence names the model, the value used and its source URL; the report notes that the design value came from the specification.
- If the kernel's design capacity differs from the specification by more than 10%, the evidence says so (a non-original battery or a different board revision can cause this). The kernel value is still the one used.
- Every entry needs a source URL and a plausible capacity (500–30000 mAh); anything else is rejected and logged, never guessed. The bundled file lists only models checked against an official manufacturer page.
- Technicians add or correct models in `%LOCALAPPDATA%\AndroidDoctorPro\device_models.json` (read after the bundled file, same format):
```json
{"schema": 1, "models": {"SM-A525F": {"name": "Samsung Galaxy A52", "battery": {"typical_mah": 4500, "rated_mah": 4370}, "source": "https://...", "checked": "2026-11-11"}}}
```

### Hardware Subsystem Checks & Interactive Tools (`core/hardware_tester.py`, `core/touch_matrix.py`)
- **Reported sensors & cameras**: Parses `dumpsys sensorservice` and `dumpsys media.camera` for reported sensors and camera modules; absent fields stay `None`. Missing common sensors are listed for reference rather than treated as an automatic fault.
- **Subsystem checks**: Evaluates AudioFlinger/ALSA soundcards (`/proc/asound/cards`, `/proc/asound/pcm`), biometric HAL (`pm has-feature`, `dumpsys fingerprint`, `dumpsys biometric`, `getprop ro.hardware.fingerprint`), physical keys (interactive `getevent -l`, passing each key only on observed `DOWN` + `UP`), SIM & baseband (`getprop`, `dumpsys telephony.registry`), display backlight + DSI logs, display panel controller & verified boot state (`display.panel_info`, without claiming screen originality), Wi-Fi (`cmd wifi status`, `dumpsys wifi`), Bluetooth (`dumpsys bluetooth_manager`), and declared features (`pm has-feature` flags for GPS, NFC, and USB host; the command prints `true` with exit 0 and `false` with exit 1, and both are answers).
- **Interactive bench helpers**: Pushes standalone HTML5 screen color and `8x14` touch-grid test pages (`core/touch_matrix.py`) opened via intent with `pointer_location` enabled, generates a temporary `1000 Hz` PCM speaker WAV tone in the OS temp folder, triggers haptic vibration pulses, and includes optional companion APK broadcast helpers.

### Companion App (`companion/`, `core/companion.py`)
A small Android app that measures on the phone what ADB cannot read without root. The desktop program installs it (`adb install -r -g`), starts its test screen with a unique run id and reads that run's raw readings from logcat (`ADP|{json}`, tag `AndroidDoctorCompanion`). The app never decides PASS/FAIL and stores no images or recordings; `core/companion.py` judges the readings, with the raw line as evidence:
- **Battery capacity:** Android's charge counter / charge level gives the present full capacity (a fuel-gauge estimate, only at 50% charge or more), compared with the manufacturer's capacity from the model database (FAIL below 80%). No spec: the value is shown, not judged.
- **Sensors (live):** a listed sensor that delivers no reading in 2.5 s is FAIL.
- **Vibration:** accelerometer jitter while vibrating vs. at rest (PASS at 3x, FAIL below 1.5x; not judged if the phone moved).
- **Speaker to microphone:** the 1 kHz test tone recorded vs. without it (PASS at 20 dB, FAIL below 6 dB, with possible causes).
- **Cameras:** each camera opens and delivers frames (PASS) or not (FAIL with Android's error); image brightness is information only.

GUI: Hardware & Sensors tab, "Companion APK Test" (results go into the next report). CLI: `--cli --companion`. Keep the phone unlocked, screen on, lying still. Build: `python companion/build.py` (Android SDK `platforms;android-35` + `build-tools;35.0.0`, JDK 17+, no Gradle); the APK signing key is created in `%LOCALAPPDATA%\AndroidDoctorPro\companion-signing\` and never committed. `packaging\build.ps1` builds it first and bundles it.

### Apps with Sensitive Permissions & Debloater (`core/malware_hunter.py`)
- Lists third-party packages holding granted sensitive permissions (`install permissions:` / `runtime permissions:` with `granted=true` in `dumpsys package`; ungranted declarations are ignored), enabled accessibility services, active device admins, user CA storage (`/data/misc/user/0/cacerts-added`), and active `/proc/net/tcp` + `/proc/net/tcp6` sockets mapped to UIDs. Reported strictly as a technician review inventory, with no threat or malware labels.
- Checks installed packages against `OEM_DEBLOAT_PACKAGES` (Xiaomi, Samsung, Oppo/Realme) and supports background restriction, disabling, `--user 0` uninstallation, and restoration (`cmd package install-existing`) tracked in SQLite `rollback_history`.

### System Maintenance (`core/optimizer.py`)
- Executes three non-root commands: `pm trim-caches 999G` (measured before and after via free `/data` space from `df -k /data`), `sm idle-maint run`, and `cmd package bg-dexopt-job`. The latter two jobs are explicitly marked as **not measurable**. Does not delete user/app cache folders manually, force idle modes, alter animation scales, or report attributed RAM or battery-current gains.

### Rescue, Partition Backup, APK Batch & Provisioning (`core/rescue_center.py`, `core/partition_backup.py`, `core/batch_apk_manager.py`, `core/provisioning_manager.py`)
- Launches bundled `scrcpy` screen mirroring (`--turn-screen-off`, `--stay-awake`) and `--otg` HID mode on port `5038`, plus ADB key/text/swipe helpers.
- Fastboot utilities: `getvar all`, active slot switching (`--set-active` / `set_active`), Anti-Rollback index check (`getvar anti`, `rollback-ver`), whitelisted partition flashing, and `fastboot erase cache` guarded by `fastboot getvar partition-type:cache` (refuses if no `cache` partition is reported).
- Streams `.zip` packages via `adb sideload` with percentage progress parsing.
- Media rescue (`emergency_data_pull`, `one_click_customer_backup`): pulls `/sdcard/DCIM`, `/sdcard/Pictures`, `/sdcard/Download`, `/sdcard/Documents`, and **WhatsApp Media folders only** (`/sdcard/Android/media/com.whatsapp/WhatsApp/Media`, `/sdcard/WhatsApp/Media`); encrypted chat history files (`msgstore.db.crypt*`) are not copied. Contacts and SMS are queried via `content query` where permitted.
- Partition table discovery (`by-name`, `/proc/partitions`) and `dd` backup (`core/partition_backup.py`), bulk APK installation (`adb install -r -d -g`) and extraction (`core/batch_apk_manager.py`), and neutral setup-wizard flag reading/writing (`device_provisioned`, `user_setup_complete`) and Settings/URL intent helpers (`core/provisioning_manager.py`).

### Several Devices at Once (`diagnostics_runner.py`)
- The Reports tab card "All connected devices" and `--cli --all-devices` run the full diagnostics on every connected phone at the same time, each with its own report; each phone's line appears as soon as it finishes. Unauthorized/offline phones are listed, not scanned; a crash in one phone's run becomes that phone's internal error and the others continue.
- `ADBBridge` keeps one lock per device: commands to different phones run in parallel, commands to the same phone stay in order, and server-wide calls (device list, start/kill server) share one lock.

### Diagnostic Report & Persistence (`ui/report_exporter.py`, `database/db_manager.py`)
- Generates printable dark-theme HTML **Diagnostic Report** files (saved to `reports/` by default or `--output-dir`), featuring HTML-escaped device values, a per-check status table, overall aggregated status, expandable evidence drawers, and optional vector SVG QR codes (`qrcode`). A PDF copy can be exported from the Reports tab ("Export PDF") or with `--pdf` (`ui/pdf_export.py`): it is printed by the Microsoft Edge or Google Chrome already installed on the PC, in headless mode with a temporary profile (the user's browser profile is not touched), and is accepted only if the output really is a PDF. Without a signing key the footer states that the report is **not digitally signed**; see [Report Signing](#report-signing-ed25519) for Ed25519 signatures.
- Persists device history, diagnostic summaries, repair tickets, leakage logs, maintenance logs, and package rollbacks in SQLite (`data/android_doctor.db`).
- **Where data is written** (`app_paths.py`): run from source, the database is `data/android_doctor.db` and reports go to `reports/`. In a packaged build the install folder may be read-only, so the database is `%LOCALAPPDATA%\AndroidDoctorPro\android_doctor.db` and reports go to `Documents\Android Doctor Pro\Reports\`. Media and partition backups go to the Desktop in both cases.

---

## Bundled Tools

Tool versions and SHA-256 checksums are pinned in `bin/manifest.json` (`manifest_version: 1`):

- `platform-tools`: **`37.0.1`** (`adb.exe`, `fastboot.exe`, and supporting binaries/DLLs; download URLs, archive SHA-256, and per-file SHA-256 hashes listed for `windows-x86_64`, `windows-arm64`, `linux-x86_64`, `darwin-arm64`, `darwin-x86_64`).
- `scrcpy`: **`5.0.1`** (`scrcpy.exe`, `scrcpy-server`, libraries, and `LICENSE.txt`; download URLs and archive SHA-256 hashes listed for all 5 platforms; per-file SHA-256 hashes pre-listed for `windows-x86_64`, while non-Windows installs record verified hashes into `bin/installed.json`).

Key tool policies enforced in `setup_bins.py` and `core/adb_bridge.py`:
- **No startup downloads:** `verify_binaries()` hashes files in `bin/` against `bin/manifest.json` without touching the network or spawning subprocesses.
- **Manual verified repair:** Triggered via the GUI banner `"Repair tools"` button, `python main.py --repair-tools`, or `python setup_bins.py --repair [--archive-dir DIR]`. Downloads the pinned archive for the current OS, verifies archive SHA-256 **before** extraction, rejects unsafe archive paths (`..`, absolute paths, drive letters), verifies extracted file hashes, and only then installs into `bin/`.
- **Private ADB server port:** Configured via `ADB_SERVER_PORT = 5038`. Child tools run with `ANDROID_ADB_SERVER_PORT=5038` and `ADB` pointing to `bin/adb.exe`. Direct socket shell commands connect to `127.0.0.1:5038` using `shell,v2,raw:` when supported. `stop_adb_server()` executes `adb -P 5038 kill-server` and never kills external ADB servers.

---

## Installation & Running

### Requirements
- Windows 10/11 (primary target; PnP USB and BootROM port queries use PowerShell) and Python 3.13 (tested; the code also compiles on 3.12; older versions are not tested).
- USB Debugging enabled on Android devices for ADB checks (Fastboot and `scrcpy --otg` work outside ADB).

Install dependencies:
```bash
pip install -r requirements.txt
```
`requirements.txt` lists `customtkinter`, `darkdetect`, `packaging`, `Pillow`, `arabic-reshaper`, `python-bidi`, `qrcode` (QR codes in the report are optional; the report is written without one if `qrcode` is missing) and `cryptography` (needed only for report signing).

### Building the Windows Package
```bash
pip install -r requirements.txt -r packaging/requirements-build.txt
```
```bash
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```
`packaging/build.ps1` runs the tests first, builds `dist\AndroidDoctorPro\` with PyInstaller (`packaging/android_doctor.spec`), runs `adp-cli.exe --check-bins` inside the build (it stops if the bundled tools do not match `bin/manifest.json`), and zips the folder to `dist\AndroidDoctorPro-<date>-windows-x64.zip`. The folder holds two programs sharing one runtime:
- `AndroidDoctorPro.exe`: the GUI (no console window), e.g. `AndroidDoctorPro.exe --lang en`
- `adp-cli.exe`: the console commands (`--cli`, `--check-bins`, `--gen-signing-key`, `--verify-report`, ...) with the same exit codes

`bin/` is copied byte-for-byte (no UPX). In the build the database and reports are written outside the program folder (see `app_paths.py`). `--repair-tools` writes into the program folder, so for an install under `Program Files` reinstall the package instead. The executables are not code-signed, so Windows SmartScreen may warn on first start.

### Launching the GUI
On Windows, double-click `run.bat` (launches `python main.py --lang ar`) or run directly:
```bash
python main.py
python main.py --lang en
```
GUI tabs are created on demand and during idle intervals for fast startup. If `verify_binaries()` detects missing or mismatched tool files, a non-blocking banner offers `"Repair tools"`.

### CLI Usage & Exit Codes
```bash
python main.py --cli --lang en
python main.py --cli --serial <DEVICE_SERIAL> --output-dir <PATH>
python main.py --check-bins
python main.py --repair-tools
```
Arguments parsed in `main.py`:
- `--cli`: run diagnostics in console mode without launching the GUI
- `--serial SERIAL`: target a specific device serial
- `--lang {ar,en}`: interface/report language (loaded dynamically from `locales/`, default `ar`)
- `--check-bins`: verify `bin/` against `bin/manifest.json` and print JSON status (no download)
- `--repair-tools`: download/reinstall missing or mismatched packages after SHA-256 verification
- `--output-dir OUTPUT_DIR`: custom folder for the CLI HTML report
- `--all-devices`: with `--cli`, diagnose every connected device in parallel (`--workers N`, default 4); one report per device, a summary table at the end, and the exit code of the worst device (`4` > `3` > `2` > `0`)
- `--companion`: with `--cli`, also run the companion app on the phone
- `--pdf`: also write a PDF copy of the CLI report (signed too when a signing key is used)
- `--sign-key PATH`: sign the CLI report with this Ed25519 private key (default: `ADP_SIGNING_KEY` environment variable)
- `--verify-qr TEXT --public-key PUB`: verify a report QR code (text or link)
- `--export-verifier DIR --public-key PUB`: write the offline QR verification page `DIR/adp_verify.html`
- `--gen-signing-key DIR`: create a new key pair in `DIR` (outside the program folder)
- `--verify-report REPORT --public-key PUB [--signature SIG]`: verify a signed report

CLI exit codes:
- `0`: run completed (individual checks may still be `FAIL`, `UNKNOWN`, or `NEEDS_ROOT`; also returned after reading variables in `fastboot` mode)
- `1`: no device found, or the given `--serial` is not connected
- `2`: device is `unauthorized`, `offline`, or in an unsupported ADB state
- `3`: partial results (one or more diagnostic stages failed and became `UNKNOWN`; the HTML report is still written)
- `4`: internal error (for example, `list_devices` failure, report write error, signing key error, or `--check-bins` failure)
- `5`: `--verify-report` only: the signature does not verify (report changed, other key, or bad signature file)

### Report Signing (Ed25519)

A key kept inside the program would let anyone sign, so the private key lives **outside** this folder and the program refuses a key path inside it.

```bash
python main.py --gen-signing-key D:\keys\adp
```
This writes `adp_signing_key.pem` (private: keep it secret and back it up) and `adp_signing_key.pub.pem` (public: give it to whoever verifies). An existing key is never overwritten. If `ADP_SIGNING_KEY_PASSPHRASE` is set, the private key is encrypted with it and the same variable is needed to sign.

Sign reports from the CLI with `--sign-key D:\keys\adp\adp_signing_key.pem`, or set `ADP_SIGNING_KEY` to that path for both CLI and GUI. A signed report's footer names the key id (first 16 hex digits of the SHA-256 of the public key), and `<report>.html.sig` (JSON) next to it holds the Ed25519 signature of the report file's exact bytes and their SHA-256. A bad or missing key fails before any report is written.

```bash
python main.py --verify-report Diagnostic_Report_X.html --public-key D:\keys\adp\adp_signing_key.pub.pem
```
Exit `0` means the file is unchanged and was signed by that key; exit `5` means it was not. The signature proves that the holder of that private key signed this exact file and that it was not changed afterwards; it does not make the readings themselves more accurate.

### Verifying a Printed Report (QR code)
A signed report's QR code carries a signed summary, so the result can be checked from the paper or PDF alone:
`ADP1;reference;serial;model;overall result;date;key id;signature` (fields URL-encoded; Ed25519 signature over everything before the last `;`). Without a signing key the QR keeps the plain, unsigned summary.

```bash
python main.py --verify-qr "ADP1;..." --public-key D:\keys\adp\adp_signing_key.pub.pem
```
Exit `0` = genuine, `5` = changed or signed with another key.

```bash
python main.py --export-verifier D:\site --public-key D:\keys\adp\adp_signing_key.pub.pem --lang ar
```
writes `adp_verify.html`, a single offline page with the shop's public key built in. It checks the QR text (pasted, scanned with the camera where the browser supports it, or passed after `#` in the link) with the browser's own Ed25519; nothing is sent anywhere. Publish it on an `https://` address and set `ADP_VERIFY_URL` to that address: report QR codes then become links that open the page and verify immediately on the customer's phone.

### Log File Location
Logs are written by a rotating file handler (`1 MB` per file, `5` backups) in:
- `%LOCALAPPDATA%\AndroidDoctorPro\logs\android_doctor.log` (or `~/.local/state/AndroidDoctorPro/logs/android_doctor.log` if `LOCALAPPDATA` is unset).

---

## Languages

All user-facing text lives in `locales/ar.json`, `locales/en.json` and `locales/fr.json` (the same keys in each file). The French translation is machine-written and should be reviewed by a native speaker before distribution; the placeholders and keys are checked by the tests. `ui/i18n.py` loads all `locales/*.json` files, exposes `available_languages()`, formats `{placeholders}` safely via `t()` / `render()`. Arabic letters are shaped by Windows (DirectWrite); `ui/theme.py` `rtl_wrap()` only adds direction marks. Core modules emit `Msg(key, args)` objects via `M()` rather than building display strings.

To add a language, copy `locales/en.json` to `locales/<code>.json`, translate every value and set `meta.name` (the language's own name, shown in the language switch) and `meta.dir` (`rtl` or `ltr`). Nothing else changes: right-to-left layout, the report, the verification page and the CLI `--lang` choices follow the file; the switch shows buttons for up to three languages and a list beyond that. `tests/test_i18n_keys.py` fails until every key is present.

To add or modify text:
1. Add the same key and matching `{placeholder}` names to both `locales/ar.json` and `locales/en.json`.
2. Reference the key from code via `M("key", ...)` (in `core/`) or `self._tr("key", ...)` / `t("key", lang, ...)` (in UI/CLI).

`tests/test_i18n_keys.py` statically parses `core/*.py`, `ui/*.py`, `main.py`, and `setup_bins.py` with `ast` and fails if:
- Any language in `locales/` has missing, extra, or empty keys or mismatched `{placeholders}`.
- Any key referenced by `M`, `Msg`, `t`, `_tr`, `tr`, progress keys, abnormal-boot rules, or `check_id` names is absent from any locale.
- Any Arabic character literal or language-branching UI string ternary (`if is_ar else`) appears inside Python code.

---

## Tests

Run the full test suite from the repository root:
```bash
python -m unittest discover -s tests -t .
```
All tests run against in-memory fakes (`tests/fakes.py`) or mocked bridges and store temporary SQLite databases and generated HTML reports inside `tempfile.TemporaryDirectory()`. Running the test suite does not touch `data/android_doctor.db` or `reports/`.

Test modules:
- `tests/test_verdict_system.py`: 4-state verdict contracts, `Evidence` truncation and `exit_code=None` default, USB vs. Wi-Fi current notes, BootROM `possible_causes`, `getevent` non-root error behavior, `display.panel_info`, and HTML report terms.
- `tests/test_status_matrix.py`: Command-routed `FakeBridge` read matrices (`normal`, `empty`, `permission denied` in stdout/stderr, `missing`, `malformed`), legacy shell vs. `shell_v2` parsing, strict integer and JEDEC parsers, and glob path handling.
- `tests/test_root_detection.py`: Root detection (granted, denied, missing `su`, non-root uid text) and `su` exit status on legacy transports.
- `tests/test_check_result_migration.py`: `to_check_result()` for every per-check class, `check_statuses` mirroring `checks`, report overall including failed CLI stages, legacy report dicts, and the GUI report including the screen-off current result.
- `tests/test_report_signing.py`: Key generation and refusal inside the program folder, encrypted keys, signed and unsigned reports in Arabic and English, single-byte tampering, forged hashes, other keys, bad signature files, CLI exit codes `0`/`4`/`5`.
- `tests/test_pdf_export.py`: Browser lookup, stale or non-PDF output rejected, timeout, private browser profile, bad key fails before printing, CLI `--pdf` exit codes, and a real Arabic report printed and signed when Edge/Chrome is installed.
- `tests/test_model_db.py`: Sourced entries only (missing URL, implausible or swapped capacities rejected), `/DS` suffix and case, user file overrides, rated capacity preferred, spec fills a missing design value but never a missing reading, kernel value wins with a note when it differs by more than 10%.
- `tests/test_multi_device.py`: Parallel runs (timed), worker limit, order kept, one crash isolated, database closed once, combined exit code, `--all-devices` CLI with one report per authorized device, the GUI button end to end, per-device ADB locks (different phones do not wait, the same phone stays in order).
- `tests/test_qr_verify.py`: Signed QR summary round trip, any changed field or another key rejected, link and percent-encoded forms, the report QR content (signed, https link only, unsigned plain), the offline page contents, the page checking signatures in a real Edge/Chrome, and CLI exit codes.
- `tests/test_companion.py`: Java app and Python agree on package, log tag, prefix, extras and field names; the app stores nothing; run flow (results until done, timeout keeps partial results, reinstall on another signing key, missing APK, failed install); every judging rule (battery vs rated spec and no verdict without reading/level/spec, live sensors, vibration, audio, cameras).
- `tests/test_languages.py`: Every locale declares `meta.name`/`meta.dir`; direction follows the metadata (layout, text wrapping, report, verification page, evidence text) for added RTL and LTR languages; the switch with two, three and four languages.
- `tests/test_app_paths.py`: Database/report paths unchanged from source, outside the install folder in a packaged build.
- `tests/test_ui_smoke.py`: Headless build of every GUI tab in Arabic and English, width fit, language switch, and status-pill rules.
- `tests/test_misleading_checks.py`: Preconditions for wireless-only screen-off current, per-category thermal limits, measurable vs. unmeasurable maintenance actions, `fastboot erase cache` precondition, WhatsApp media-only pulls, unsigned report notices, and granted-permission security inventories.
- `tests/test_i18n_keys.py`: Locale key parity, placeholder parity, AST scan for hardcoded text, and `check_id` translations.
- `tests/test_cli_and_tools.py`: CLI exit codes (`0`–`4`), isolated stage failures, `bin/manifest.json` schema, archive SHA-256 verification before extraction, path traversal rejection, and private ADB port `5038` lifecycle.
- `tests/test_ui_wiring.py`: Every GUI call into the core matches a real method signature, return-tuple size and result fields (AST + `inspect`), and no module uses an undefined global name.
- `tests/test_audit_fixes.py`: One behaviour test per issue confirmed in the external audit (partition and APK backups, Msg values saved to the database, battery/forensics/report verdicts, profiler values never invented, shell quoting, signing errors, ticket notes); each failed on the code before its fix.
- `tests/test_device_findings.py`: One test per issue found on a real phone, using that phone's own command output (`pm has-feature` and its exit-1 `false`, blocked battery current and cycle count shown as `NEEDS_ROOT`, an implausible -26 °C sensor listed but not judged, an unreadable sound-card list not counted as zero, camera sections after the device list, numbers kept left-to-right in the Arabic report, the wireless pairing code never logged); each failed on the code before its fix.

---

## Limitations Without Root

On an unrooted production Android device, SELinux restricts several paths and tools; the application marks these explicitly rather than faking results:
- **Kernel log (`dmesg`):** Typically restricted for shell users -> returns `NEEDS_ROOT` (retries with `su` in `MotherboardDiagnostics` when root is present).
- **`pstore` / `console-ramoops` (`/sys/fs/pstore/console-ramoops*`):** Usually blocked without root -> `NEEDS_ROOT`, or `UNKNOWN` if not created by the kernel.
- **Battery capacity (`charge_full`, `charge_full_design`):** Frequently blocked by SELinux or omitted by OEM BMS drivers -> `health_verdict` returns `NEEDS_ROOT` or `UNKNOWN`.
- **Storage wear registers (`life_time`, `pre_eol_info`, UFS `health_descriptor`):** Commonly blocked -> `StorageBenchmark` returns `NEEDS_ROOT` / `UNKNOWN`; `read_emmc_ufs_lifespan` retries with `su` when root is available.
- **Storage read benchmark:** Without root, Linux page cache cannot be flushed between write and read passes, so read speeds may reflect RAM cache.
- **User CA store (`/data/misc/user/0/cacerts-added`):** Typically restricted without root -> returns `NEEDS_ROOT`.
- **EFS / NVRAM / partition `dd` backups:** `backup_efs_nvram_partitions` requires `su -c id` (`uid=0`); `PartitionBackupManager` requires root for protected block devices.
- **Root forensics (`run_root_forensics_pro`):** Explicitly requires root and returns `NEEDS_ROOT` otherwise.

### What Software Cannot Prove at All
- **Original vs. replacement screen:** `display.panel_info` reads kernel controller strings and boot state properties; software cannot verify OEM screen originality.
- **Motherboard short circuits:** High battery discharge with the screen off lists possible hardware/software causes; isolating an electrical short requires bench hardware tools.
- **Vibrator motor, screen uniformity, touch dead zones, and speaker distortion:** Software triggers the motor, test color/grid pages, and tone playback; the technician must observe the hardware directly.
- **App intent:** Apps holding sensitive permissions, accessibility access, or device admin privileges are listed for human review, not labeled malicious.

---

## Known Limitations

- **Physical device coverage:** All automated tests (258) use mocked/fake ADB responses (`tests/fakes.py`). Checked on one real phone so far (Redmi Note 9, MT6768, Android 12, not rooted) over USB: shell v2 exit codes, CLI diagnostics and exit codes `0`/`1`/`4`/`5`, Arabic and English HTML and PDF reports, signing and verification, and `scrcpy` mirroring. Not yet checked on a real phone: wireless ADB and the screen-off battery current test, a rooted phone, `scrcpy --otg`, and the GUI tabs.
- **Non-Windows tool manifests:** Download URLs and archive SHA-256 hashes are pinned for all 5 platforms, but pre-listed per-file `scrcpy` SHA-256 entries cover `windows-x86_64` only, and non-Windows archive downloads have not been runtime-tested.
- **Signing is opt-in:** Reports are signed only when a key is configured (`--sign-key` / `ADP_SIGNING_KEY`); keeping the private key safe is the technician's job.

---

## Project Structure

```text
android/
├── bin/                   # Pinned platform-tools (37.0.1), scrcpy (5.0.1), and manifest.json
├── core/                  # ADB shell_v2 bridge, shared readers, CheckResult/Msg contracts, diagnostics, rescue, report signing
├── data/                  # Runtime SQLite DB (android_doctor.db), fault_signatures.json, thermal_thresholds.json
├── database/              # SQLite schema and persistence manager (db_manager.py)
├── locales/               # Complete Arabic (ar.json) and English (en.json) translation files
├── reports/               # Exported HTML Diagnostic Report output directory
├── tests/                 # Unit, contract, matrix, i18n AST, and CLI/tool verification tests
├── ui/                    # CustomTkinter window (main_window.py), theme, i18n renderer, HTML report exporter
│   └── tabs/              # One mixin per GUI tab (dashboard, board_diag, hardware, security, optimizer, rescue, reports)
├── main.py                # Application entrypoint (GUI default, --cli console mode)
├── app_paths.py           # Database/report locations (source run vs. packaged build)
├── companion/             # Android companion app source (Java, no Gradle) and build.py
├── packaging/             # PyInstaller spec, build script, build-only requirements
├── setup_bins.py          # Manifest verification, hash-checked repair, and port 5038 configuration
├── requirements.txt       # Base GUI Python package dependencies
├── run.bat                # Windows GUI launcher (--lang ar)
└── README.md              # Project documentation
```
