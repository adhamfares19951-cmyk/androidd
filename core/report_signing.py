"""
Ed25519 signing of exported reports, with a key kept outside the program.

- The private key is created by the technician (`python main.py --gen-signing-key DIR`) in a
  folder outside this project, and is never read from inside it: a key shipped with the program
  would let anyone sign. If ADP_SIGNING_KEY_PASSPHRASE is set, the key file is encrypted with it.
- A signature is detached: `<report>.sig` (JSON) next to the report holds the Ed25519 signature
  of the report file's exact bytes, their SHA-256, and the key id (SHA-256 of the raw public key,
  first 16 hex digits). Any byte changed in the report makes verification fail.
- Verification needs only the public key (`--verify-report REPORT --public-key PUB`).

Errors are raised as SigningError carrying a Msg, so the UI/CLI show them in the user's language.
"""

import base64
import hashlib
import json
import os
from typing import Optional, Tuple

from core.result import Msg, M

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIGNATURE_FORMAT = "adp-report-signature/1"
PASSPHRASE_ENV = "ADP_SIGNING_KEY_PASSPHRASE"
KEY_PATH_ENV = "ADP_SIGNING_KEY"
PRIVATE_KEY_NAME = "adp_signing_key.pem"
PUBLIC_KEY_NAME = "adp_signing_key.pub.pem"


class SigningError(Exception):
    def __init__(self, msg: Msg):
        super().__init__(msg.key)
        self.msg = msg


def _crypto():
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ed25519
        from cryptography.exceptions import InvalidSignature
    except ImportError:
        raise SigningError(M("sign.err.no_library"))
    return serialization, ed25519, InvalidSignature


def is_inside_project(path: str, root: str = PROJECT_ROOT) -> bool:
    real = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    base = os.path.normcase(os.path.realpath(root))
    return real == base or real.startswith(base + os.sep)


def _passphrase() -> Optional[bytes]:
    value = os.environ.get(PASSPHRASE_ENV)
    return value.encode("utf-8") if value else None


def key_id_from_public(public_key) -> str:
    serialization, _, _ = _crypto()
    raw = public_key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def generate_keypair(directory: str, project_root: str = PROJECT_ROOT) -> Tuple[str, str, str]:
    """Create a new key pair in `directory` (must be outside the project). Returns (private, public, key_id)."""
    serialization, ed25519, _ = _crypto()
    directory = os.path.normpath(os.path.abspath(directory))
    if is_inside_project(directory, project_root):
        raise SigningError(M("sign.err.key_inside_project", path=directory))
    os.makedirs(directory, exist_ok=True)
    priv_path = os.path.join(directory, PRIVATE_KEY_NAME)
    pub_path = os.path.join(directory, PUBLIC_KEY_NAME)
    if os.path.exists(priv_path) or os.path.exists(pub_path):
        raise SigningError(M("sign.err.key_exists", path=priv_path))

    key = ed25519.Ed25519PrivateKey.generate()
    pw = _passphrase()
    encryption = serialization.BestAvailableEncryption(pw) if pw else serialization.NoEncryption()
    priv_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption)
    pub_pem = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)

    # "x": never overwrite an existing key
    fd = os.open(priv_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(priv_pem)
    with open(pub_path, "xb") as f:
        f.write(pub_pem)
    return priv_path, pub_path, key_id_from_public(key.public_key())


def load_private_key(path: str, project_root: str = PROJECT_ROOT):
    serialization, ed25519, _ = _crypto()
    if is_inside_project(path, project_root):
        raise SigningError(M("sign.err.key_inside_project", path=path))
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        raise SigningError(M("sign.err.key_unreadable", path=path, error=str(e)))
    try:
        key = serialization.load_pem_private_key(data, password=_passphrase())
    except TypeError:
        raise SigningError(M("sign.err.passphrase_needed", env=PASSPHRASE_ENV))
    except ValueError:
        raise SigningError(M("sign.err.key_invalid", path=path))
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise SigningError(M("sign.err.not_ed25519", path=path))
    return key


def load_public_key(path: str):
    serialization, ed25519, _ = _crypto()
    try:
        with open(path, "rb") as f:
            key = serialization.load_pem_public_key(f.read())
    except OSError as e:
        raise SigningError(M("sign.err.key_unreadable", path=path, error=str(e)))
    except ValueError:
        raise SigningError(M("sign.err.key_invalid", path=path))
    if not isinstance(key, ed25519.Ed25519PublicKey):
        raise SigningError(M("sign.err.not_ed25519", path=path))
    return key


def signature_path_for(report_path: str) -> str:
    return report_path + ".sig"


def sign_file(report_path: str, private_key) -> str:
    """Sign the file's bytes as they are on disk; write `<report>.sig`. Returns the signature path."""
    with open(report_path, "rb") as f:
        data = f.read()
    record = {
        "format": SIGNATURE_FORMAT,
        "algorithm": "Ed25519",
        "file": os.path.basename(report_path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "key_id": key_id_from_public(private_key.public_key()),
        "signature": base64.b64encode(private_key.sign(data)).decode("ascii"),
    }
    sig_path = signature_path_for(report_path)
    with open(sig_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
        f.write("\n")
    return sig_path


def verify_file(report_path: str, public_key_path: str, sig_path: Optional[str] = None) -> Tuple[bool, Msg]:
    """(True, msg) only when the signature over the exact file bytes verifies with this public key."""
    _, _, InvalidSignature = _crypto()
    public_key = load_public_key(public_key_path)
    sig_path = sig_path or signature_path_for(report_path)
    try:
        with open(report_path, "rb") as f:
            data = f.read()
        with open(sig_path, encoding="utf-8") as f:
            record = json.load(f)
    except OSError as e:
        raise SigningError(M("sign.err.file_unreadable", error=str(e)))
    except ValueError:
        return False, M("sign.verify.bad_format", path=sig_path)

    if not isinstance(record, dict) or record.get("format") != SIGNATURE_FORMAT or record.get("algorithm") != "Ed25519":
        return False, M("sign.verify.bad_format", path=sig_path)
    key_id = key_id_from_public(public_key)
    if record.get("key_id") != key_id:
        return False, M("sign.verify.other_key", expected=key_id, found=str(record.get("key_id")))
    if record.get("sha256") != hashlib.sha256(data).hexdigest():
        return False, M("sign.verify.modified")
    try:
        public_key.verify(base64.b64decode(record.get("signature", ""), validate=True), data)
    except (InvalidSignature, ValueError):
        return False, M("sign.verify.bad_signature")
    return True, M("sign.verify.ok", key_id=key_id)
