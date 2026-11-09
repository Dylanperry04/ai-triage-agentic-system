"""Deserialize exactly the artifact bytes verified against the configured pin."""
from io import BytesIO
from pathlib import Path
import hashlib
import hmac
import re
import joblib


def load_verified_model(path: Path, expected_sha256: str):
    if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
        raise ValueError("A trusted SHA-256 model pin is required")
    payload = Path(path).read_bytes()
    if not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), expected_sha256.lower()):
        raise ValueError("Model SHA-256 does not match the trusted release")
    return joblib.load(BytesIO(payload))
