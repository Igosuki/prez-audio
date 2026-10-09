from prezaudio.tts.base import TTSBackend, TTSResult, VoiceSettings, WordTiming

__all__ = ["TTSBackend", "TTSResult", "VoiceSettings", "WordTiming", "get_backend"]


def get_backend(name: str) -> TTSBackend:
    if name == "fake":
        from prezaudio.tts.fake import FakeBackend
        return FakeBackend()
    if name == "elevenlabs":
        from prezaudio.tts.elevenlabs import ElevenLabsClient, get_api_key
        return ElevenLabsClient(get_api_key())
    from prezaudio.project import PrezError
    raise PrezError(f"unknown voice backend {name!r} (use elevenlabs or fake)")
