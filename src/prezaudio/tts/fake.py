import tempfile
from pathlib import Path

from prezaudio.media import ffmpeg, probe_duration
from prezaudio.tts.base import TTSResult, VoiceSettings, WordTiming

SECONDS_PER_WORD = 0.35


class FakeBackend:
    def synthesize(self, text: str, settings: VoiceSettings, *, previous_text: str | None = None,
                   next_text: str | None = None, lang: str = "") -> TTSResult:
        words = text.split()
        total = max(len(words) * SECONDS_PER_WORD, 0.3)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "a.mp3"
            ffmpeg("-f", "lavfi", "-i", f"sine=frequency=220:duration={total:.3f}",
                   "-af", "volume=0.05", "-ar", "44100", "-ac", "1", "-c:a", "libmp3lame", out)
            data = out.read_bytes()
            duration = probe_duration(out)
        step = total / max(len(words), 1)
        timings = [WordTiming(w, round(i * step, 3), round((i + 1) * step, 3))
                   for i, w in enumerate(words)]
        return TTSResult(data, timings, duration)
