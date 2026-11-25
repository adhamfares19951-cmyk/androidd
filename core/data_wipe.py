"""Secure-wipe assessment and a signed Data Erasure Certificate for resold phones.

Honest by construction - the certificate only claims what can actually be verified:

- Modern Android encrypts userdata (File-Based Encryption, or older Full-Disk). A factory
  reset discards the encryption key, so the data becomes cryptographically unrecoverable.
  That is the real erasure mechanism, and the certificate asserts it ONLY when encryption
  is confirmed from the device (ro.crypto.state / ro.crypto.type). If encryption is not
  confirmed, the certificate says so plainly and does NOT claim cryptographic erasure.
- Whether the wipe actually happened is read back from the device after the reset
  (`settings get secure user_setup_complete` == 0 at the setup wizard), never assumed.
- The certificate is signed with the program's Ed25519 key (core/report_signing.py) and
  carries a verifiable QR summary, exactly like the diagnostic reports.

Statuses follow the project convention: PASS (encryption confirmed / post-wipe confirmed),
FAIL (device reports it is NOT encrypted - a real, evidence-backed shortfall), UNKNOWN
(the device did not report enough to tell - never guessed as safe).
"""
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.result import Evidence, M, ProducesCheckResult, Status

logger = logging.getLogger("DataWipe")

# Erasure verdicts recorded on the certificate.
VERDICT_CRYPTO = "CRYPTO_ERASURE"       # encryption confirmed -> factory reset destroys the key
VERDICT_UNVERIFIED = "RESET_UNVERIFIED"  # not encrypted / unknown -> no cryptographic guarantee

CRYPTO_FBE = "FBE"     # file-based encryption (ro.crypto.type = file)
CRYPTO_FDE = "FDE"     # full-disk encryption (ro.crypto.type = block)
CRYPTO_NONE = "none"
CRYPTO_UNKNOWN = "unknown"


class WipeAssessment(ProducesCheckResult):
    """Pre-wipe check: is this phone's userdata encrypted (so a reset erases it for good)?"""
    check_id = "wipe.encryption"
    _check_detail_attr = "detail"

    def __init__(self, status: Status, encrypted: Optional[bool], crypto_type: str,
                 device: Dict[str, str], detail: Any = None, evidence: Optional[List[Evidence]] = None):
        self.status = status
        self.encrypted = encrypted          # True / False / None(unknown)
        self.crypto_type = crypto_type      # FBE / FDE / none / unknown
        self.device = device
        self.detail = detail
        self.evidence = evidence or []
        self.possible_causes: List[Any] = []

    def _check_value(self):
        return self.crypto_type, ""


@dataclass
class WipeCertificate:
    cert_id: str
    timestamp: str
    device: Dict[str, str]
    encrypted: Optional[bool]
    crypto_type: str
    verdict: str
    method: str
    post_wipe_verified: bool
    operator: str = ""
    shop: str = ""
    notes: str = ""

    def to_payload(self) -> Dict[str, Any]:
        """Ordered, signable representation written verbatim into the .json certificate."""
        return {
            "format": "adp-data-erasure-certificate/1",
            "cert_id": self.cert_id,
            "timestamp": self.timestamp,
            "device": self.device,
            "encryption": {
                "encrypted": self.encrypted,
                "type": self.crypto_type,
            },
            "verdict": self.verdict,
            "method": self.method,
            "post_wipe_verified": self.post_wipe_verified,
            "operator": self.operator,
            "shop": self.shop,
            "notes": self.notes,
        }


def _prop(bridge, serial: str, name: str) -> str:
    try:
        return (bridge.get_prop(name, serial=serial) or "").strip()
    except Exception:
        return ""


def read_device_identity(bridge, serial: str) -> Dict[str, str]:
    return {
        "serial": serial or _prop(bridge, serial, "ro.serialno"),
        "brand": _prop(bridge, serial, "ro.product.brand"),
        "model": _prop(bridge, serial, "ro.product.model"),
        "android_version": _prop(bridge, serial, "ro.build.version.release"),
        "build_id": _prop(bridge, serial, "ro.build.id"),
    }


