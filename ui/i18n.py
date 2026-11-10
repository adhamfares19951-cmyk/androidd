import logging
import arabic_reshaper
from bidi.algorithm import get_display

logger = logging.getLogger("i18n")

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
    
    # Check if text contains any Arabic Unicode codepoints
    has_arabic = any(
        '\u0600' <= c <= '\u06FF' or 
        '\u0750' <= c <= '\u077F' or 
        '\u08A0' <= c <= '\u08FF' or 
        '\uFB50' <= c <= '\uFDFF' or 
        '\uFE70' <= c <= '\uFEFF'
        for c in text
    )
    if not has_arabic:
        return text
        
    try:
        # Handle multiline text properly line by line to preserve line breaks
        if "\n" in text:
            lines = text.split("\n")
            processed = [get_display(reshaper.reshape(line)) if line.strip() else line for line in lines]
            return "\n".join(processed)
        return get_display(reshaper.reshape(text))
    except Exception as e:
        logger.debug(f"Arabic reshape error: {e}")
        return text


STRINGS = {
    "en": {
        "app_title": "ANDROID DOCTOR PRO",
        "app_subtitle": "Hardware Diagnostics & Deep System Suite",
        "target_device": "TARGET DEVICE:",
        "refresh_devices": "Refresh Devices",
        "fix_connection": "Connection Troubleshooting",
        "no_devices": "No Devices Found",
        "adb_disconnected": "ADB: Disconnected | No Devices",
        
        # Tabs
        "tab_dashboard": "Dashboard & Specs",
        "tab_board_diag": "Motherboard & ICs",
        "tab_hardware": "Hardware & Sensors",
        "tab_malware": "Security & Permissions",
        "tab_optimizer": "System Maintenance",
        "tab_rescue": "Rescue & Recovery",
        "tab_reports": "Diagnostic Reports",
        
        # Dashboard
        "dash_title": "Device Overview & Hardware Profiler",
        "dash_sub": "Complete low-level inspection of motherboard architecture, SoC, display, and memory.",
        "btn_full_scan": "Full Deep Diagnostic Scan",
        "btn_reboot_sys": "Reboot System",
        "btn_reboot_rec": "Reboot Recovery",
        "btn_reboot_bl": "Reboot Bootloader",
        "core_specs": "CORE HARDWARE SPECIFICATIONS",
        "live_gauges": "LIVE SYSTEM GAUGES",
        
        # Specs labels
        "spec_model": "Brand & Model:",
        "spec_serial": "Serial Number:",
        "spec_soc": "Chipset / SoC:",
        "spec_cores": "CPU Cores:",
        "spec_android": "Android Version:",
        "spec_build_id": "Build ID / Fingerprint:",
        "spec_security": "Security Patch Level:",
        "spec_kernel": "Kernel Version:",
        "spec_root": "Root Access Status:",
        "spec_selinux": "SELinux Enforce Mode:",
        "spec_display": "Display Spec:",
        
        # Connection guide & alerts
        "guide_title": "HOW TO CONNECT YOUR PHONE (STEP BY STEP)",
        "guide_step1": "1. Go to Settings > About Phone > Tap 'Build Number' 7 times until Developer Mode is unlocked.",
        "guide_step2": "2. Open Settings > Developer Options > Enable 'USB Debugging'.",
        "guide_step3": "3. Connect USB cable, unlock your phone screen, and tap 'Allow' on the prompt.",
        "guide_btn_restart_adb": "Restart ADB Server & Scan",
        "alert_unauthorized_title": "Connected Phone is UNAUTHORIZED",
        "alert_unauthorized_desc": "Device is connected via USB, but you MUST unlock your phone screen and tap 'ALLOW' on the 'Allow USB debugging?' prompt.",
        "alert_offline_title": "Phone is in OFFLINE State",
        "alert_offline_desc": "Device is connected but unresponsive. Please unplug and re-plug the USB cable, and ensure the phone screen is awake.",
        "alert_pnp_title": "Phone Detected on USB, but USB Debugging is DISABLED!",
        "alert_pnp_desc": "Windows detected your device on USB ({names}), but it is only in charging/MTP mode. To diagnose:\n1. Go to Settings > About Phone > Tap 'Build Number' 7 times.\n2. Go to Settings > Developer Options > Turn ON 'USB Debugging'.",
        
        # Motherboard Tab
        "board_title": "Battery Fuel Gauge, Charging IC & Thermal Diagnostics",
        "board_sub": "Analyzes vendor sysfs kernel nodes, Battery voltage (fuel gauge), battery current, and thermal zone maps.",
        "btn_pmic": "Analyze Battery & Charging (PMIC)",
        "btn_leakage": "Test Battery Current (30s)",
        "btn_thermals": "Scan Thermal Zones",
        "btn_panics": "Scan Kernel Panics & UFS/eMMC",
        "btn_scan_bootrom": "Dead Phone / BootROM USB Scanner",
        "btn_fast_charge": "Diagnose Fast Charge Protocol",
        "btn_reset_gauge": "Reset Battery Statistics",
        "btn_nand_lifespan": "eMMC/UFS Lifespan & Wear",
        "btn_speed_bench": "Storage Speed Benchmark (64MB)",
        "btn_stress_test": "30s CPU & PMIC Stress Test",
        "card_pmic": "BATTERY FUEL GAUGE & CHARGING IC (PMIC)",
        "card_fast_charge": "FAST CHARGING PROTOCOL & POWER NEGOTIATION",
        "card_nand_wear": "eMMC / UFS CHIP LIFESPAN & WEAR LEVEL",
        "card_stress_test": "30-SECOND CPU & PMIC LOAD / THROTTLING STRESS TEST",
        "card_leakage": "SCREEN-OFF BATTERY CURRENT (wireless ADB, no charger)",
        "card_thermal": "THERMAL ZONE TEMPERATURE MAP",
        "card_panics": "NAND (UFS/eMMC) & KERNEL PANICS",
        "card_bootrom": "NON-BOOTING DEVICE & BOOTROM USB DIAGNOSIS",
        
        # Hardware Tab
        "hw_title": "Interactive Hardware & Peripherals Diagnostics",
        "hw_sub": "Inspect reported sensors, biometric IC, physical keys continuity, SIM reader, camera, and display backlight.",
        "btn_audit_hw": "Full Component & Peripherals Audit",
        "btn_storage_bench": "Test Storage Wear & Speed",
        "btn_touch_matrix": "Interactive Touch Digitizer Grid Test",
        "btn_test_vib": "Vibrate Motor (1s)",
        "btn_test_audio": "Audio Sound Test",
        "switch_pointer": "Touch Pointer Overlay",
        "card_sensors": "Reported sensors (Sensor HAL)",
        "card_cameras": "CAMERA & AUDIO SUBSYSTEMS",
        "card_audio_codec": "AUDIO CODEC IC & ALSA SOUNDCARDS",
        "card_panel_info": "DISPLAY PANEL INFO & VERIFIED BOOT STATE",
        "card_storage_bench": "NAND STORAGE (eMMC/UFS) LIFE CYCLE & SPEED BENCHMARK",
        "card_touch_matrix": "TOUCH SCREEN DIGITIZER MATRIX TEST",
        "card_fingerprint": "BIOMETRIC / FINGERPRINT SENSOR IC & FLEX",
        "card_keys": "PHYSICAL KEYS (PRESS & RELEASE TEST)",
        "card_sim": "SIM CARD READER & BASEBAND RF HEALTH",
        "card_backlight": "DISPLAY PANEL & BACKLIGHT IC CHECK",
        "card_wifi": "WI-FI CHIPSET & SIGNAL DIAGNOSTICS",
        "card_bluetooth": "BLUETOOTH ADAPTER & BLE DIAGNOSTICS",
        "card_chips": "PERIPHERAL CHIPS (GPS, NFC, USB OTG)",
        
        # Malware Tab
        "malware_title": "Apps with Sensitive Permissions & Debloater",
        "malware_sub": "Lists third-party apps holding sensitive permissions, enabled accessibility services, device admins, user-installed CA certificates and network connections. This is an inventory for review, not a malware verdict.",
        "btn_run_sec": "List Apps with Sensitive Permissions",
        "btn_sniff_net": "Sniff Active Network Sockets",
        "btn_rollback": "Rollback / Restore Center",
        "card_threats": "APPS WITH SENSITIVE PERMISSIONS (FOR REVIEW)",
        "target_pkg": "Target Package:",
        "btn_restrict": "Restrict Background",
        "btn_disable": "Disable App",
        "btn_uninstall": "Force Uninstall",
        "card_net": "ACTIVE NETWORK CONNECTIONS",
        
        # Optimizer Tab
        "opt_title": "System Maintenance (measured results only)",
        "opt_sub": "Trims app caches (free space measured before/after) and starts Android's own storage-maintenance and app-compilation jobs (their effect cannot be measured). No files are deleted and no settings are changed.",
        "btn_run_opt": "Run Maintenance (3 steps)",
        "gain_ram": "Available RAM (before -> after)",
        "gain_storage": "Free space change (cache trim)",
        "gain_drain": "Battery drain (not measured)",
        "card_opt_log": "REAL EXECUTION LOG & PROTOCOL OUTPUT",
        
        # Rescue & Recovery Tab
        "rescue_title": "Broken Screen, Fastboot & System Recovery Center",
        "rescue_sub": "Launch low-latency scrcpy screen mirroring, inspect setup wizard flags, erase the cache partition, flash images, and rescue user data.",
        "card_mirror": "BROKEN SCREEN INTERACTIVE CONTROL (scrcpy)",
        "btn_launch_mirror": "Launch Screen Mirror",
        "btn_stop_mirror": "Stop Mirror",
        "check_turn_off": "Turn Phone Screen Off (Power Saver)",
        "card_provisioning": "SETUP WIZARD & SYSTEM PROVISIONING",
        "btn_check_prov": "Inspect Setup Flags",
        "btn_fix_prov": "Repair Stuck Setup Wizard",
        "btn_open_settings": "Launch Settings App",
        "card_fb": "FASTBOOT & BOOTLOOP REPAIR",
        "btn_fb_vars": "Read Fastboot Variables",
        "btn_fix_bootloop": "Erase Cache Partition (if present)",
        "btn_fb_reboot": "Reboot to System",
        "card_ab_slot": "FASTBOOT ACTIVE SLOT (A/B) & ANTI-ROLLBACK (ARB)",
        "btn_switch_slot_a": "Switch to Slot A",
        "btn_switch_slot_b": "Switch to Slot B",
        "btn_check_arb": "Check Anti-Rollback (ARB) Index",
        "card_partition_backup": "CRITICAL PARTITIONS & RADIO BACKUP (EFS / MODEM / PERSIST)",
        "btn_backup_critical": "Backup Critical Partitions (EFS/Modem)",
        "btn_backup_all": "Backup All Partitions (Full Dump)",
        "btn_open_backups": "Open Backups Folder",
        "card_batch_apk": "BATCH APK INSTALLER & APP BACKUP EXTRACTOR",
        "btn_install_batch_apk": "Batch Install APK Files",
        "btn_extract_apps": "Extract User Apps to PC",
        "lbl_partition": "Partition:",
        "btn_browse": "Browse",
        "btn_flash": "Flash Image",
        "card_data": "EMERGENCY USER DATA EXTRACTION",
        "lbl_category": "Category:",
        "btn_pull_data": "Pull & Rescue to Desktop",
        "card_sideload": "ADB SIDELOAD OTA ROM FLASHER",
        "btn_browse_ota": "Browse OTA (.zip)",
        "btn_flash_sideload": "Start Sideload OTA Update",
        "btn_one_click_backup": "1-Click Full Customer Backup (Media + Contacts + SMS)",
        
        # OTG & Control Pad
        "btn_scrcpy_otg": "🖱️ Launch OTG Mouse/Keyboard Mode (No ADB Required)",
        "desc_scrcpy_otg": "Hardware USB HID emulation. Control broken-screen or locked phone using PC mouse & keyboard (Zero ADB required).",
        "control_pad_title": "VIRTUAL ON-SCREEN CONTROL PAD",
        "card_vpad": "VIRTUAL ON-SCREEN CONTROL PAD & NAVIGATION",
        "lbl_vpad_pin": "PIN / Code:",
        "btn_send_pin": "Enter PIN",
        "btn_send_enter": "Enter",
        "btn_swipe_unlock": "Swipe Up (Unlock)",
        "btn_key_power": "Power",
        "btn_key_vol_up": "Vol +",
        "btn_key_vol_down": "Vol -",
        "btn_key_back": "Back",
        "btn_key_home": "Home",
        "btn_key_menu": "Menu",
        
        # Wireless ADB
        "wireless_title": "WIRELESS ADB (UNPLUGGED TESTING)",
        "card_wireless_adb": "WIRELESS ADB & REMOTE CONNECTION",
        "btn_enable_wireless": "Enable Wireless ADB (Unplug USB)",
        "btn_pair_wireless": "Pair (Android 11+)",
        "btn_disconnect_wireless": "Disconnect Wireless",
        "lbl_wireless_target": "Device IP:Port:",
        "btn_wireless_connect": "Connect",
        "lbl_wireless_pair": "Pair Host:Port:",
        "lbl_wireless_code": "6-Digit Code:",
        
        # Debloater & Blackbox
        "debloat_title": "SAFE OEM BLOATWARE REMOVER (UAD-ng)",
        "card_bloatware": "SAFE OEM SYSTEM DEBLOATER (UAD-ng DATABASE)",
        "btn_scan_bloat": "Scan OEM Bloatware",
        "btn_debloat_selected": "Safe One-Click Debloat",
        "btn_restore_debloat": "Restore Debloated Apps",
        "blackbox_title": "SUDDEN SHUTDOWN & REBOOT FORENSICS (BLACKBOX)",
        "card_blackbox": "BLACKBOX FORENSICS & CRITICAL EVENT AUDIT",
        "btn_run_blackbox": "Run Blackbox Forensics",
        "btn_reset_battery": "Reset Battery Statistics (batterystats --reset)",
        "card_telemetry": "LIVE HARDWARE TELEMETRY (1.5s POLLING)",
        "btn_live_telemetry": "Live Telemetry",
        
        # Reports Tab
        "reports_title": "Diagnostic Report Center",
        "reports_sub": "Export HTML / printable diagnostic reports with the raw evidence behind every check.",
        "lbl_tech_name": "Lead Technician Name:",
        "btn_gen_cert": "Generate Diagnostic Report",
        "btn_open_folder": "Open Reports Folder",
        "card_past_reports": "PAST DIAGNOSTIC REPORTS & INSPECTION HISTORY",
        
        # Interactive Testing & Root Forensics Pro
        "btn_lcd_color_test": "🎨 LCD Color & Dead-Pixel Test",
        "btn_speaker_test": "🔊 Speaker Test Tone (1kHz)",
        "btn_companion_diag": "📱 Companion APK Test",
        "card_root_forensics": "ROOT FORENSICS PRO & PARTITION RAW DUMP",
        "btn_run_root_forensics": "🔓 Kernel & Ramoops Forensics (Root)",
        "btn_backup_efs_raw": "💾 1-Click EFS / NVRAM Raw Backup",
        "card_cust_ticket": "CUSTOMER REPAIR TICKET & INTAKE INFORMATION",
        "lbl_cust_name": "Customer Name:",
        "lbl_cust_phone": "Customer Phone:",
        "lbl_cust_fault": "Reported Fault:",
        "lbl_repair_cost": "Estimated Cost ($):",
        "btn_gen_signed_cert": "📜 Generate Diagnostic Report (with QR)",
        "msg_ticket_created": "Repair ticket #{ticket_id} created successfully!",
        "btn_show_evidence": "🔍 Show Evidence",
        "btn_hide_evidence": "Hide Evidence",
    },
    
    "ar": {
        "app_title": "أندرويد دكتور برو — فاحص الهواتف والمازربورد",
        "app_subtitle": "فحص اللوحة الأم، اختبار الهاردوير، مراجعة الأذونات والصيانة",
        "target_device": "الجهاز المستهدف:",
        "refresh_devices": "تحديث قائمة الأجهزة",
        "fix_connection": "حل مشكلة عدم ظهور الهاتف",
        "no_devices": "لا توجد أجهزة متصلة",
        "adb_disconnected": "الاتصال غير متصل: بانتظار توصيل الهاتف عبر USB",
        
        # Tabs
        "tab_dashboard": "لوحة التحكم والمواصفات",
        "tab_board_diag": "اللوحة الأم وآيسيات الشحن",
        "tab_hardware": "فحص الحساسات والقطع",
        "tab_malware": "الأمان والأذونات",
        "tab_optimizer": "صيانة النظام",
        "tab_rescue": "الإنقاذ واستعادة النظام",
        "tab_reports": "تقارير الفحص والتشخيص",
        
        # Dashboard
        "dash_title": "نظرة عامة ومواصفات العتاد والمعالج",
        "dash_sub": "فحص منخفض المستوى للمعالج، اللوحة الأم، البطارية، الرام والذاكرة الحقيقية.",
        "btn_full_scan": "بدء الفحص الشامل للعتاد",
        "btn_reboot_sys": "إعادة تشغيل الهاتف",
        "btn_reboot_rec": "إعادة تشغيل ريكفري",
        "btn_reboot_bl": "إعادة تشغيل فاست بوت",
        "core_specs": "المواصفات التقنية للهاتف",
        "live_gauges": "المؤشرات الحية للعتاد والموارد",
        
        # Specs labels
        "spec_model": "الشركة والموديل:",
        "spec_serial": "الرقم التسلسلي:",
        "spec_soc": "نوع المعالج والشريحة:",
        "spec_cores": "عدد أنوية المعالج:",
        "spec_android": "إصدار نظام أندرويد:",
        "spec_build_id": "رقم البناء:",
        "spec_security": "مستوى التحديث الأمني:",
        "spec_kernel": "إصدار كيرنل النظام:",
        "spec_root": "حالة الروت:",
        "spec_selinux": "وضع الحماية SELinux:",
        "spec_display": "أبعاد وتردد الشاشة:",
        
        # Connection guide & alerts
        "guide_title": "دليل توصيل الهاتف خطوة بخطوة في حال لم يظهر جهازك:",
        "guide_step1": "الخطوة الأولى: ادخل إلى الإعدادات ثم حول الهاتف، واضغط 7 مرات على رقم الإصدار لتفعيل خيارات المطور.",
        "guide_step2": "الخطوة الثانية: ادخل إلى الإعدادات ثم خيارات المطور، وفعّل خيار تصحيح أخطاء USB.",
        "guide_step3": "الخطوة الثالثة: وصّل كابل USB وافتح قفل شاشة الهاتف، ستظهر رسالة السماح بتصحيح USB اضغط موافق.",
        "guide_btn_restart_adb": "إعادة تشغيل خادم الاتصال",
        "alert_unauthorized_title": "تم رصد هاتف متصل ولكنه غير مصرح (Unauthorized)",
        "alert_unauthorized_desc": "الهاتف متصل عبر USB، يرجى فتح شاشة القفل والضغط على 'موافق' في رسالة السماح بتصحيح أخطاء USB.",
        "alert_offline_title": "الهاتف متصل بوضع غير متصل (Offline)",
        "alert_offline_desc": "الهاتف متصل ولكن الاتصال معلق. يرجى فصل كابل USB وإعادة تركيبه وفتح شاشة الهاتف.",
        "alert_pnp_title": "تم رصد هاتف متصل ولكن خيار تصحيح USB غير مفعل!",
        "alert_pnp_desc": "الكمبيوتر يرى جهازك ({names}) بوضع الشحن فقط. لتشغيل الفحص:\nالخطوة الأولى: افتح الإعدادات ثم حول الهاتف واضغط 7 مرات على رقم الإصدار.\nالخطوة الثانية: ادخل خيارات المطور وفعّل خيار تصحيح أخطاء USB.",
        
        # Motherboard Tab
        "board_title": "فحص بطارية ومقياس الوقود والشحن (PMIC) والحرارة",
        "board_sub": "يقيس جهد البطارية عبر مقياس الوقود، وتيار البطارية الحقيقي، وحرارة الآيسيات ومنافذ BootROM للطوارئ.",
        "btn_pmic": "فحص بطارية وشحن الهاتف (PMIC)",
        "btn_leakage": "اختبار تيار البطارية (30 ثانية)",
        "btn_thermals": "فحص درجات حرارة الآيسيات",
        "btn_panics": "فحص أخطاء ذاكرة UFS/eMMC والكيرنل",
        "btn_scan_bootrom": "فحص منافذ BootROM",
        "btn_fast_charge": "فحص بروتوكول الشحن السريع",
        "btn_reset_gauge": "إعادة ضبط إحصائيات استهلاك البطارية",
        "btn_nand_lifespan": "عمر واستهلاك خلايا الذاكرة",
        "btn_speed_bench": "اختبار سرعة الذاكرة (64MB)",
        "btn_stress_test": "اختبار ضغط المعالج والباور (30 ثانية)",
        "card_pmic": "مقياس وقود البطارية وآيسي الشحن (PMIC)",
        "card_fast_charge": "بروتوكول الشحن السريع وقدرة الواط الفعلية",
        "card_nand_wear": "مستوى استهلاك وعمر شريحة الذاكرة (eMMC/UFS Lifespan)",
        "card_stress_test": "اختبار إجهاد المعالج واستقرار دوائر الطاقة والحرارة (Stress Test)",
        "card_leakage": "تيار البطارية والشاشة مطفأة (ADB لاسلكي وبدون شاحن)",
        "card_thermal": "خريطة حرارة معالج الهاتف والآيسيات",
        "card_panics": "سجلات أخطاء ذاكرة التخزين وانهيار النظام",
        "card_bootrom": "تشخيص الأجهزة الميتة ومنافذ الطوارئ (EDL / BROM)",
        
        # Hardware Tab
        "hw_title": "الفحص التفاعلي للقطع والملحقات والحساسات",
        "hw_sub": "فحص المستشعرات المسجلة، حساس البصمة، أزرار الصوت والباور، بيت الشريحة، إضاءة الشاشة، والكاميرات.",
        "btn_audit_hw": "فحص شامل للملحقات والحساسات",
        "btn_storage_bench": "فحص عمر وسرعة الذاكرة الداخلية",
        "btn_touch_matrix": "اختبار شبكة اللمس التفاعلية للديجيتيزر",
        "btn_test_vib": "تجربة محرك الاهتزاز (1 ثانية)",
        "btn_test_audio": "تجربة الصوت ومكبر السماعة",
        "switch_pointer": "تتبع مسار اللمس",
        "card_sensors": "المستشعرات المسجلة (حسب نظام Sensor HAL)",
        "card_cameras": "كاميرات الهاتف ونظام الصوت",
        "card_audio_codec": "آيسي الصوت ومتحكمات ALSA الداخلية (Audio Codec IC)",
        "card_panel_info": "معلومات شريحة الشاشة وحالة الإقلاع المُتحقق",
        "card_storage_bench": "صحة واستهلاك وسرعة ذاكرة التخزين (eMMC / UFS)",
        "card_touch_matrix": "فحص مربعات اللمس للشاشة (Touch Grid)",
        "card_fingerprint": "فحص آيسي وفلاتة حساس البصمة (Fingerprint IC)",
        "card_keys": "أزرار الصوت والباور (اختبار الضغط والإفلات)",
        "card_sim": "بيت الشريحة ومعالج الشبكة والاتصال (Baseband)",
        "card_backlight": "فحص شاشة العرض وآيسي الإضاءة الخلفية (Backlight)",
        "card_wifi": "فحص بطاقة وهوائي الواي فاي (Wi-Fi Chipset)",
        "card_bluetooth": "فحص محول وبلوتوث الطاقة المنخفضة (Bluetooth & BLE)",
        "card_chips": "فحص شرائح الموقع GPS وحساس NFC ومنفذ OTG",
        
        # Malware Tab
        "malware_title": "التطبيقات ذات الأذونات الحساسة وإزالة التطبيقات المثبتة مسبقاً",
        "malware_sub": "يعرض تطبيقات المستخدم التي تملك أذونات حساسة، وخدمات إمكانية الوصول المفعلة، ومديري الجهاز، وشهادات CA المثبتة من المستخدم، والاتصالات الشبكية. هذه قائمة للمراجعة وليست حكماً بوجود برمجيات خبيثة.",
        "btn_run_sec": "عرض التطبيقات ذات الأذونات الحساسة",
        "btn_sniff_net": "مراقبة الاتصالات الخارجية النشطة",
        "btn_rollback": "سجل استرجاع التطبيقات المحذوفة",
        "card_threats": "التطبيقات ذات الأذونات الحساسة (للمراجعة)",
        "target_pkg": "اسم حزمة التطبيق:",
        "btn_restrict": "منع العمل بالخلفية",
        "btn_disable": "تعطيل التطبيق",
        "btn_uninstall": "إلغاء التثبيت الإجباري",
        "card_net": "الاتصالات الشبكية النشطة",
        
        # Optimizer Tab
        "opt_title": "صيانة النظام (نتائج مقاسة فقط)",
        "opt_sub": "تنظيف كاش التطبيقات (مع قياس المساحة الحرة قبل وبعد) وتشغيل مهام صيانة التخزين وتجميع التطبيقات الخاصة بأندرويد (لا يمكن قياس أثرها). لا يتم حذف أي ملفات ولا تغيير أي إعدادات.",
        "btn_run_opt": "تشغيل الصيانة (3 خطوات)",
        "gain_ram": "الرام المتاحة (قبل -> بعد)",
        "gain_storage": "تغير المساحة الحرة (تنظيف الكاش)",
        "gain_drain": "استهلاك البطارية (غير مقاس)",
        "card_opt_log": "سجل تنفيذ عمليات الصيانة خطوة بخطوة",
        
        # Rescue & Recovery Tab
        "rescue_title": "مركز الإنقاذ، معالج التهيئة واستعادة النظام",
        "rescue_sub": "عرض شاشة الهاتف والتحكم به بالماوس، فحص معالج التهيئة الأولي، مسح قسم الكاش، وسحب البيانات.",
        "card_mirror": "التحكم بالهاتف للشاشات المكسورة (Scrcpy)",
        "btn_launch_mirror": "تشغيل عرض الشاشة والتحكم",
        "btn_stop_mirror": "إيقاف العرض",
        "check_turn_off": "إطفاء شاشة الهاتف لتوفير البطارية والحرارة",
        "card_provisioning": "إعدادات معالج التهيئة الأولي (Setup Wizard)",
        "btn_check_prov": "فحص حالة معالج التهيئة",
        "btn_fix_prov": "تخطي وتثبيت معالج التهيئة العالق",
        "btn_open_settings": "فتح إعدادات الهاتف",
        "card_fb": "أدوات وضع الفاست بوت (Fastboot)",
        "btn_fb_vars": "قراءة معلومات البوت لودر",
        "btn_fix_bootloop": "مسح قسم الكاش (إن وُجد)",
        "btn_fb_reboot": "إعادة التشغيل للوضع الطبيعي",
        "card_ab_slot": "تبديل سلوت الإقلاع (Slot A/B) وحماية الرجوع (ARB)",
        "btn_switch_slot_a": "التحويل إلى السلوت A",
        "btn_switch_slot_b": "التحويل إلى السلوت B",
        "btn_check_arb": "فحص حماية الرجوع (Anti-Rollback)",
        "card_partition_backup": "نسخ الأقسام الحساسة والشبكة (EFS / Modem / Persist)",
        "btn_backup_critical": "نسخ أقسام الشبكة الحساسة (EFS/Modem)",
        "btn_backup_all": "نسخ احتياطي لكامل أقسام الهاتف",
        "btn_open_backups": "فتح مجلد النسخ الاحتياطية",
        "card_batch_apk": "تثبيت التطبيقات دفعة واحدة وسحبها للكمبيوتر",
        "btn_install_batch_apk": "تثبيت ملفات APK دفعة واحدة",
        "btn_extract_apps": "سحب تطبيقات الهاتف وحفظها بالكمبيوتر",
        "lbl_partition": "القسم (Partition):",
        "btn_browse": "استعراض ملف",
        "btn_flash": "تفليش الملف",
        "card_data": "سحب وإنقاذ بيانات المستخدم للطوارئ",
        "lbl_category": "نوع الملفات:",
        "btn_pull_data": "سحب وحفظ الملفات على سطح المكتب",
        "card_sideload": "تثبيت وتحديث الروم الرسمي (ADB Sideload OTA Flasher)",
        "btn_browse_ota": "اختيار ملف الروم (.zip)",
        "btn_flash_sideload": "بدء التفليش عبر وضع Sideload",
        "btn_one_click_backup": "النسخ الشامل لبيانات العميل (صور + جهات اتصال + رسائل)",
        
        # OTG & Control Pad
        "btn_scrcpy_otg": "🖱️ وضع التحكم بالماوس والكيبورد OTG (بدون تصحيح USB)",
        "desc_scrcpy_otg": "محاكاة ماوس ولوحة مفاتيح حقيقية عبر منفذ USB للتحكم بالهاتف عند كسر الشاشة أو تعطل اللمس حتى لو كان تصحيح USB مغلقاً.",
        "control_pad_title": "لوحة التحكم بالأزرار الافتراضية",
        "card_vpad": "لوحة التحكم الافتراضية بالأزرار والإيماءات",
        "lbl_vpad_pin": "رمز القفل / PIN:",
        "btn_send_pin": "إدخال الرمز",
        "btn_send_enter": "إدخال (Enter)",
        "btn_swipe_unlock": "سحب لفتح القفل",
        "btn_key_power": "زر الباور",
        "btn_key_vol_up": "رفع الصوت",
        "btn_key_vol_down": "خفض الصوت",
        "btn_key_back": "رجوع",
        "btn_key_home": "الرئيسية",
        "btn_key_menu": "القائمة",
        
        # Wireless ADB
        "wireless_title": "الاتصال اللاسلكي بدون كابل USB",
        "card_wireless_adb": "الاتصال اللاسلكي بالأجهزة عبر الشبكة (Wireless ADB)",
        "btn_enable_wireless": "تفعيل وضع ADB اللاسلكي (لفصل السلك)",
        "btn_pair_wireless": "اقتران أندرويد 11+ بالرمز",
        "btn_disconnect_wireless": "فصل الاتصال اللاسلكي",
        "lbl_wireless_target": "عنوان الهاتف والمنفذ:",
        "btn_wireless_connect": "اتصال",
        "lbl_wireless_pair": "عنوان الاقتران والمنفذ:",
        "lbl_wireless_code": "رمز الاقتران (6 أرقام):",
        
        # Debloater & Blackbox
        "debloat_title": "حذف تطبيقات المصنع الاستنزافية (قاعدة بيانات UAD-ng)",
        "card_bloatware": "أداة حذف تطبيقات النظام الآمنة (قاعدة بيانات UAD-ng)",
        "btn_scan_bloat": "فحص تطبيقات النظام الزائدة",
        "btn_debloat_selected": "حذف التطبيقات المحددة بأمان",
        "btn_restore_debloat": "استرجاع التطبيقات المحذوفة",
        "blackbox_title": "تشخيص أسباب الانطفاء المفاجئ (الصندوق الأسود)",
        "card_blackbox": "تشخيص الصندوق الأسود وأسباب الانطفاء المفاجئ",
        "btn_run_blackbox": "تشخيص أسباب إعادة التشغيل المفاجئ",
        "btn_reset_battery": "إعادة ضبط إحصائيات استهلاك البطارية (batterystats --reset)",
        "card_telemetry": "المراقبة اللحظية لفولت وسحب اللوحة والحرارة (تحديث كل 1.5 ثانية)",
        "btn_live_telemetry": "المراقبة اللحظية",
        
        # Reports Tab
        "reports_title": "مركز تقارير الفحص والتشخيص الفني",
        "reports_sub": "توليد تقرير فحص تشخيصي قابل للطباعة بصيغة HTML مع الأدلة الخام لكل فحص.",
        "lbl_tech_name": "اسم الفني المسؤول:",
        "btn_gen_cert": "استخراج تقرير الفحص التشخيصي",
        "btn_open_folder": "فتح مجلد التقارير",
        "card_past_reports": "سجل تقارير الفحص والتشخيص السابقة",
        
        # Interactive Testing & Root Forensics Pro
        "btn_lcd_color_test": "🎨 فحص الشاشة والبكسلات الميتة (ألوان كاملة)",
        "btn_speaker_test": "🔊 فحص مكبر الصوت (نغمة ترددية)",
        "btn_companion_diag": "📱 فحص التطبيق المساعد التفاعلي",
        "card_root_forensics": "التحليل الجنائي المتقدم (Root Forensics Pro) والنسخ الخام",
        "btn_run_root_forensics": "🔓 التحليل الجنائي للكيرنل وسجلات الانهيار (Root)",
        "btn_backup_efs_raw": "💾 نسخ أقسام EFS و NVRAM الحساسة كصور خام",
        "card_cust_ticket": "بيانات تذكرة استلام العميل وقسيمة الصيانة الفنية",
        "lbl_cust_name": "اسم العميل:",
        "lbl_cust_phone": "رقم هاتف العميل:",
        "lbl_cust_fault": "العطل المشتكى منه:",
        "lbl_repair_cost": "التكلفة المقدرة ($):",
        "btn_gen_signed_cert": "📜 استخراج تقرير فحص تشخيصي (مع رمز QR)",
        "msg_ticket_created": "تم إنشاء تذكرة الصيانة رقم {ticket_id} بنجاح!",
        "btn_show_evidence": "🔍 عرض الدليل التقني",
        "btn_hide_evidence": "إخفاء الدليل",
    }
}


def t(key: str, lang: str = "ar") -> str:
    lang_dict = STRINGS.get(lang, STRINGS["ar"])
    return lang_dict.get(key, STRINGS["en"].get(key, key))
