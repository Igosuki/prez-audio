import dataclasses
from pathlib import Path

import httpx
import numpy as np
import pytest
import respx

from prezaudio import voice
from prezaudio.media import probe_duration_bytes
from prezaudio.project import PrezError
from prezaudio.tts import get_backend
from prezaudio.tts import kokoro as k
from prezaudio.tts.base import VoiceSettings

S = VoiceSettings("kokoro", "", "m", 0.5, 0.75, 0.0, 1.0, True)


class StubEngine:
    rate = 24000

    def __init__(self):
        self.calls = []

    def create(self, text, voice, speed, lang):
        self.calls.append((text, voice, speed, lang))
        return np.full(int(self.rate * 0.5), 0.01, dtype=np.float32), self.rate


def test_synthesize_sentence_by_sentence():
    eng = StubEngine()
    r = k.KokoroBackend(eng).synthesize("Bonjour tout le monde. Au revoir.", S, lang="fr")
    assert [c[0] for c in eng.calls] == ["Bonjour tout le monde.", "Au revoir."]
    assert eng.calls[0][1:] == ("ff_siwis", 1.0, "fr-fr")
    assert r.duration == pytest.approx(0.5 + 0.25 + 0.5, abs=0.1)
    second = [w for w in r.alignment if w.word in ("Au", "revoir.")]
    assert second[0].start == pytest.approx(0.75, abs=0.01)
    assert r.alignment[3].end == pytest.approx(0.5, abs=0.01)
    assert r.alignment[-1].end == pytest.approx(1.25, abs=0.01)
    assert probe_duration_bytes(r.audio_bytes) == pytest.approx(r.duration, abs=0.1)


def test_voice_and_speed_honored():
    eng = StubEngine()
    s = dataclasses.replace(S, voice_id="am_adam", speed=1.2)
    k.KokoroBackend(eng).synthesize("Hello.", s, lang="en")
    assert eng.calls[0][1:] == ("am_adam", 1.2, "en-us")
    assert k.default_voice("en-GB") == "af_heart" and k.kokoro_lang("fr_FR") == "fr-fr"


def test_missing_package_gives_install_hint(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "kokoro_onnx", None)
    with pytest.raises(PrezError, match="uv sync --extra kokoro"):
        k._load_engine()


def test_get_backend_kokoro():
    assert isinstance(get_backend("kokoro"), k.KokoroBackend)


@respx.mock
def test_download_is_atomic_and_size_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(k, "FILES", {"a.bin": 5, "b.bin": 3})
    respx.get(f"{k.RELEASE}/a.bin").mock(return_value=httpx.Response(200, content=b"12345"))
    respx.get(f"{k.RELEASE}/b.bin").mock(return_value=httpx.Response(200, content=b"12"))
    with pytest.raises(PrezError, match="incomplete"):
        k.ensure_model_files(tmp_path)
    assert (tmp_path / "a.bin").read_bytes() == b"12345"
    assert not (tmp_path / "b.bin").exists() and not list(tmp_path.glob(".*tmp"))


def test_cache_dir_respects_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert k.cache_dir() == tmp_path / "prez-audio" / "kokoro"


def test_audition_varies_speed_and_choose_writes_speed(project):
    project.set_value("voice", "backend", "kokoro")
    project.save()
    project.discours_dir.mkdir()
    project.discours_path(1).write_text("Bonjour tout le monde.")
    vs = voice.audition(project, 1, backend=k.KokoroBackend(StubEngine()))
    assert [v["speed"] for v in vs] == [0.9, 1.0, 1.1, 1.2]
    assert len({v["stability"] for v in vs}) == 1
    voice.choose(project, 4)
    project.reload()
    assert project.voice["speed"] == 1.2 and project.voice["stability"] == 0.5


REAL = k.cache_dir()


@pytest.mark.skipif(not all((REAL / n).exists() for n in k.FILES), reason="kokoro model not cached")
def test_real_kokoro_french():
    pytest.importorskip("kokoro_onnx")
    r = k.KokoroBackend().synthesize("Bonjour. Ceci est un test.", S, lang="fr")
    assert r.duration > 1 and len(r.alignment) == 5