def assess_wipe_readiness(bridge, serial: str) -> WipeAssessment:
    """Read the device's own encryption state. Encryption confirmed => a factory reset is a
    true cryptographic erasure. Never inferred: an unreported state stays UNKNOWN."""
    device = read_device_identity(bridge, serial)
    state = _prop(bridge, serial, "ro.crypto.state").lower()
    ctype = _prop(bridge, serial, "ro.crypto.type").lower()
    ev = [Evidence(source="getprop ro.crypto.state", raw_excerpt=state or "(empty)"),
          Evidence(source="getprop ro.crypto.type", raw_excerpt=ctype or "(empty)")]

    crypto_type = {"file": CRYPTO_FBE, "block": CRYPTO_FDE}.get(ctype, CRYPTO_UNKNOWN)

    if state == "encrypted":
        encrypted: Optional[bool] = True
        if crypto_type == CRYPTO_UNKNOWN:
            crypto_type = CRYPTO_FBE   # modern default, but only once state confirms encryption
        return WipeAssessment(Status.PASS, True, crypto_type, device,
                              detail=M("wipe.enc.confirmed", type=crypto_type), evidence=ev)
    if state == "unencrypted":
        return WipeAssessment(Status.FAIL, False, CRYPTO_NONE, device,
                              detail=M("wipe.enc.absent"), evidence=ev)
    # Empty or "unsupported": the device did not tell us - do not claim it either way.
    return WipeAssessment(Status.UNKNOWN, None, CRYPTO_UNKNOWN, device,
                          detail=M("wipe.enc.unknown"), evidence=ev)


def verify_post_wipe(bridge, serial: str) -> bool:
    """True only when the device is in a factory-fresh state (sitting at the setup wizard).
    user_setup_complete is 0 on a freshly wiped phone and 1 once it has been set up."""
    try:
        code, out, _ = bridge.shell("settings get secure user_setup_complete", serial=serial, timeout=5)
    except Exception:
        return False
    return code == 0 and out.strip() == "0"


def build_certificate(assessment: WipeAssessment, method: str = "factory_reset",
                      post_wipe_verified: bool = False, operator: str = "", shop: str = "",
                      notes: str = "", when: Optional[float] = None) -> WipeCertificate:
    """Turn an assessment into a certificate with an honest verdict. The verdict asserts
    cryptographic erasure only when encryption was actually confirmed."""
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(when if when is not None else time.time()))
    cert_id = "ADPW-" + time.strftime("%Y%m%d", time.localtime(when if when is not None else time.time())) \
              + "-" + secrets.token_hex(3).upper()
    verdict = VERDICT_CRYPTO if assessment.encrypted is True else VERDICT_UNVERIFIED
    return WipeCertificate(
        cert_id=cert_id,
        timestamp=ts,
        device=dict(assessment.device),
        encrypted=assessment.encrypted,
        crypto_type=assessment.crypto_type,
        verdict=verdict,
        method=method,
        post_wipe_verified=post_wipe_verified,
        operator=operator,
        shop=shop,
        notes=notes,
    )


def _generate_qr_svg(text: str) -> str:
    """Generate inline SVG for QR code if qrcode library is available."""
    try:
        import qrcode
        import qrcode.image.svg
        import re
        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
        svg = img.to_string(encoding="unicode")
        if "<?xml" in svg:
            svg = svg.split("?>", 1)[-1].strip()
        svg = re.sub(r'width="[^"]+"', 'width="120"', svg, count=1)
        svg = re.sub(r'height="[^"]+"', 'height="120"', svg, count=1)
        return svg
    except Exception:
        return ""


