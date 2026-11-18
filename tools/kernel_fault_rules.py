MT="https://github.com/LineageOS/android_kernel_xiaomi_mt6768/blob/lineage-23.2/"
QC="https://github.com/LineageOS/android_kernel_xiaomi_sm6150/blob/lineage-23.2/"
SS="https://github.com/LineageOS/android_kernel_samsung_exynos9820/blob/lineage-23.2/"
def src(base, path, *msgs): return [{"url": base+path, "message": m} for m in msgs]

R = []
def rule(**k): R.append(k)

# ---------------------------------------------------------------- charging port
rule(id="USB_CC_VBUS_SHORT", category="CHARGING_PORT", severity="HIGH", board_areas=["charging", "power"],
     pattern=r"(?:CC|SBU)-Vbus Short case",
     kernel_source=src(SS, "drivers/muic/max77705/max77705-muic-afc.c", "%s:%s CC-Vbus Short case\\n", "%s:%s SBU-Vbus Short case\\n"),
     description_en="The USB-C controller found a short between VBUS (charging power) and a CC or SBU pin of the charging port.",
     description_ar="وجد متحكم USB-C قصراً (شورت) بين خط الشحن VBUS وأحد أطراف CC أو SBU في مدخل الشحن.",
     suspect_components=["Liquid or corrosion inside the charging port", "Damaged charging port (bent or burnt pins)", "Charging port flex / sub-board", "A damaged cable or accessory plugged in"],
     suspect_components_ar=["سائل أو أكسدة داخل مدخل الشحن", "مدخل شحن تالف (أطراف معوجة أو محروقة)", "فلاتة أو بورد الشحن الفرعي", "كابل أو ملحق تالف موصول"],
     action_en="Possible causes, most common first: liquid or corrosion in the port, a damaged port, its flex or sub-board, a damaged cable. Try another original cable first, then clean and dry the port, then check the port flex.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: سائل أو أكسدة في المدخل، مدخل تالف، فلاتته أو بورده الفرعي، كابل تالف. جرّب كابلاً أصلياً آخر أولاً، ثم نظّف المدخل وجففه، ثم افحص فلاتة الشحن.")

rule(id="USB_PORT_MOISTURE", category="CHARGING_PORT", severity="HIGH", board_areas=["charging"],
     pattern=r"Water is detected|Moisture Detection|water detected\(|CCIC_NOTIFY_ID_WATER\b|water hiccup mode",
     kernel_source=src(MT, "drivers/misc/mediatek/typec/tcpc/rt_pd_manager.c", "%s Water is detected in KPOC\\n")
       + src(MT, "drivers/power/supply/mtk_charger.c", "[%s] CASE7: Moisture Detection\\n")
       + src(SS, "drivers/muic/max77705/max77705-muic-afc.c", "%s water detected(%d), skip AFC\\n")
       + src(SS, "drivers/muic/max77705/max77705-muic-ccic.c", "%s: CCIC_NOTIFY_ID_WATER\\n")
       + src(SS, "drivers/muic/max77705/max77705-muic.c", "%s water hiccup mode, Aux USB path\\n"),
     description_en="The charging controller detected liquid in the charging port and limited or stopped charging.",
     description_ar="اكتشف متحكم الشحن وجود سائل في مدخل الشحن، فقلّل الشحن أو أوقفه.",
     suspect_components=["Liquid in the charging port (dries out)", "Corrosion left after liquid damage", "Dirt or lint pressed into the port", "Damaged port or its flex"],
     suspect_components_ar=["سائل في مدخل الشحن (يجف مع الوقت)", "أكسدة بقيت بعد دخول سائل", "أوساخ أو وبر مضغوط داخل المدخل", "مدخل تالف أو فلاتته"],
     action_en="Possible causes, most common first: liquid that has not dried, corrosion, dirt in the port, a damaged port. Let the phone dry, clean the port, then charge again; if the message returns on a dry, clean port, replace the port or its flex.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: سائل لم يجف، أكسدة، أوساخ في المدخل، مدخل تالف. اترك الجهاز يجف ونظّف المدخل ثم اشحن من جديد؛ إذا عادت الرسالة والمدخل جاف ونظيف فاستبدل المدخل أو فلاتته.")

