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


def write_certificate(cert: WipeCertificate, out_dir: str, signing_key=None) -> Dict[str, Any]:
    """Write the certificate JSON and, when a signing key is given, a detached Ed25519
    signature and a verifiable QR summary. Returns the paths and the QR text."""
    import json
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, cert.cert_id + ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(cert.to_payload(), f, indent=2, ensure_ascii=False)

    result: Dict[str, Any] = {"json_path": json_path, "sig_path": None, "qr_text": None, "signed": False}
    if signing_key is not None:
        from core.report_signing import sign_file, make_signed_summary
        result["sig_path"] = sign_file(json_path, signing_key)
        result["qr_text"] = make_signed_summary(signing_key, {
            "ref": cert.cert_id,
            "serial": cert.device.get("serial", ""),
            "model": f"{cert.device.get('brand', '')} {cert.device.get('model', '')}".strip(),
            "status": cert.verdict,
            "date": cert.timestamp,
        })
        result["signed"] = True
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
