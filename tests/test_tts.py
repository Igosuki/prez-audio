import base64

import httpx
import pytest
import respx

from prezaudio.media import probe_duration_bytes
from prezaudio.project import PrezError
from prezaudio.tts.base import VoiceSettings
from prezaudio.tts.elevenlabs import (ElevenLabsClient, ElevenLabsError,
                                      ElevenLabsPermissionError, alignment_to_words, get_api_key)
from prezaudio.tts.fake import FakeBackend

S = VoiceSettings("elevenlabs", "vid", "eleven_multilingual_v2", 0.5, 0.75, 0.0, 1.0, True)


def test_alignment_to_words():
    al = {"characters": list("Hi  you."), "character_start_times_seconds": [0, .1, .2, .3, .4, .5, .6, .7],
          "character_end_times_seconds": [.1, .2, .3, .4, .5, .6, .7, .8]}
    w = alignment_to_words(al)
    assert [(x.word, x.start, x.end) for x in w] == [("Hi", 0, .2), ("you.", .4, .8)]


def test_fake_backend():
    r = FakeBackend().synthesize("one two three four", S)
    assert len(r.alignment) == 4
    assert r.duration == pytest.approx(1.4, abs=0.1)
    assert r.alignment[-1].end == pytest.approx(1.4, abs=0.01)
    assert probe_duration_bytes(r.audio_bytes) == pytest.approx(r.duration, abs=0.1)


@respx.mock
def test_elevenlabs_synthesize():
    audio = FakeBackend().synthesize("hello", S).audio_bytes
    route = respx.post("https://api.elevenlabs.io/v1/text-to-speech/vid/with-timestamps").mock(
        return_value=httpx.Response(200, json={
            "audio_base64": base64.b64encode(audio).decode(),
            "alignment": {"characters": list("hi yo"),
                          "character_start_times_seconds": [0, .1, .2, .3, .4],
                          "character_end_times_seconds": [.1, .2, .3, .4, .5]}}))
    r = ElevenLabsClient("KEY").synthesize("hi yo", S, previous_text="before", next_text="after", lang="fr")
    req = route.calls[0].request
    assert req.headers["xi-api-key"] == "KEY"
    import json
    body = json.loads(req.content)
    assert body["text"] == "hi yo" and body["previous_text"] == "before" and body["next_text"] == "after"
    assert body["voice_settings"] == {"stability": 0.5, "similarity_boost": 0.75, "style": 0.0,
                                      "use_speaker_boost": True, "speed": 1.0}
    assert "language_code" not in body
    assert [w.word for w in r.alignment] == ["hi", "yo"]
    assert r.audio_bytes == audio and r.duration > 0


@respx.mock
def test_language_code_sent_for_other_models():
    import dataclasses
    import json
    route = respx.post("https://api.elevenlabs.io/v1/text-to-speech/vid/with-timestamps").mock(
        return_value=httpx.Response(200, json={
            "audio_base64": base64.b64encode(FakeBackend().synthesize("a", S).audio_bytes).decode(),
            "alignment": {"characters": ["a"], "character_start_times_seconds": [0],
                          "character_end_times_seconds": [0.1]}}))
    ElevenLabsClient("K").synthesize("a", dataclasses.replace(S, model_id="eleven_flash_v2_5"),
                                     lang="fr-FR")
    assert json.loads(route.calls[0].request.content)["language_code"] == "fr"


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
def test_permission_error(status):
    respx.post("https://api.elevenlabs.io/v1/speech-to-text").mock(
        return_value=httpx.Response(status, json={"detail": "nope"}))
    with pytest.raises(ElevenLabsPermissionError):
        ElevenLabsClient("K").transcribe(b"x")


@respx.mock
def test_other_http_error():
    respx.post("https://api.elevenlabs.io/v1/speech-to-text").mock(return_value=httpx.Response(500))
    with pytest.raises(ElevenLabsError) as e:
        ElevenLabsClient("K").transcribe(b"x")
    assert not isinstance(e.value, ElevenLabsPermissionError)


@respx.mock
def test_transcribe():
    route = respx.post("https://api.elevenlabs.io/v1/speech-to-text").mock(
        return_value=httpx.Response(200, json={"text": "hello"}))
    assert ElevenLabsClient("K").transcribe(b"audio", lang="en") == "hello"
    assert b"scribe_v1" in route.calls[0].request.content


def test_api_key_lookup(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "abc")
    assert get_api_key() == "abc"
    monkeypatch.delenv("ELEVENLABS_API_KEY")
    monkeypatch.setitem(__import__("sys").modules, "keyring", None)
    with pytest.raises(PrezError):
        get_api_key()


URL = "https://api.elevenlabs.io/v1/text-to-speech/vid/with-timestamps"


@respx.mock
def test_retries_with_backoff_and_retry_after():
    ok = httpx.Response(200, json={
        "audio_base64": base64.b64encode(FakeBackend().synthesize("a", S).audio_bytes).decode(),
        "alignment": {"characters": ["a"], "character_start_times_seconds": [0],
                      "character_end_times_seconds": [0.1]}})
    route = respx.post(URL).mock(side_effect=[
        httpx.Response(429, headers={"retry-after": "7"}), httpx.Response(503), ok])
    sleeps = []
    ElevenLabsClient("K", sleep=sleeps.append).synthesize("a", S)
    assert route.call_count == 3 and sleeps == [7.0, 2.0]


@respx.mock
def test_retry_gives_up_after_four_attempts():
    route = respx.post(URL).mock(return_value=httpx.Response(503))
    sleeps = []
    with pytest.raises(ElevenLabsError):
        ElevenLabsClient("K", sleep=sleeps.append).synthesize("a", S)
    assert route.call_count == 4 and sleeps == [1.0, 2.0, 4.0]


@respx.mock
@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_no_retry_on_client_errors(status):
    route = respx.post(URL).mock(return_value=httpx.Response(status))
    with pytest.raises(ElevenLabsError):
        ElevenLabsClient("K", sleep=lambda s: pytest.fail("slept")).synthesize("a", S)
    assert route.call_count == 1


@respx.mock
@pytest.mark.parametrize("response", [
    httpx.Response(200, text="<html>oops</html>"),
    httpx.Response(200, json=["x"]),
    httpx.Response(200, json={"alignment": {}}),
    httpx.Response(200, json={"audio_base64": "!!notb64"}),
    httpx.Response(200, json={"audio_base64": "AAAA"}),
    httpx.Response(200, json={"audio_base64": "AAAA", "alignment": {"characters": []}}),
])
def test_malformed_200_is_elevenlabs_error(response):
    respx.post(URL).mock(return_value=response)
    with pytest.raises(ElevenLabsError):
        ElevenLabsClient("K").synthesize("a", S)


@respx.mock
def test_malformed_transcription():
    respx.post("https://api.elevenlabs.io/v1/speech-to-text").mock(
        return_value=httpx.Response(200, json={"words": []}))
    with pytest.raises(ElevenLabsError):
        ElevenLabsClient("K").transcribe(b"x")