rule(id="VBUS_OVERCURRENT", category="CHARGING_PORT", severity="HIGH", board_areas=["charging", "power"],
     pattern=r"over-current detected on VBUS",
     kernel_source=src(QC, "drivers/power/supply/qcom/smb-lib.c", "over-current detected on VBUS\\n"),
     description_en="The charger IC measured too much current on VBUS while the phone was powering an accessory (OTG).",
     description_ar="قاس متحكم الشحن تياراً زائداً على خط VBUS بينما كان الجهاز يغذّي ملحقاً (OTG).",
     suspect_components=["The OTG accessory or cable draws too much or is shorted", "Short on the charging port", "Charging port flex", "Charger IC"],
     suspect_components_ar=["ملحق أو كابل OTG يسحب تياراً زائداً أو فيه شورت", "شورت في مدخل الشحن", "فلاتة الشحن", "شريحة الشحن"],
     action_en="Possible causes, most common first: the accessory or OTG cable, a short in the port, the port flex, the charger IC. Unplug the accessory and test another one; if it repeats with nothing plugged in, check the port for a short.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: الملحق أو كابل OTG، شورت في المدخل، فلاتة الشحن، شريحة الشحن. افصل الملحق وجرّب ملحقاً آخر؛ إذا تكرر بدون أي شيء موصول فافحص المدخل بحثاً عن شورت.")

