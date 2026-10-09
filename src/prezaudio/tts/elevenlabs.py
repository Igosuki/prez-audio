import base64
import binascii
import os
import time

import httpx

from prezaudio.media import probe_duration_bytes
from prezaudio.project import PrezError
from prezaudio.tts.base import TTSResult, VoiceSettings, WordTiming

BASE_URL = "https://api.elevenlabs.io"
STT_MODEL = "scribe_v1"
KEYRING_SERVICE = "prez-audio"
KEYRING_USER = "elevenlabs"
RETRY_STATUSES = {429, 502, 503, 504}
ATTEMPTS = 4
BACKOFF_BASE = 1.0
MAX_RETRY_AFTER = 60.0


class ElevenLabsError(PrezError):
    pass


class ElevenLabsPermissionError(ElevenLabsError):
    pass


def get_api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if key:
        return key
    try:
        import keyring
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
    except Exception:
        key = None
    if not key:
        raise PrezError("no ElevenLabs API key: set ELEVENLABS_API_KEY or run `prez key set`")
    return key


def primary_lang(lang: str) -> str:
    return lang.replace("_", "-").split("-")[0]


def alignment_to_words(alignment: dict) -> list[WordTiming]:
    chars = alignment["characters"]
    starts = alignment["character_start_times_seconds"]
    ends = alignment["character_end_times_seconds"]
    words: list[WordTiming] = []
    buf, start, end = "", 0.0, 0.0
    for c, s, e in zip(chars, starts, ends):
        if c.isspace():
            if buf:
                words.append(WordTiming(buf, start, end))
                buf = ""
            continue
        if not buf:
            start = s
        buf += c
        end = e
    if buf:
        words.append(WordTiming(buf, start, end))
    return words


class ElevenLabsClient:
    def __init__(self, api_key: str, *, base_url: str = BASE_URL,
                 client: httpx.Client | None = None, timeout: float = 120.0, sleep=time.sleep):
        self.sleep = sleep
        self.base_url = base_url.rstrip("/")
        self.headers = {"xi-api-key": api_key}
        self.client = client or httpx.Client(timeout=timeout)

    def _post(self, path: str, **kw) -> dict:
        for attempt in range(ATTEMPTS):
            try:
                r = self.client.post(f"{self.base_url}{path}", headers=self.headers, **kw)
            except httpx.HTTPError as e:
                raise ElevenLabsError(f"ElevenLabs request failed: {e}") from None
            if r.status_code in RETRY_STATUSES and attempt < ATTEMPTS - 1:
                self.sleep(self._delay(r, attempt))
                continue
            break
        if r.status_code in (401, 403):
            raise ElevenLabsPermissionError(
                f"ElevenLabs refused the request (HTTP {r.status_code}): {r.text[:200]}")
        if r.status_code >= 400:
            raise ElevenLabsError(f"ElevenLabs HTTP {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
        except ValueError:
            raise ElevenLabsError(f"ElevenLabs returned a non-JSON body: {r.text[:200]}") from None
        if not isinstance(data, dict):
            raise ElevenLabsError("ElevenLabs returned an unexpected JSON payload")
        return data

    @staticmethod
    def _delay(r: httpx.Response, attempt: int) -> float:
        try:
            return min(max(float(r.headers["retry-after"]), 0.0), MAX_RETRY_AFTER)
        except (KeyError, ValueError):
            return BACKOFF_BASE * 2 ** attempt

    def synthesize(self, text: str, settings: VoiceSettings, *, previous_text: str | None = None,
                   next_text: str | None = None, lang: str = "") -> TTSResult:
        if not settings.voice_id:
            raise PrezError("voice.voice_id is empty in prez.toml")
        body: dict = {
            "text": text,
            "model_id": settings.model_id,
            "voice_settings": {
                "stability": settings.stability,
                "similarity_boost": settings.similarity_boost,
                "style": settings.style,
                "use_speaker_boost": settings.use_speaker_boost,
                "speed": settings.speed,
            },
        }
        if previous_text:
            body["previous_text"] = previous_text
        if next_text:
            body["next_text"] = next_text
        # the API rejects language_code on eleven_multilingual_v2
        if lang and "multilingual_v2" not in settings.model_id:
            body["language_code"] = primary_lang(lang)
        data = self._post(f"/v1/text-to-speech/{settings.voice_id}/with-timestamps", json=body)
        try:
            audio = base64.b64decode(data["audio_base64"], validate=True)
            words = alignment_to_words(data["alignment"])
        except (KeyError, TypeError, ValueError, binascii.Error):
            raise ElevenLabsError(
                "ElevenLabs response lacks usable audio_base64/alignment") from None
        return TTSResult(audio, words, probe_duration_bytes(audio))

    def transcribe(self, audio: bytes, *, lang: str = "", model_id: str = STT_MODEL) -> str:
        form = {"model_id": model_id}
        if lang:
            form["language_code"] = primary_lang(lang)
        data = self._post("/v1/speech-to-text", data=form,
                          files={"file": ("audio.mp3", audio, "audio/mpeg")})
        if not isinstance(data.get("text"), str):
            raise ElevenLabsError("ElevenLabs transcription response lacks text")
        return data["text"]
