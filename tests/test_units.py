import pytest

from prezaudio import discours as disc
from prezaudio import fingerprint as fp
from prezaudio.guards import GuardError, require_fresh_audio, require_validated
from prezaudio.project import PrezError, parse_selector
from prezaudio.subtitles import build_cues, to_srt, to_vtt
from prezaudio.tts.base import WordTiming
from prezaudio.verify import wer


def test_tts_text_normalization():
    raw = "# Intro\n\n<!-- note -->Hello **big** _world_, see [docs](http://x).\n- item  one\n"
    assert disc.tts_text(raw) == "Hello big world, see docs. item one"
    assert disc.split_title(raw)[0] == "Intro"
    assert disc.split_title("no heading")[0] is None
    assert disc.tts_text("snake_case stays") == "snake_case stays"
    assert disc.tts_text("# T\n<!-- only -->") == ""


def test_title_change_does_not_change_text_fp():
    assert fp.text_fp(disc.tts_text("# A\n\nsame")) == fp.text_fp(disc.tts_text("# B\n\nsame"))


def test_fingerprints_depend_on_inputs():
    t = fp.text_fp("x")
    a = fp.audio_fp(t, {"stability": 0.5})
    assert a == fp.audio_fp(t, {"stability": 0.5})
    assert a != fp.audio_fp(t, {"stability": 0.4})
    assert fp.clip_fp(a, "img1", {"crf": 20}) != fp.clip_fp(a, "img2", {"crf": 20})
    assert fp.clip_fp(a, "img1", {"crf": 20}) != fp.clip_fp(a, "img1", {"crf": 21})


def test_selector_parsing():
    avail = list(range(1, 9))
    assert parse_selector("all", avail) == avail
    assert parse_selector("3", avail) == [3]
    assert parse_selector("3,5", avail) == [3, 5]
    assert parse_selector("3-5,2", avail) == [2, 3, 4, 5]
    for bad in ("x", "9", "5-3"):
        with pytest.raises(PrezError):
            parse_selector(bad, avail)


def test_guards(project):
    project.discours_dir.mkdir()
    project.discours_path(1).write_text("# T\n\nHello there.")
    with pytest.raises(GuardError, match="not validated"):
        require_validated(project, 1)
    project.set_validated({1: project.text_fp(1)})
    require_validated(project, 1)
    project.discours_path(1).write_text("# T\n\nChanged.")
    with pytest.raises(GuardError):
        require_validated(project, 1)
    with pytest.raises(GuardError, match="stale or missing"):
        require_fresh_audio(project, 1)


def words(text, step=0.5):
    return [WordTiming(w, i * step, (i + 1) * step) for i, w in enumerate(text.split())]


def test_cues_split_on_sentences_and_limits():
    cues = build_cues(words("One two. Three four five."), offset=10.0, slide=2)
    assert [c.text for c in cues] == ["One two.", "Three four five."]
    assert cues[0].start == 10.0 and cues[1].start == 11.0 and cues[0].slide == 2
    long = build_cues(words(" ".join(["word"] * 40)), 0)
    assert len(long) > 1
    for c in long:
        assert len(c.text.split("\n")) <= 2
        assert all(len(line) <= 42 for line in c.text.split("\n"))
        assert c.end - c.start <= 6.0
    slow = build_cues(words("a b c d e f g h i j k l", step=1.0), 0)
    assert all(c.end - c.start <= 6.0 for c in slow) and len(slow) >= 2


def test_srt_vtt_format():
    cues = build_cues(words("Hello world."), offset=3661.5)
    srt, vtt = to_srt(cues), to_vtt(cues)
    assert srt.startswith("1\n01:01:01,500 --> 01:01:02,500\nHello world.\n\n")
    assert vtt.startswith("WEBVTT\n\n01:01:01.500 --> 01:01:02.500\nHello world.")


def test_wer():
    assert wer("Hello, world!", "hello world") == 0.0
    assert wer("a b c d", "a x c d") == 0.25
    assert wer("a b", "") == 1.0
    assert wer("", "") == 0.0