rule(id="OTG_BOOST_FAULT", category="CHARGING_PORT", severity="MEDIUM", board_areas=["charging", "power"],
     pattern=r"OTG soft start failed|OTG failed to enable after \d+ attempts|otg fail\(|en otg fail\(",
     kernel_source=src(QC, "drivers/power/supply/qcom/smb-lib.c", "OTG soft start failed\\n", "OTG failed to enable after %d attempts\\n")
       + src(MT, "drivers/power/supply/rt9471.c", "%s en otg fail(%d)\\n") + src(MT, "drivers/power/supply/rt9467.c", "%s: otg fail(%d)\\n"),
     description_en="The phone could not start powering a USB accessory (OTG boost did not start).",
     description_ar="لم يستطع الجهاز تشغيل التغذية لملحق USB (لم تبدأ دارة OTG).",
     suspect_components=["The accessory draws too much at start", "Short on the charging port", "Charger IC (it makes the OTG power)"],
     suspect_components_ar=["الملحق يسحب تياراً كبيراً عند التشغيل", "شورت في مدخل الشحن", "شريحة الشحن (هي التي تولّد تغذية OTG)"],
     action_en="Possible causes, most common first: the accessory, a short in the port, the charger IC. Test with a small, known-good accessory (a USB stick).",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: الملحق، شورت في المدخل، شريحة الشحن. جرّب بملحق صغير سليم (فلاشة USB).")

rule(id="CHARGER_TYPE_DETECT_FAIL", category="CHARGER_IC", severity="MEDIUM", board_areas=["charging", "peripherals"],
     pattern=r"en bc12 fail|handle bc12 fail|bc12 dis/en fail|read chg type fail|disable chg type detect fail",
     kernel_source=src(MT, "drivers/power/supply/mt6370_pmu_charger.c", "%s: en bc12 fail(%d)\\n", "%s: read chg type fail\\n")
       + src(MT, "drivers/power/supply/rt9471.c", "%s bc12 dis/en fail(%d)\\n") + src(MT, "drivers/power/supply/mt6362-chg.c", "%s: handle bc12 fail, en = %d\\n")
       + src(MT, "drivers/power/supply/mt6360_pmu_chg.c", "%s: disable chg type detect fail\\n"),
     description_en="The phone could not ask the charger IC what kind of charger is plugged in (the IC did not answer on its bus).",
     description_ar="لم يستطع الجهاز سؤال شريحة الشحن عن نوع الشاحن الموصول (الشريحة لم ترد على خط الاتصال).",
     suspect_components=["Charger IC or its I2C lines", "Charging port data lines (D+ / D-)", "Software that does not match the board"],
     suspect_components_ar=["شريحة الشحن أو خطوط I2C الخاصة بها", "خطوط البيانات في مدخل الشحن (D+ / D-)", "سوفتوير غير مطابق للبورد"],
     action_en="Possible causes, most common first: the charger IC or its I2C lines, the port's data lines, mismatched software. If charging is slow or shows the wrong charger type, check the charger IC area.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: شريحة الشحن أو خطوط I2C، خطوط البيانات في المدخل، سوفتوير غير مطابق. إذا كان الشحن بطيئاً أو يظهر نوع شاحن خاطئ فافحص منطقة شريحة الشحن.")

# ---------------------------------------------------------------- battery
rule(id="BATTERY_ABSENT_SHUTDOWN", category="BATTERY", severity="HIGH", board_areas=["power"],
     pattern=r"battery doesn't exist, shutdown|Battery is not exist, power off|fg battery absent",
     kernel_source=src(MT, "drivers/power/supply/mtk_charger.c", "battery doesn't exist, shutdown\\n")
       + src(MT, "drivers/power/supply/mediatek/battery_common_fg_20.c", "[BATTERY] Battery is not exist, power off FAN5405 and system (%d)\\n")
       + src(MT, "drivers/misc/mediatek/typec/tcpc/tcpci_core.c", "%s: fg battery absent\\n"),
     description_en="The fuel gauge did not see a battery and the phone shut down.",
     description_ar="لم يرَ مقياس البطارية أي بطارية، فأُطفئ الجهاز.",
     suspect_components=["Battery connector not seated or dirty", "Battery ID / BSI line (connector to board)", "Battery itself (internal protection open)", "Fuel gauge circuit"],
     suspect_components_ar=["موصل البطارية غير مثبت أو متسخ", "خط تعريف البطارية BSI / ID (من الموصل إلى البورد)", "البطارية نفسها (دارة الحماية الداخلية مفصولة)", "دارة مقياس البطارية"],
     action_en="Possible causes, most common first: the battery connector, the battery ID line, the battery's own protection, the fuel gauge. Reseat and clean the connector, try a known-good battery, then measure the ID line.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: موصل البطارية، خط تعريف البطارية، حماية البطارية الداخلية، مقياس البطارية. أعد تركيب الموصل ونظّفه، جرّب بطارية سليمة، ثم قِس خط التعريف.")

rule(id="BATTERY_NTC_FAULT", category="BATTERY", severity="HIGH", board_areas=["power", "thermal"],
     pattern=r"Battery over Temperature or NTC fail|bat_temp\(-?\d+\) out of range",
     kernel_source=src(MT, "drivers/power/supply/mtk_charger.c", "Battery over Temperature or NTC fail %d %d\\n", "[BATTERY] bat_temp(%d) out of range(too high)\\n", "bat_temp(%d) out of range(too low)\\n"),
     description_en="The battery temperature reading is outside the allowed range, so charging was stopped. The reading comes from the battery's NTC sensor through the connector.",
     description_ar="قراءة حرارة البطارية خارج المدى المسموح، فأُوقف الشحن. القراءة تأتي من حساس NTC في البطارية عبر الموصل.",
     suspect_components=["NTC line or battery connector (a reading like -20 or 80 degrees at room temperature)", "Non-original battery with a different NTC", "Battery really too hot or cold", "Fuel gauge / PMIC ADC"],
     suspect_components_ar=["خط NTC أو موصل البطارية (قراءة مثل -20 أو 80 درجة في حرارة الغرفة)", "بطارية غير أصلية بحساس NTC مختلف", "البطارية فعلاً ساخنة أو باردة جداً", "مقياس البطارية / محوّل ADC في PMIC"],
     action_en="Possible causes, most common first: the NTC line or connector, a non-original battery, a real temperature problem. Compare the logged temperature with the room: an impossible value points to the NTC line or the battery, not to heat.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: خط NTC أو الموصل، بطارية غير أصلية، مشكلة حرارة حقيقية. قارن الحرارة المسجلة بحرارة الغرفة: القيمة المستحيلة تشير إلى خط NTC أو البطارية وليس إلى السخونة.")

rule(id="BATTERY_JEITA_HOT", category="BATTERY", severity="MEDIUM", board_areas=["thermal", "power"],
     pattern=r"\[SW_JEITA\] Battery Over high Temperature|\[BATTERY\] Battery Over (high )?Temperature",
     kernel_source=src(MT, "drivers/power/supply/mtk_charger.c", "[SW_JEITA] Battery Over high Temperature(%d) !!\\n")
       + src(MT, "drivers/power/supply/mediatek/battery_common_fg_20.c", "[BATTERY] Battery Over Temperature !!\\n\\r"),
     description_en="Charging stopped because the battery temperature reached its upper charging limit.",
     description_ar="توقف الشحن لأن حرارة البطارية وصلت حد الشحن الأعلى.",
     suspect_components=["Hot environment or heavy use while charging", "Battery or board heating (a short pulls current)", "NTC line reading wrong"],
     suspect_components_ar=["مكان حار أو استعمال ثقيل أثناء الشحن", "سخونة في البطارية أو البورد (شورت يسحب تياراً)", "خط NTC يقرأ قيمة خاطئة"],
     action_en="Possible causes, most common first: the environment or use, a heating board or battery, a wrong NTC reading. Let it cool and charge idle; if it repeats cold, look for a hot spot on the board or check the NTC line.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: الحرارة المحيطة أو الاستعمال، بورد أو بطارية تسخن، قراءة NTC خاطئة. اتركه يبرد واشحنه بدون استعمال؛ إذا تكرر وهو بارد فابحث عن نقطة ساخنة في البورد أو افحص خط NTC.")

rule(id="BATTERY_LOW_VOLTAGE_SHUTDOWN", category="BATTERY", severity="HIGH", board_areas=["power"],
     pattern=r"battery voltage is low shutdown|soc zero shutdown",
     kernel_source=src(MT, "drivers/power/supply/mtk_power_misc.c", "normal tmp, battery voltage is low shutdown\\n", "cold tmp, battery voltage is low shutdown\\n", "soc zero shutdown\\n"),
     description_en="The phone shut itself down because the battery voltage fell to the shutdown level.",
     description_ar="أطفأ الجهاز نفسه لأن جهد البطارية نزل إلى مستوى الإطفاء.",
     suspect_components=["Worn battery (voltage drops under load)", "Battery connector with poor contact", "A board fault that pulls high current", "Battery simply empty"],
     suspect_components_ar=["بطارية مستهلكة (الجهد ينهار تحت الحمل)", "موصل بطارية تلامسه ضعيف", "عطل في البورد يسحب تياراً عالياً", "البطارية فارغة فعلاً"],
     action_en="Possible causes, most common first: a worn battery, the connector, a board fault drawing current. If it shuts down with charge left, test with a known-good battery and measure the current draw.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: بطارية مستهلكة، الموصل، عطل في البورد يسحب تياراً. إذا انطفأ وفيه شحن فجرّب بطارية سليمة وقِس سحب التيار.")

rule(id="BATTERY_GAUGE_LOW_SOH", category="BATTERY", severity="MEDIUM", board_areas=["power"],
     pattern=r"SOH update: SOH=(?:[0-6]?\d)\b",
     kernel_source=src(QC, "drivers/power/supply/qcom/qpnp-qg.c", "SOH update: SOH=%d esr_actual=%d esr_nominal=%d\\n"),
     description_en="The Qualcomm fuel gauge estimated the battery's state of health below 70% (from its internal resistance).",
     description_ar="قدّر مقياس البطارية من كوالكوم صحة البطارية بأقل من 70% (من مقاومتها الداخلية).",
     suspect_components=["Worn battery", "Non-original battery the gauge was not tuned for"],
     suspect_components_ar=["بطارية مستهلكة", "بطارية غير أصلية لم يُضبط المقياس عليها"],
     action_en="Possible causes, most common first: a worn battery, a non-original battery. Compare with the capacity check; replace the battery if both agree.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: بطارية مستهلكة، بطارية غير أصلية. قارن مع فحص السعة؛ استبدل البطارية إذا اتفق الفحصان.")

rule(id="BATTERY_ID_READ_FAIL", category="BATTERY", severity="MEDIUM", board_areas=["power"],
     pattern=r"Failed to read BATT_ID over ADC",
     kernel_source=src(QC, "drivers/power/supply/qcom/qpnp-qg.c", "Failed to read BATT_ID over ADC, rc=%d\\n"),
     description_en="The fuel gauge could not read the battery ID resistor, so it cannot tell which battery is fitted.",
     description_ar="لم يستطع مقياس البطارية قراءة مقاومة تعريف البطارية، فلا يعرف أي بطارية مركّبة.",
     suspect_components=["Battery ID line or battery connector", "Non-original battery without the ID resistor", "PMIC ADC"],
     suspect_components_ar=["خط تعريف البطارية أو موصلها", "بطارية غير أصلية بدون مقاومة التعريف", "محوّل ADC في PMIC"],
     action_en="Possible causes, most common first: the ID line or connector, a non-original battery, the PMIC. Reseat the connector and try a known-good original battery.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: خط التعريف أو الموصل، بطارية غير أصلية، PMIC. أعد تركيب الموصل وجرّب بطارية أصلية سليمة.")

rule(id="BATTERY_CHARGE_TIMEOUT", category="BATTERY", severity="LOW", board_areas=["power", "charging"],
     pattern=r"\[BATTERY\] Charging Over Time|BAD Battery status\.\.\. Charging Stop",
     kernel_source=src(MT, "drivers/power/supply/mediatek/battery_common_fg_20.c", "[BATTERY] Charging Over Time.\\n")
       + src(MT, "drivers/power/supply/mediatek/linear_charging.c", "[BATTERY] BAD Battery status... Charging Stop !!\\n\\r"),
     description_en="Charging was stopped by the charging safety timer or a bad-battery status.",
     description_ar="أوقف مؤقت الأمان الشحن، أو أوقفه وضع \"بطارية سيئة\".",
     suspect_components=["Worn battery that never reaches full", "Weak charger or cable", "Phone in heavy use while charging"],
     suspect_components_ar=["بطارية مستهلكة لا تصل للامتلاء", "شاحن أو كابل ضعيف", "استعمال ثقيل أثناء الشحن"],
     action_en="Possible causes, most common first: a worn battery, a weak charger or cable, heavy use. Charge idle with an original charger; if it repeats, check the battery.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: بطارية مستهلكة، شاحن أو كابل ضعيف، استعمال ثقيل. اشحن بدون استعمال وبشاحن أصلي؛ إذا تكرر فافحص البطارية.")

# ---------------------------------------------------------------- fingerprint and touch
rule(id="FINGERPRINT_NOT_RESPONDING", category="FINGERPRINT", severity="HIGH", board_areas=["peripherals"],
     pattern=r"fpc hardware reset failed|fpc1020_probe failed as fpsensor|fpc \S+, detected sensor, error=-",
     kernel_source=src(QC, "drivers/input/fingerprint/fpc_tee/fpc1020_platform_tee.c", "fpc hardware reset failed\\n")
       + src(QC, "drivers/input/fingerprint/fpc_tee/fpc1020_tee.c", "Macle fpc1020_probe failed as fpsensor=%d(1=fp)\\n")
       + src(MT, "drivers/input/fingerprint/fpc/fpc1022_tee.c", "fpc %s, detected sensor, error=%d\\n"),
     description_en="The fingerprint sensor did not answer the driver (its chip ID could not be read or its reset failed). The sensor is not reached at all.",
     description_ar="حساس البصمة لم يرد على التعريف (لم يُقرأ رقم الشريحة أو فشلت إعادة تشغيلها). الحساس غير متصل أصلاً.",
     suspect_components=["Fingerprint flex or its connector", "Fingerprint sensor itself", "Replaced sensor of another maker or model", "Sensor power or reset line on the board"],
     suspect_components_ar=["فلاتة البصمة أو موصلها", "حساس البصمة نفسه", "حساس مُستبدل من صانع أو موديل آخر", "خط تغذية أو إعادة تشغيل الحساس على البورد"],
     action_en="Possible causes, most common first: the flex or connector, the sensor, a replaced incompatible sensor, the board lines. Reseat the flex; then try a known-good original sensor: if it answers, the old sensor was the fault, if not, check the board lines.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: الفلاتة أو الموصل، الحساس، حساس مُستبدل غير متوافق، خطوط البورد. أعد تركيب الفلاتة؛ ثم جرّب حساساً أصلياً سليماً: إذا رد فالعطل كان في الحساس القديم، وإذا لم يرد فافحص خطوط البورد.")

rule(id="TOUCH_CONTROLLER_NOT_RESPONDING", category="TOUCH", severity="HIGH", board_areas=["display", "peripherals"],
     pattern=r"goodix_hw_ops->reset fail|Controller reset failed\.|silead.*Chip (?:ID read|reset) error",
     kernel_source=src(QC, "drivers/input/touchscreen/goodix_driver_gt9886/goodix_ts_core.c", "goodix_hw_ops->reset fail \\n")
       + src(MT, "drivers/input/touchscreen/silead.c", "Chip ID read error %d\\n", "Chip reset error %d\\n")
       + src(MT, "drivers/input/touchscreen/goodix.c", "Controller reset failed.\\n"),
     description_en="The touch controller did not come back after a reset: the touch screen cannot work.",
     description_ar="متحكم اللمس لم يعد للعمل بعد إعادة التشغيل: اللمس لا يمكن أن يعمل.",
     suspect_components=["Screen flex or its connector", "Replaced screen with a different touch controller", "Touch controller on the screen", "Touch power line on the board"],
     suspect_components_ar=["فلاتة الشاشة أو موصلها", "شاشة مُستبدلة بمتحكم لمس مختلف", "متحكم اللمس في الشاشة", "خط تغذية اللمس على البورد"],
     action_en="Possible causes, most common first: the screen flex or connector, a replaced screen with another controller, the controller, the board line. Reseat the screen connector and try a known-good screen.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: فلاتة الشاشة أو موصلها، شاشة مُستبدلة بمتحكم آخر، المتحكم، خط البورد. أعد تركيب موصل الشاشة وجرّب شاشة سليمة.")

# ---------------------------------------------------------------- display
rule(id="DISPLAY_BIAS_BACKLIGHT_IC_FAIL", category="DISPLAY", severity="HIGH", board_areas=["display", "power"],
     pattern=r"\[LCM\]\[ERROR\] _lcm_i2c write data fail|tps6132-*cmd=\w+--i2c write error|\[ocp2138\] I2C write fail|\[sgm37604a\] I2C write fail",
     kernel_source=src(MT, "drivers/misc/mediatek/lcm/mt65xx_lcm_list.c".replace("mt65xx_lcm_list.c", "lcm_i2c.c"), "[LCM][ERROR] _lcm_i2c write data fail !!\\n")
       + src(MT, "drivers/misc/mediatek/lcm/ocp2138.c", "[ocp2138] I2C write fail!!!\\n")
       + src(MT, "drivers/misc/mediatek/lcm/sgm37604a.c", "[sgm37604a] I2C write fail!!!\\n"),
     description_en="The phone could not set the screen's bias voltage or backlight chip: it did not answer on its I2C bus.",
     description_ar="لم يستطع الجهاز ضبط شريحة جهد الشاشة أو الإضاءة الخلفية: الشريحة لم ترد على خط I2C.",
     suspect_components=["Display bias / backlight IC on the board", "Its I2C lines", "Screen connector (on some boards the IC sits behind it)"],
     suspect_components_ar=["شريحة جهد الشاشة أو الإضاءة الخلفية على البورد", "خطوط I2C الخاصة بها", "موصل الشاشة (في بعض البوردات تكون الشريحة بعده)"],
     action_en="Possible causes, most common first: the bias or backlight IC, its I2C lines, the screen connector. A screen that stays dark with sound and vibration working points here; measure the IC's output.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: شريحة الجهد أو الإضاءة، خطوط I2C، موصل الشاشة. الشاشة التي تبقى سوداء بينما الصوت والهزاز يعملان تشير إلى هنا؛ قِس خرج الشريحة.")

rule(id="DSI_PANEL_NOT_RESPONDING", category="DISPLAY", severity="HIGH", board_areas=["display"],
     pattern=r"DSI display (?:prepare|enable|post enabled) failed|failed to reset panel|(?:prepare|enable) panel \d+ failed|DSI PLL(?:\(\d+\))? lock failed",
     kernel_source=src(QC, "techpack/display/msm/dsi/dsi_drm.c".replace("techpack/display/msm/dsi/", "drivers/gpu/drm/msm/dsi-staging/"), "[%d] DSI display prepare failed, rc=%d\\n", "[%d] DSI display enable failed, rc=%d\\n")
       + src(QC, "drivers/gpu/drm/msm/dsi-staging/dsi_panel.c", "[%s] failed to reset panel, rc=%d\\n")
       + src(MT, "drivers/gpu/drm/msm/dsi/dsi_manager.c", "%s: prepare panel %d failed, %d\\n", "%s: enable panel %d failed, %d\\n")
       + src(MT, "drivers/gpu/drm/msm/dsi/pll/dsi_pll_10nm.c", "DSI PLL(%d) lock failed, status=0x%08x\\n"),
     description_en="The display driver could not start the panel over its DSI link.",
     description_ar="لم يستطع تعريف العرض تشغيل لوحة الشاشة عبر خط DSI.",
     suspect_components=["Screen flex or its connector", "Replaced screen that does not match the phone's display driver", "Damaged panel", "DSI lines on the board"],
     suspect_components_ar=["فلاتة الشاشة أو موصلها", "شاشة مُستبدلة لا تطابق تعريف الشاشة في الجهاز", "لوحة شاشة تالفة", "خطوط DSI على البورد"],
     action_en="Possible causes, most common first: the screen flex or connector, an incompatible replacement screen, a damaged panel, the board lines. Reseat the connector and try a known-good original screen.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: فلاتة الشاشة أو موصلها، شاشة بديلة غير متوافقة، لوحة تالفة، خطوط البورد. أعد تركيب الموصل وجرّب شاشة أصلية سليمة.")

rule(id="DSI_BACKLIGHT_CMD_FAIL", category="DISPLAY", severity="MEDIUM", board_areas=["display"],
     pattern=r"failed to update dcs backlight",
     kernel_source=src(QC, "drivers/gpu/drm/msm/dsi-staging/dsi_panel.c", "failed to update dcs backlight:%d\\n"),
     description_en="The panel did not accept the brightness command (OLED screens set brightness through the panel itself).",
     description_ar="لوحة الشاشة لم تقبل أمر السطوع (شاشات OLED تضبط السطوع من اللوحة نفسها).",
     suspect_components=["Screen flex or connector", "Replaced (often non-original) screen", "Damaged panel"],
     suspect_components_ar=["فلاتة الشاشة أو الموصل", "شاشة مُستبدلة (غالباً غير أصلية)", "لوحة تالفة"],
     action_en="Possible causes, most common first: the flex or connector, a replaced screen, a damaged panel. If brightness does not change, reseat the screen and compare with an original screen.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: الفلاتة أو الموصل، شاشة مُستبدلة، لوحة تالفة. إذا كان السطوع لا يتغير فأعد تركيب الشاشة وقارن بشاشة أصلية.")

# ---------------------------------------------------------------- storage
rule(id="MSDC_CRC_TIMEOUT", category="STORAGE", severity="HIGH", board_areas=["storage", "power"],
     pattern=r"XXX (?:CMD|DATA) (?:CRC|TIMEOUT)|force remove bad card, data timeout continuous",
     kernel_source=src(MT, "drivers/mmc/host/mtk-sd.c", "XXX CMD CRC", "XXX CMD TIMEOUT", "XXX DATA CRC", "XXX DATA TIMEOUT", "force remove bad card, data timeout continuous %d"),
     description_en="The MediaTek storage controller saw CRC errors or timeouts on an eMMC or SD card bus. The log line names msdc0 (internal eMMC) or msdc1 (SD card slot).",
     description_ar="رأى متحكم التخزين في ميديا تيك أخطاء CRC أو انتهاء مهلة على خط eMMC أو كرت SD. سطر السجل يذكر msdc0 (الذاكرة الداخلية eMMC) أو msdc1 (منفذ كرت SD).",
     suspect_components=["Worn or failing eMMC (msdc0)", "eMMC solder joints (after a drop or heat)", "SD card or its slot (msdc1)", "Storage power rail"],
     suspect_components_ar=["ذاكرة eMMC مستهلكة أو تتعطل (msdc0)", "لحام الـ eMMC (بعد سقوط أو حرارة)", "كرت SD أو منفذه (msdc1)", "خط تغذية الذاكرة"],
     action_en="Possible causes, most common first: worn eMMC, its solder joints, the SD card (msdc1), the power rail. Remove the SD card and repeat; errors that stay on msdc0 point to the eMMC: back up the data first.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: eMMC مستهلكة، لحامها، كرت SD (msdc1)، خط التغذية. أخرج كرت SD وأعد الفحص؛ الأخطاء التي تبقى على msdc0 تشير إلى الـ eMMC: انسخ البيانات أولاً.")

rule(id="MMC_REQUEST_RETRY", category="STORAGE", severity="MEDIUM", board_areas=["storage"],
     pattern=r"req failed \(CMD\d+\): -?\d+, retrying|Card stuck in \d+ state|running CQE recovery|\[CQ\] reinit fail|retune \d+ times",
     kernel_source=src(MT, "drivers/mmc/core/core.c", "%s: req failed (CMD%u): %d, retrying...\\n", "%s: Card stuck in %d state! %s\\n", "%s: running CQE recovery\\n", "[CQ] reinit fail\\n")
       + src(MT, "drivers/mmc/host/mtk-sd.c", "mmc%d: %s retune %d times\\nSDeMMC"),
     description_en="Storage commands had to be retried or the storage queue had to be recovered.",
     description_ar="اضطر النظام لإعادة أوامر التخزين أو إصلاح طابور الذاكرة.",
     suspect_components=["Worn eMMC", "eMMC solder joints", "SD card (when the line names mmc1)"],
     suspect_components_ar=["ذاكرة eMMC مستهلكة", "لحام الـ eMMC", "كرت SD (إذا ذكر السطر mmc1)"],
     action_en="Possible causes, most common first: worn eMMC, its solder joints, the SD card. A few lines can be normal; many lines, or lines with slow or frozen use, point to the storage: run the storage check and back up.",
     action_ar="أسباب محتملة، الأكثر شيوعاً أولاً: eMMC مستهلكة، لحامها، كرت SD. أسطر قليلة قد تكون طبيعية؛ أسطر كثيرة أو مع بطء أو تعليق تشير إلى الذاكرة: شغّل فحص التخزين وانسخ البيانات.")
