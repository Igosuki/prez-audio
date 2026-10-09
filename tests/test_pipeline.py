import json
import time

import pytest
from click.testing import CliRunner

from prezaudio import montage, pages, voice
from prezaudio.cli import cli, run_verify
from prezaudio.guards import GuardError
from prezaudio.media import probe, probe_duration
from prezaudio.tts.elevenlabs import ElevenLabsPermissionError
from prezaudio.tts.fake import FakeBackend

TEXTS = {1: "# Intro\n\nHello and welcome to the demo.", 2: "Second slide has a few more words in it.",
         3: "# Wrap up\n\nThanks for watching."}


def write_discours(project, texts=TEXTS):
    project.discours_dir.mkdir(exist_ok=True)
    for n, t in texts.items():
        project.discours_path(n).write_text(t)


def prepared(project):
    write_discours(project)
    project.set_validated({n: project.text_fp(n) for n in TEXTS})
    voice.synth(project, [1, 2, 3])


def test_synth_requires_validation(project):
    write_discours(project)
    with pytest.raises(GuardError):
        voice.synth(project, [1])
    assert not project.audio_dir.exists()


def test_synth_skips_fresh_and_force(project):
    prepared(project)
    assert voice.synth(project, [1, 2, 3]) == []
    assert voice.synth(project, [2], force=True) == [2]
    meta = project.audio_meta(1)
    assert meta["audio_fp"] == project.audio_fp(1) and meta["alignment"]


def test_silent_slide(project):
    write_discours(project, {1: "# Only title\n", 2: "x y", 3: "z"})
    project.set_validated({n: project.text_fp(n) for n in (1, 2, 3)})
    voice.synth(project, [1, 2, 3])
    assert project.audio_meta(1)["duration"] == 0
    assert not project.audio_path(1).exists() and project.audio_fresh(1)
    res = montage.render(project)
    assert probe_duration(project.clip_path(1)) == pytest.approx(0.1 + 0.1 + 2, abs=0.15)
    assert res.output.exists()


def test_render_refuses_stale_audio(project):
    prepared(project)
    project.discours_path(2).write_text("Edited text.")
    with pytest.raises(GuardError, match="slide 2"):
        montage.render(project)


def test_render_outputs(project):
    prepared(project)
    res = montage.render(project)
    assert res.built == [1, 2, 3]
    clip_dur = probe_duration(project.clip_path(1))
    audio = project.audio_meta(1)["duration"]
    assert clip_dur == pytest.approx(0.1 + audio + 0.1, abs=0.1)
    info = probe(res.output)
    kinds = [s["codec_type"] for s in info["streams"]]
    assert "video" in kinds and "audio" in kinds and "subtitle" in kinds
    assert [c["tags"]["title"] for c in info["chapters"]] == ["Intro", "Title 2", "Wrap up"]
    total = sum(project.clip_meta(n)["duration"] for n in (1, 2, 3))
    assert float(info["format"]["duration"]) == pytest.approx(total, abs=0.15)
    out = project.out_dir
    assert (out / "demo-deck.srt").read_text().startswith("1\n")
    assert (out / "demo-deck.vtt").read_text().startswith("WEBVTT")
    first_cue = res.timeline.cues[0]
    assert first_cue.start == pytest.approx(0.1, abs=0.01)
    third = [c for c in res.timeline.cues if c.slide == 3][0]
    assert third.start == pytest.approx(
        sum(project.clip_meta(n)["duration"] for n in (1, 2)) + 0.1, abs=0.01)


def test_burn_subs(project):
    prepared(project)
    res = montage.render(project, burn=True)
    kinds = [s["codec_type"] for s in probe(res.output)["streams"]]
    assert "subtitle" not in kinds and "video" in kinds


def test_pages(project):
    write_discours(project, {1: "<script>alert(1)</script> & more", 2: "b", 3: "c"})
    html = pages.review_page(project)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    for n in ("01", "02", "03"):
        assert f'id="slide-{n}"' in html
    assert "data:image/jpeg;base64," in html
    assert len(pages.thumbnail_jpeg(project.image_path(1))) > 100


