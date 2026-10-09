from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class VoiceSettings:
    backend: str
    voice_id: str
    model_id: str
    stability: float
    similarity_boost: float
    style: float
    speed: float
    use_speaker_boost: bool


@dataclass
class WordTiming:
    word: str
    start: float
    end: float


@dataclass
class TTSResult:
    audio_bytes: bytes
    alignment: list[WordTiming] = field(default_factory=list)
    duration: float = 0.0


class TTSBackend(Protocol):
    def synthesize(self, text: str, settings: VoiceSettings, *, previous_text: str | None = None,
                   next_text: str | None = None, lang: str = "") -> TTSResult: ...
