import hashlib
import json
from pathlib import Path


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return _sha(data)


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def text_fp(text: str) -> str:
    return _sha(text.encode())


def audio_fp(text_fingerprint: str, voice: dict) -> str:
    return _sha((text_fingerprint + canonical(voice)).encode())


def clip_fp(audio_fingerprint: str, image_sha256: str, render: dict) -> str:
    return _sha((audio_fingerprint + image_sha256 + canonical(render)).encode())


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