def test_player_page_escaping(project):
    page = pages.player_page(project, project.out_dir / "v.mp4",
                             [{"n": 1, "title": "</script><b>", "start": 0, "end": 1}],
                             [{"start": 0, "end": 1, "text": "</script>x", "slide": 1}], project.out_dir)
    assert "</script><b>" not in page and 'src="v.mp4"' in page
    for s in ("1.25", "10", "chapters", "transcript"):
        assert s in page
    assert "http://" not in page and "https://" not in page


def test_audition_and_choose(project):
    write_discours(project)
    variants = voice.audition(project, 1)
    assert [(v["stability"], v["style"]) for v in variants] == [(0.5, 0.0), (0.35, 0.3), (0.3, 0.5), (0.2, 0.7)]
    for k in range(1, 5):
        assert (project.audition_dir / f"variant-{k}.mp3").exists()
    html = (project.audition_dir / "audition.html").read_text()
    assert html.count("<audio") == 4 and "stability=0.35" in html
    assert len(voice.audition(project, 1, 6)) == 6
    voice.audition(project, 1)
    voice.choose(project, 3)
    project.reload()
    assert project.voice["stability"] == 0.3 and project.voice["style"] == 0.5
    prov = json.loads((project.state_dir / "voice.json").read_text())
    assert prov["chosen_variant"] == 3 and prov["audition_slide"] == 1
    assert "[voice]" in project.config_path.read_text()


class FakeSTT:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio, lang=""):
        return self.text


class DeniedSTT:
    def transcribe(self, audio, lang=""):
        raise ElevenLabsPermissionError("HTTP 401")


def test_verify(project):
    prepared(project)
    rows = run_verify(project, [1], 0.15, FakeSTT("hello and welcome to the demo"))
    assert rows[0].status == "OK"
    rows = run_verify(project, [1], 0.15, FakeSTT("completely different"))
    assert rows[0].status == "FAIL"
    rows = run_verify(project, [1], 0.15, DeniedSTT())
    assert rows[0].status == "SKIPPED" and "401" in rows[0].reason


def test_cli_basics(project, invoke):
    assert invoke("status").exit_code == 0
    r = invoke("status", "--json")
    assert [row["n"] for row in json.loads(r.output)] == [1, 2, 3]
    assert invoke("discours", "init").exit_code == 0
    assert project.discours_path(2).read_text() == "# Title 2\n\n"
    r = invoke("discours", "text", "1")
    assert "[01]" in r.output
    assert invoke("voice", "set", "stability=0.3", "voice_id=abc").exit_code == 0
    assert "stability = 0.3" in invoke("voice", "show").output
    assert invoke("voice", "set", "bogus=1").exit_code == 2


def test_cli_errors_are_clean(project, invoke, tmp_path):
    r = invoke("synth", "1")
    assert r.exit_code == 2 and "error:" in r.output and "Traceback" not in r.output
    r = CliRunner().invoke(cli, ["-C", str(tmp_path), "status"])
    assert r.exit_code == 2 and "prez.toml" in r.output
    assert invoke("validate", "9").exit_code == 2
    assert invoke("player").exit_code == 2


def test_new_writes_gitignore(project):
    assert (project.root / ".gitignore").read_text().split() == ["audition/", "audio/", "build/", "out/"]
    assert (project.root / "sources").is_dir()
    r = CliRunner().invoke(cli, ["new", str(project.root), "--deck", "y"])
    assert r.exit_code == 2


def mtimes(project):
    return ({n: project.audio_path(n).stat().st_mtime_ns for n in (1, 2, 3)},
            {n: project.clip_path(n).stat().st_mtime_ns for n in (1, 2, 3)})


