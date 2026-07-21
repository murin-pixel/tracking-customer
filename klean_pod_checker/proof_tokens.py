from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from urllib.parse import urlparse


LOCAL_KEX_PROOF_RE = re.compile(
    r"^/proof/kex/(ANB[A-Z0-9]{6,})/(proof-\d+-[0-9a-f]{10}\.(?:jpg|png|webp))$",
    re.IGNORECASE,
)
SKYFROG_HOSTS = {"skyfrog.net", "www.skyfrog.net"}


def make_sheet_proof_token(proof_url: str, secret: str) -> str:
    """Return a tamper-proof bearer token for a known proof image source."""
    source = _validate_source(proof_url)
    payload = base64.urlsafe_b64encode(
        json.dumps({"source": source}, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def read_sheet_proof_token(token: str, secret: str) -> str:
    """Validate a token and return its approved local or Skyfrog source URL."""
    try:
        payload, signature = token.rsplit(".", 1)
    except ValueError as exc:
        raise ValueError("invalid proof token") from exc
    expected = hmac.new(
        secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid proof token")
    try:
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        source = json.loads(decoded.decode("utf-8")).get("source", "")
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid proof token") from exc
    return _validate_source(str(source))


def local_kex_proof_parts(source: str) -> tuple[str, str] | None:
    match = LOCAL_KEX_PROOF_RE.fullmatch(source)
    if not match:
        return None
    return match.group(1).upper(), match.group(2)


def _validate_source(value: str) -> str:
    if local_kex_proof_parts(value):
        return value
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.hostname in SKYFROG_HOSTS:
        return value
    raise ValueError("unapproved proof source")