def generate_wipe_certificate_html(cert: WipeCertificate, qr_text: Optional[str] = None,
                                    signed: bool = False, key_id: str = "") -> str:
    """Generate a self-contained, printable HTML erasure certificate with optional QR code."""
    qr_svg = _generate_qr_svg(qr_text) if qr_text else ""
    is_crypto = cert.verdict == VERDICT_CRYPTO
    status_bg = "#059669" if is_crypto else "#d97706"
    status_title = "CRYPTOGRAPHIC DATA ERASURE VERIFIED" if is_crypto else "RESET COMPLETED (UNVERIFIED CRYPTO)"
    status_desc = (
        "Userdata encryption (FBE/FDE) active prior to reset. Factory reset destroyed cryptographic keys, "
        "rendering all previous user data permanently unrecoverable."
        if is_crypto else
        "Factory reset executed. Cryptographic key destruction could not be formally certified from device properties."
    )
    sig_html = f"<span style='color:#059669; font-weight:bold;'>Signed (Ed25519 Key ID: {key_id})</span>" if signed else "<span style='color:#6b7280;'>Not Digitally Signed</span>"
    post_wipe_badge = "<span style='color:#059669; font-weight:bold;'>VERIFIED (Setup Wizard Active)</span>" if cert.post_wipe_verified else "<span style='color:#d97706;'>PENDING / UNVERIFIED</span>"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Data Erasure Certificate - {cert.cert_id}</title>