def test_end_to_end(project, invoke):
    write_discours(project)
    assert invoke("synth").exit_code == 2
    assert invoke("validate", "all").exit_code == 0
    r = invoke("synth")
    assert r.exit_code == 0, r.output
    r = invoke("render")
    assert r.exit_code == 0, r.output
    assert "1, 2, 3" in r.output
    r = invoke("player")
    assert r.exit_code == 0, r.output
    player = (project.out_dir / "player.html").read_text()
    assert "demo-deck.mp4" in player and "Wrap up" in player
    assert invoke("review-page").exit_code == 0
    audio0, clip0 = mtimes(project)

    time.sleep(0.05)
    project.discours_path(2).write_text("Second slide, now rewritten with different words.")
    r = invoke("synth")
    assert r.exit_code == 2 and "slide 2" in r.output
    r = invoke("render")
    assert r.exit_code == 2 and "stale" in r.output
    assert invoke("validate", "2").exit_code == 0
    r = invoke("synth")
    assert "synthesized 1 slide(s): 2" in r.output
    r = invoke("render")
    assert "clips rebuilt: 2" in r.output
    audio1, clip1 = mtimes(project)
    assert audio1[1] == audio0[1] and audio1[3] == audio0[3] and audio1[2] != audio0[2]
    assert clip1[1] == clip0[1] and clip1[3] == clip0[3] and clip1[2] != clip0[2]
    r = invoke("render")
    assert "clips rebuilt: -" in r.output
    st = json.loads(invoke("status", "--json").output)
    assert all(row["validated"] and row["audio_fresh"] and row["clip_fresh"] for row in st)


