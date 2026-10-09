import os
import sys
import tempfile
from pathlib import Path

import httpx

from prezaudio import discours as disc
from prezaudio.media import ffmpeg, probe_duration
from prezaudio.project import PrezError
from prezaudio.tts.base import TTSResult, VoiceSettings, WordTiming

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
# no checksums are published; sizes of the v1.0 release assets guard against truncated downloads
FILES = {"kokoro-v1.0.int8.onnx": 92_361_271, "voices-v1.0.bin": 28_214_398}
MODEL, VOICES = "kokoro-v1.0.int8.onnx", "voices-v1.0.bin"
SENTENCE_PAUSE = 0.25
DEFAULT_VOICES = {"fr": "ff_siwis", "en": "af_heart"}
LANG_CODES = {"fr": "fr-fr", "en": "en-us"}
INSTALL_HINT = ("the kokoro backend needs the optional dependency: run `uv sync --extra kokoro` "
                "or `uv tool install 'prez-audio[kokoro]'`")


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "prez-audio" / "kokoro"


def ensure_model_files(directory: Path | None = None) -> tuple[Path, Path]:
    directory = directory or cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    for name, size in FILES.items():
        target = directory / name
        if target.exists() and target.stat().st_size == size:
            continue
        _download(f"{RELEASE}/{name}", target, size)
    return directory / MODEL, directory / VOICES


def _download(url: str, target: Path, size: int) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    print(f"downloading {target.name} ({size // 1_000_000} MB) to {target.parent}", file=sys.stderr)
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as r:
            r.raise_for_status()
            with tmp.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
        if tmp.stat().st_size != size:
            raise PrezError(f"download of {target.name} is incomplete "
                            f"({tmp.stat().st_size} of {size} bytes); retry")
        os.replace(tmp, target)
    except httpx.HTTPError as e:
        raise PrezError(f"cannot download {url}: {e}") from None
    finally:
        tmp.unlink(missing_ok=True)


def kokoro_lang(lang: str) -> str:
    lang = lang.lower().replace("_", "-")
    return LANG_CODES.get(lang, lang) if lang else "en-us"


def default_voice(lang: str) -> str:
    return DEFAULT_VOICES.get(lang.lower().replace("_", "-").split("-")[0], DEFAULT_VOICES["en"])


def _load_engine():
    try:
        from kokoro_onnx import Kokoro
    except ImportError:
        raise PrezError(INSTALL_HINT) from None
    model, voices = ensure_model_files()
    return Kokoro(str(model), str(voices))


class KokoroBackend:
    def __init__(self, engine=None):
        self._engine = engine

    @property
    def engine(self):
        if self._engine is None:
            self._engine = _load_engine()
        return self._engine

    def synthesize(self, text: str, settings: VoiceSettings, *, previous_text: str | None = None,
                   next_text: str | None = None, lang: str = "") -> TTSResult:
        import numpy as np
        voice = settings.voice_id or default_voice(lang)
        code = kokoro_lang(lang)
        chunks, words, cursor, rate = [], [], 0.0, None
        for k, sentence in enumerate(disc.sentences(text)):
            samples, rate = self.engine.create(sentence, voice=voice, speed=settings.speed, lang=code)
            if k:
                chunks.append(np.zeros(int(SENTENCE_PAUSE * rate), dtype=np.float32))
                cursor += SENTENCE_PAUSE
            length = len(samples) / rate
            words += _spread(sentence, cursor, length)
            chunks.append(np.asarray(samples, dtype=np.float32))
            cursor += length
        if rate is None:
            raise PrezError("nothing to synthesize")
        audio = np.concatenate(chunks)
        with tempfile.TemporaryDirectory() as d:
            raw, out = Path(d) / "a.f32", Path(d) / "a.mp3"
            raw.write_bytes(audio.tobytes())
            ffmpeg("-f", "f32le", "-ar", rate, "-ac", 1, "-i", raw, "-c:a", "libmp3lame",
                   "-q:a", 4, out)
            return TTSResult(out.read_bytes(), words, probe_duration(out))


def _spread(sentence: str, start: float, length: float) -> list[WordTiming]:
    words = sentence.split()
    weights = [len(w) + 1 for w in words]
    total = sum(weights)
    out, t = [], start
    for w, weight in zip(words, weights):
        end = t + length * weight / total
        out.append(WordTiming(w, round(t, 3), round(end, 3)))
        t = end
    return out