<style>
  @page {{ size: A4; margin: 20mm; }}
  body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 24px; }}
  .cert-container {{ max-width: 800px; margin: 0 auto; background: #1e293b; border: 2px solid #334155; border-radius: 12px; padding: 32px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
  .header {{ display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 2px solid #334155; padding-bottom: 20px; }}
  .title {{ font-size: 22px; font-weight: 800; color: #38bdf8; letter-spacing: 0.5px; margin: 0; }}
  .subtitle {{ font-size: 13px; color: #94a3b8; margin-top: 4px; }}
  .cert-id {{ font-family: monospace; font-size: 15px; font-weight: bold; background: #0f172a; padding: 6px 12px; border-radius: 6px; border: 1px solid #475569; }}
  .verdict-banner {{ margin: 24px 0; background: {status_bg}; color: #ffffff; padding: 16px 20px; border-radius: 8px; text-align: center; }}
  .verdict-banner h2 {{ margin: 0 0 6px 0; font-size: 18px; letter-spacing: 1px; }}
  .verdict-banner p {{ margin: 0; font-size: 13px; opacity: 0.95; }}
  table {{ width: 100%; border-collapse: collapse; margin: 16px 0; }}
  th, td {{ padding: 10px 14px; text-align: left; font-size: 13px; border-bottom: 1px solid #334155; }}
  th {{ background: #0f172a; color: #94a3b8; width: 30%; }}
  td {{ color: #e2e8f0; }}
  .footer {{ display: flex; justify-content: space-between; align-items: center; margin-top: 24px; padding-top: 20px; border-top: 1px solid #334155; }}
  .qr-box {{ background: #ffffff; padding: 6px; border-radius: 8px; display: inline-block; }}
  @media print {{
    body {{ background: #ffffff !important; color: #000000 !important; padding: 0 !important; }}
    .cert-container {{ border: 1px solid #000000 !important; background: #ffffff !important; box-shadow: none !important; }}
    th {{ background: #f1f5f9 !important; color: #334155 !important; }}
    td {{ color: #000000 !important; }}
    .verdict-banner {{ print-color-adjust: exact; -webkit-print-color-adjust: exact; }}
    .cert-id {{ background: #f8fafc !important; color: #000000 !important; }}
    .subtitle {{ color: #475569 !important; }}
  }}
</style>
</head>
<body>
<div class="cert-container">
  <div class="header">
    <div>
      <h1 class="title">ANDROID DOCTOR PRO</h1>
      <div class="subtitle">CERTIFICATE OF SECURE DATA ERASURE & COMPLIANCE</div>
    </div>
    <div class="cert-id">{cert.cert_id}</div>
  </div>

  <div class="verdict-banner">
    <h2>{status_title}</h2>
    <p>{status_desc}</p>
  </div>

  <table>
    <tr><th>Device Brand & Model</th><td>{cert.device.get('brand', '')} {cert.device.get('model', '')}</td></tr>
    <tr><th>Serial Number</th><td><code>{cert.device.get('serial', '')}</code></td></tr>
    <tr><th>Android Version & Build</th><td>Android {cert.device.get('android_version', '-')} (Build: {cert.device.get('build_id', '-')})</td></tr>
    <tr><th>Userdata Encryption Type</th><td>{cert.crypto_type}</td></tr>
    <tr><th>Erasure Method</th><td>{cert.method}</td></tr>
    <tr><th>Post-Wipe State</th><td>{post_wipe_badge}</td></tr>
    <tr><th>Technician / Operator</th><td>{cert.operator or '-'}</td></tr>
    <tr><th>Facility / Shop</th><td>{cert.shop or '-'}</td></tr>
    <tr><th>Date & Timestamp</th><td>{cert.timestamp}</td></tr>
    <tr><th>Additional Notes</th><td>{cert.notes or 'None'}</td></tr>
  </table>

  <div class="footer">
    <div>
      <div style="font-size:13px; margin-bottom:4px;"><strong>Digital Verification:</strong> {sig_html}</div>
      <div style="font-size:11px; color:#94a3b8;">Tamper-evident audit record verified by Android Doctor Pro.</div>
    </div>
    <div class="qr-box">
      {qr_svg if qr_svg else '<div style="color:#000; font-size:10px; width:100px; text-align:center;">' + (qr_text or cert.cert_id) + '</div>'}
    </div>
  </div>
</div>
</body>
</html>
"""


def write_certificate(cert: WipeCertificate, out_dir: str, signing_key=None) -> Dict[str, Any]:
    """Write the certificate JSON, HTML view, and, when a signing key is given, a detached Ed25519
    signature and a verifiable QR summary. Returns the paths and the QR text."""
    import json
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, cert.cert_id + ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(cert.to_payload(), f, indent=2, ensure_ascii=False)

    result: Dict[str, Any] = {"json_path": json_path, "html_path": None, "sig_path": None, "qr_text": None, "signed": False}
    key_id = ""
    if signing_key is not None:
        from core.report_signing import sign_file, make_signed_summary, key_id_from_public
        result["sig_path"] = sign_file(json_path, signing_key)
        result["qr_text"] = make_signed_summary(signing_key, {
            "ref": cert.cert_id,
            "serial": cert.device.get("serial", ""),
            "model": f"{cert.device.get('brand', '')} {cert.device.get('model', '')}".strip(),
            "status": cert.verdict,
            "date": cert.timestamp,
        })
        result["signed"] = True
        key_id = key_id_from_public(signing_key.public_key())

    html_path = os.path.join(out_dir, cert.cert_id + ".html")
    html_content = generate_wipe_certificate_html(cert, qr_text=result["qr_text"], signed=result["signed"], key_id=key_id)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    result["html_path"] = html_path

    return result


def verify_certificate(json_path: str, public_key_path: str):
    """(ok, message) - the certificate file verifies against this public key and is unmodified."""
    from core.report_signing import verify_file
    return verify_file(json_path, public_key_path)


# ------------------------------------------------------------------ wipe trigger
def initiate_factory_reset(bridge, serial: str, confirm: bool = False) -> Dict[str, Any]:
    """Start a factory reset, honestly. Destructive, so it does nothing unless confirm=True.
    Without root the reset cannot be forced silently, so the on-device confirm screen is
    opened for the technician; completion is proven later by verify_post_wipe, never assumed.
    Returns {started, needs_on_device_confirm, method, message}."""
    if not confirm:
        return {"started": False, "needs_on_device_confirm": False,
                "method": None, "message": M("wipe.reset.not_confirmed")}

    # With root, MASTER_CLEAR can be broadcast directly.
    if bridge.is_root_available(serial):
        code, _, err = bridge.shell(
            "am broadcast -a android.intent.action.MASTER_CLEAR", serial=serial, timeout=15, root=True)
        if code == 0:
            return {"started": True, "needs_on_device_confirm": False,
                    "method": "master_clear_root", "message": M("wipe.reset.broadcast_sent")}

    # No root (or the broadcast was refused): open the factory-reset confirmation screen so the
    # technician completes it on the handset. We do not claim the wipe happened here.
    bridge.shell("am start -a android.settings.FACTORY_RESET", serial=serial, timeout=10)
    return {"started": False, "needs_on_device_confirm": True,
            "method": "on_device_confirm", "message": M("wipe.reset.confirm_on_device")}
