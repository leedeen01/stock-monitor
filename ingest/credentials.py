"""Read credentials encrypted by the web layer.

Deliberately NOT named secrets.py: that shadows a standard library
module, and because ingest/ is on sys.path the shadow wins for every
import in the package. It broke two unrelated test files before this
was renamed.

Decrypt only. The app encrypts in `web/lib/secrets.ts`; this exists because the
scheduled refresh has no Node process to ask, and an IBKR sync that only works
when someone clicks a button is not a daily job.

**This must stay byte-compatible with web/lib/secrets.ts.** Same scheme, same
salt, same derivation, same wire format:

    v1.<iv hex>.<tag hex>.<ciphertext hex>      AES-256-GCM
    key = scrypt(ENCRYPTION_KEY, salt="stock-monitor.credential.v1", 32 bytes)

Changing either side alone silently breaks every stored token, and the symptom
is a portfolio that stops updating rather than an error anyone sees. There is a
round-trip test covering exactly that.
"""

import hashlib
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = "v1"
SALT = b"stock-monitor.credential.v1"

# Node's crypto.scryptSync defaults: N=16384, r=8, p=1. They have to match.
SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
KEY_BYTES = 32


def _key() -> bytes | None:
    raw = os.environ.get("ENCRYPTION_KEY")
    if not raw or len(raw) < 32:
        return None
    return hashlib.scrypt(
        raw.encode("utf-8"), salt=SALT,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
        dklen=KEY_BYTES, maxmem=64 * 1024 * 1024,
    )


def configured() -> bool:
    return _key() is not None


def decrypt(stored: str) -> str | None:
    """Returns None rather than raising, so a value encrypted under a rotated
    key reads as 'needs relinking' instead of taking the whole run down."""
    key = _key()
    if not key or not stored:
        return None

    parts = stored.split(".")
    if len(parts) != 4 or parts[0] != VERSION:
        return None

    _, iv_hex, tag_hex, body_hex = parts
    try:
        iv = bytes.fromhex(iv_hex)
        # Python expects the tag appended to the ciphertext; Node keeps it
        # separate. Same bytes, different convention.
        payload = bytes.fromhex(body_hex) + bytes.fromhex(tag_hex)
        return AESGCM(key).decrypt(iv, payload, None).decode("utf-8")
    except Exception:  # noqa: BLE001 - any failure means "cannot read this"
        return None


def encrypt(plaintext: str) -> str | None:
    """Only used by the round-trip test. The app encrypts in TypeScript; having
    two writers would be two things to keep in step instead of one."""
    key = _key()
    if not key:
        return None
    iv = os.urandom(12)
    sealed = AESGCM(key).encrypt(iv, plaintext.encode("utf-8"), None)
    body, tag = sealed[:-16], sealed[-16:]
    return ".".join([VERSION, iv.hex(), tag.hex(), body.hex()])
