"""
Names of phone parts inside file names and readings: camera sensors (with the module maker when the
name carries it), fingerprint makers and NFC chips.

One set of rules, used twice: by tools/build_parts_db.py on the firmware file lists of each model,
and by core/parts_check.py on what the phone itself reports. A name the rules do not recognise is
skipped, never guessed.
"""
import re

SENSOR = (r"(?:imx\d{3}[a-z0-9]*|s5k[0-9a-z]{2,8}|ov\d{2,3}[a-z0-9]*|ov[0-9]{2}[a-z][0-9]{2}[a-z0-9]*"
          r"|os\d{2}[a-z]\d{2}[a-z0-9]*|gc\d{2,4}[a-z0-9]*|hi\d{3}[a-z]*|hi1\d{3}[a-z]*|sc\d{3}[a-z]{0,3}\d?"
          r"|sl\d{3}|jx[a-z]\d{2})")
# SoC and other chip names that look like sensors: never sensors
NOT_SENSOR_TOKENS = re.compile(r"^(?:sc\d{4}|hi[2-9]\d{3}|ov\d{4}x?_?dvp)$")
SENSOR_RE = re.compile(rf"^{SENSOR}$")
MAKER_ALIASES = {"lsi": "samsung", "semco": "semco", "hynix": "sk hynix", "skhynix": "sk hynix"}
MODULE_MAKERS = {
    "ofilm", "qtech", "sunny", "semco", "truly", "shinetech", "sy", "liteon", "primax", "lce", "cmk",
    "aac", "acc", "hlt", "txd", "seasons", "holitech", "foxconn", "sony", "samsung", "mcnex", "partron",
    "cowell", "luxvisions", "hrt", "ctc", "sharp", "tsp", "skyworth", "lsi", "hynix", "skhynix",
    "kingcome", "darling", "aoe", "bsx", "cxt", "jsl", "hq", "huaquan",
}
NOT_SENSORS = {"qti_tpg0", "qti_tpg1", "qti_tpg2"}
FP_MAKERS = {
    "fpc": "FPC", "goodix": "Goodix", "egis": "Egis", "silead": "Silead", "chipone": "Chipone",
    "focaltech": "FocalTech", "elan": "Elan", "synaptics": "Synaptics", "jiiov": "JIIOV",
    "sunwave": "Sunwave", "betterlife": "Betterlife", "fortsense": "Fortsense",
    "qbt1000": "Qualcomm (ultrasonic)", "qbt2000": "Qualcomm (ultrasonic)", "novatek": "Novatek", "validity": "Synaptics", "gf_": "Goodix",
}
_NFC_PATTERNS = (
    (r"\bpn5\d\d", "NXP PN5xx"), (r"\bsn1\d\d", "NXP SN1xx"), (r"nxp", "NXP"), (r"st21nfc", "ST ST21NFC"),
    (r"st54", "ST ST54"), (r"sec_nfc|sec-nfc|libnfc-sec", "Samsung S3NRN"), (r"nq-nci|nqnfc", "NXP (NQ)"),
)
NFC_CHIPS = []
for _pattern, _chip in _NFC_PATTERNS:
    NFC_CHIPS.append((re.compile(_pattern), _chip))


_GLUED = re.compile(r"^(?P<s>.+?)(?P<m>" + "|".join(sorted(MODULE_MAKERS, key=len, reverse=True)) + r")?(?:mipi(?:raw|mono))?(?:india)?$")


def _normalize(tok: str):
    """'ov13b10ofilmmipiraw' -> ('ov13b10', 'ofilm'); 'imx258tuned' -> ('imx258', ''); 'imx000' -> ('', '')."""
    tok = re.sub(r"(?:tuned|otp|auxvcm|aux|vcm|front|rear|wide)$", "", tok)
    m = _GLUED.match(tok)
    sensor, maker = (m.group("s"), m.group("m") or "") if m else (tok, "")
    if not SENSOR_RE.match(sensor) or re.match(r"^imx0+$", sensor):
        return "", ""
    return sensor, MAKER_ALIASES.get(maker, maker)


def sensors_in(text: str, camera_lines_only: bool = True):
    """[(sensor, module maker or '')] named in `text`, sorted, without duplicates.
    For a firmware file list (camera_lines_only): only camera lines count (camera / chromatix / sensor /
    mipi_raw / setfile in the path), and generic vendor libraries every phone of a platform ships (PDAF
    libraries) are skipped. For a reading that is already about the cameras (a camera sysfs node, the
    MediaTek camera_info list): every line counts."""
    found = set()
    for raw in (text or "").lower().splitlines():
        line = raw.split("|")[0].split(";")[0].strip() if camera_lines_only else raw.strip()
        if not line or line.startswith("#"):
            continue
        if camera_lines_only and not re.search(r"camera|chromatix|sensor|mipi_raw|setfile|tuned|mmcamera|eeprom|actuator", line):
            continue
        if "pdaflibrary" in line or "pdaf_" in line and "mipi_raw" not in line:
            continue
        for path in (line.split(":") if camera_lines_only else re.split(r"[\s:;,=\[\]]+", line)):
            name = path.rsplit("/", 1)[-1]
            sf = re.match(r"setfile_([a-z0-9]+)", name)
            if sf and re.match(r"^\d[a-z0-9]{2}$", sf.group(1)):
                found.add(("s5k" + sf.group(1), "samsung"))   # Samsung LSI setfiles name the sensor without S5K
                continue
            tokens = [t for t in re.split(r"[._\-]", name) if t]
            for i, tok in enumerate(tokens):
                if tok in NOT_SENSORS or NOT_SENSOR_TOKENS.match(tok) or not SENSOR_RE.match(tok):
                    continue
                maker = ""
                for j in (i - 1, i + 1):
                    if 0 <= j < len(tokens) and tokens[j] in MODULE_MAKERS:
                        maker = MAKER_ALIASES.get(tokens[j], tokens[j])
                        break
                tok, glued = _normalize(tok)
                if tok:
                    found.add((tok, maker or glued))
    # a sensor seen with a maker does not need a second, maker-less entry
    with_maker = {s for s, mk in found if mk}
    return sorted((s, mk) for s, mk in found if mk or s not in with_maker)


def fingerprint_makers(text: str):
    out = set()
    section = ""
    for line in text.lower().splitlines():
        if line.startswith("#"):
            section = line
            continue
        in_fp = "fingerprint" in section or "fingerprint" in line or re.search(r"\bfp[_.-]|_fp\b|fpsensor|biometrics", line)
        for token, name in FP_MAKERS.items():
            if token in line and (in_fp or token in ("fpc", "egis", "silead", "qbt1000", "qbt2000", "gf_")):
                if token == "fpc" and "fpc" not in re.findall(r"[a-z]+", line) and "fpc_" not in line and ".fpc" not in line and "fpc." not in line:
                    continue
                out.add(name)
    return sorted(out)


def nfc_chips(text: str):
    out = set()
    for line in text.lower().splitlines():
        if "nfc" not in line:
            continue
        for rx, name in NFC_CHIPS:
            if rx.search(line):
                out.add(name)
    if "NXP" in out and len(out) > 1:
        out.discard("NXP")
    return sorted(out)


def fingerprint_maker_of(value: str):
    """'goodix_fod' -> 'Goodix', 'fpc' -> 'FPC'; None when the value names no known maker."""
    v = (value or "").strip().lower()
    for token, name in FP_MAKERS.items():
        if token in v:
            return name
    return None