def test_cli_verify_skipped_without_key(project, invoke, monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setitem(__import__("sys").modules, "keyring", None)
    prepared(project)
    r = invoke("verify", "1")
    assert r.exit_code == 3 and "SKIPPED" in r.output


def test_cli_burn_flag(project, invoke):
    prepared(project)
    assert invoke("render", "--burn-subs").exit_code == 0
    kinds = [s["codec_type"] for s in probe(project.out_dir / "demo-deck.mp4")["streams"]]
    assert "subtitle" not in kinds
    assert invoke("render", "--force").exit_code == 0
    kinds = [s["codec_type"] for s in probe(project.out_dir / "demo-deck.mp4")["streams"]]
    assert "subtitle" in kinds


def test_new_refuses_non_empty_dir(tmp_path):
    d = tmp_path / "busy"
    d.mkdir()
    (d / "x.txt").write_text("x")
    assert CliRunner().invoke(cli, ["new", str(d), "--deck", "y"]).exit_code == 2
    assert CliRunner().invoke(cli, ["new", str(d), "--deck", "y", "--force"]).exit_code == 0


def test_freeze_resolves_relative_source_and_maps_errors(project, invoke, monkeypatch):
    import prezaudio.deck as deck
    seen = {}

    def fake_freeze(source, out_dir, **kw):
        seen.update(source=source, **kw)
        if kw["exclude"] == [99]:
            raise ValueError("exclude out of range")
        return [deck.SlideInfo(1, 1, "T", "slide-01.png", "abc")]

    monkeypatch.setattr(deck, "freeze", fake_freeze)
    project.set_value("deck", "source", "deck/index.html")
    project.save()
    r = invoke("freeze")
    assert r.exit_code == 0, r.output
    assert seen["source"] == str(project.root / "deck/index.html") and seen["deck_dir"] == project.root
    assert json.loads(project.manifest_path.read_text())[0]["sha256"] == "abc"
    r = invoke("freeze", "--exclude", "99")
    assert r.exit_code == 2 and "out of range" in r.output


def test_progress_lines_and_stale_status(project, invoke):
    write_discours(project)
    invoke("validate", "all")
    out = invoke("synth").output
    assert "slide 1: synthesized" in out and "slide 3: synthesized" in out
    out = invoke("render").output
    assert "slide 2: clip built" in out
    assert "slide 2: clip fresh, skipped" in invoke("render").output
    project.discours_path(2).write_text("changed")
    rows = [line for line in invoke("status").output.splitlines() if "STALE" in line]
    assert len(rows) == 1 and rows[0].split()[0] == "2"


def test_force_resynth_changes_audio_and_restales_clip(project):
    prepared(project)
    montage.render(project)
    assert project.clip_fresh(1)

    class Other:
        def synthesize(self, text, settings, **kw):
            r = FakeBackend().synthesize(text + " extra extra", settings)
            r.alignment = r.alignment[:-2]
            return r

    voice.synth(project, [1], force=True, backend=Other())
    assert project.audio_fresh(1) and not project.clip_fresh(1)
    assert project.clip_fresh(2)
    assert montage.render(project).built == [1]


def test_audio_tampered_on_disk_is_stale(project):
    prepared(project)
    project.audio_path(1).write_bytes(b"garbage")
    assert not project.audio_fresh(1)
    with pytest.raises(GuardError):
        montage.render(project)


def test_meta_fingerprints_follow_text_sent_to_api(project):
    write_discours(project)
    project.set_validated({n: project.text_fp(n) for n in TEXTS})

    class Racing:
        def synthesize(self, text, settings, **kw):
            project.discours_path(1).write_text("Edited while the API call was running.")
            return FakeBackend().synthesize(text, settings)

    voice.synth(project, [1], backend=Racing())
    meta = project.audio_meta(1)
    assert meta["text_fp"] != project.text_fp(1)
    assert not project.audio_fresh(1)


def test_synth_rechecks_validation_against_sent_text(project):
    write_discours(project)
    project.set_validated({n: project.text_fp(n) for n in TEXTS})
    project.discours_path(1).write_text("changed after validation")
    with pytest.raises(GuardError):
        voice.synth(project, [1], backend=FakeBackend())


def test_atomic_writes_leave_no_temp_files(project):
    prepared(project)
    assert not list(project.audio_dir.glob(".*tmp")) and not list(project.state_dir.glob(".*tmp"))


def test_corrupt_state_file_is_clean_error(project, invoke):
    project.state_dir.mkdir()
    (project.state_dir / "validated.json").write_text("{not json")
    r = invoke("status")
    assert r.exit_code == 2 and "corrupt state file" in r.output and "Traceback" not in r.output


def test_voice_set_keeps_ids_as_strings(project, invoke):
    assert invoke("voice", "set", "voice_id=123456", "stability=0.4", "use_speaker_boost=false").exit_code == 0
    project.reload()
    assert project.voice["voice_id"] == "123456" and project.voice["stability"] == 0.4
    assert project.voice["use_speaker_boost"] is False


def test_verify_fail_shows_transcript_and_diff(project, invoke, monkeypatch):
    prepared(project)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    import httpx
    import respx
    with respx.mock:
        respx.post("https://api.elevenlabs.io/v1/speech-to-text").mock(
            return_value=httpx.Response(200, json={"text": "Hello and welcome to the show."}))
        r = invoke("verify", "1", "--threshold", "0.05")
    assert r.exit_code == 1
    assert "heard: Hello and welcome to the show." in r.output
    assert "expected 'demo' / heard 'show'" in r.output


def test_burn_with_awkward_slug(project):
    project.doc["slug"] = "it's a, [slug]"
    project.save()
    prepared(project)
    res = montage.render(project, burn=True)
    assert res.output.exists()


def test_freeze_maps_subprocess_and_browser_errors(project, invoke, monkeypatch):
    import subprocess
    import prezaudio.deck as deck
    from playwright.sync_api import Error

    def boom(*a, **k):
        raise subprocess.CalledProcessError(1, ["pdftoppm"], stderr="bad pdf")

    monkeypatch.setattr(deck, "freeze", boom)
    r = invoke("freeze")
    assert r.exit_code == 2 and "pdftoppm failed: bad pdf" in r.output

    def nobrowser(*a, **k):
        raise Error("BrowserType.launch: Executable doesn't exist at /x")

    monkeypatch.setattr(deck, "freeze", nobrowser)
    r = invoke("freeze")
    assert r.exit_code == 2 and "playwright install chromium" in r.output and "Traceback" not in r.output


def test_key_set_keyring_failure_is_clean(invoke, monkeypatch):
    import keyring

    def fail(*a):
        raise keyring.errors.NoKeyringError("none")

    monkeypatch.setattr(keyring, "set_password", fail)
    r = invoke("key", "set", "--value", "abc")
    assert r.exit_code == 2 and "ELEVENLABS_API_KEY" in r.output and "Traceback" not in r.output
