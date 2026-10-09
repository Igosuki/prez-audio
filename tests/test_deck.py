import logging
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

from prezaudio.deck import NoSlidesError, freeze

FIXTURES = Path(__file__).parent / "fixtures" / "decks"
W, H = 320, 180


def _chromium_available() -> bool:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            pw.chromium.launch().close()
        return True
    except Exception:
        return False


needs_chromium = pytest.mark.skipif(not _chromium_available(), reason="Chromium cannot launch")


def png_size(path: Path) -> tuple[int, int]:
    return struct.unpack(">II", path.read_bytes()[16:24])


def run(source, tmp_path, **kw):
    return freeze(str(source), tmp_path / "out", width=W, height=H, **kw)


@needs_chromium
def test_selector_strategy_shows_hidden_elements(tmp_path):
    slides = run(FIXTURES / "selector.html", tmp_path, selector=".page")
    assert [s.title for s in slides] == ["Alpha", "Beta", "Slide 3"]
    assert len({s.sha256 for s in slides}) == 3
    assert [s.file for s in slides] == ["slide-01.png", "slide-02.png", "slide-03.png"]


@needs_chromium
def test_png_sizes_are_exact(tmp_path):
    slides = run(FIXTURES / "sections.html", tmp_path)
    for s in slides:
        assert png_size(tmp_path / "out" / s.file) == (W, H)


@needs_chromium
def test_reveal_strategy_wins_over_class_slide_and_flattens_stacks(tmp_path):
    slides = run(FIXTURES / "reveal.html", tmp_path)
    assert [s.title for s in slides] == ["One", "Two", "Two B"]
    assert len({s.sha256 for s in slides}) == 3


@needs_chromium
def test_section_slide_with_animation_ends_visible(tmp_path):
    slides = run(FIXTURES / "sections.html", tmp_path)
    assert [s.title for s in slides] == ["First", "Second", "Third", "Slide 4"]
    assert len({s.sha256 for s in slides}) == 4


@needs_chromium
def test_keyboard_fallback_stops_on_identical_screenshot(tmp_path):
    slides = run(FIXTURES / "keyboard.html", tmp_path)
    assert [s.title for s in slides] == ["K1", "K2", "K3"]
    assert len({s.sha256 for s in slides}) == 3


@needs_chromium
def test_static_page_is_a_single_slide(tmp_path):
    slides = run(FIXTURES / "empty.html", tmp_path)
    assert [s.title for s in slides] == ["Just a page"]


@needs_chromium
def test_exclude_uses_source_numbering_and_renumbers(tmp_path):
    full = run(FIXTURES / "sections.html", tmp_path)
    slides = run(FIXTURES / "sections.html", tmp_path, exclude=[2])
    assert [(s.n, s.source_n) for s in slides] == [(1, 1), (2, 3), (3, 4)]
    assert [s.sha256 for s in slides] == [full[0].sha256, full[2].sha256, full[3].sha256]
    assert sorted(p.name for p in (tmp_path / "out").glob("*.png")) == [
        "slide-01.png", "slide-02.png", "slide-03.png"
    ]


@needs_chromium
def test_exclude_out_of_range_and_exclude_all(tmp_path):
    with pytest.raises(ValueError, match="out of range"):
        run(FIXTURES / "sections.html", tmp_path, exclude=[9])
    with pytest.raises(NoSlidesError):
        run(FIXTURES / "sections.html", tmp_path, exclude=[1, 2, 3, 4])


@needs_chromium
def test_file_guard_blocks_requests_outside_deck_dir(tmp_path, caplog):
    (tmp_path / "secret.png").write_bytes(
        bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
            "0000000d49444154789c6360f8ffff3f0005fe02fea75b0b6c0000000049454e44ae426082"
        )
    )
    deck = tmp_path / "deck"
    deck.mkdir()
    (deck / "index.html").write_text(
        '<h1 id="t">PENDING</h1><script>'
        'const i=new Image();i.onload=()=>t.textContent="LEAKED";i.onerror=()=>t.textContent="BLOCKED";'
        'i.src="../secret.png";</script>'
    )
    with caplog.at_level(logging.WARNING, logger="prezaudio.deck"):
        slides = freeze(str(deck / "index.html"), tmp_path / "out", width=W, height=H)
    assert slides[0].title == "BLOCKED"
    assert "secret.png" in caplog.text


@needs_chromium
def test_deck_dir_untouched_for_file_source(tmp_path):
    deck_dir = tmp_path / "project"
    run(FIXTURES / "sections.html", tmp_path, deck_dir=deck_dir)
    assert not deck_dir.exists()


@needs_chromium
def test_url_source_saves_frozen_copy(tmp_path):
    import functools
    import http.server
    import threading

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURES))
    handler.log_message = lambda *a, **k: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/sections.html"
        deck_dir = tmp_path / "project"
        slides = freeze(url, tmp_path / "out", width=W, height=H, deck_dir=deck_dir)
    finally:
        server.shutdown()
    assert len(slides) == 4
    assert (deck_dir / "deck" / "index.html").read_text() == (FIXTURES / "sections.html").read_text()
    assert (deck_dir / "deck" / "SOURCE.txt").read_text().strip() == url


def _make_image(path: Path, size: str, color: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}",
         "-frames:v", "1", str(path)],
        check=True,
    )


def test_image_dir_is_letterboxed_in_natural_order(tmp_path):
    imgs = tmp_path / "imgs"
    imgs.mkdir()
    _make_image(imgs / "s10.png", "100x100", "red")
    _make_image(imgs / "s2.png", "64x36", "green")
    (imgs / "notes.txt").write_text("x")
    slides = freeze(str(imgs), tmp_path / "out", width=W, height=H)
    assert [s.title for s in slides] == ["Slide 1", "Slide 2"]
    assert all(png_size(tmp_path / "out" / s.file) == (W, H) for s in slides)
    assert slides[0].sha256 != slides[1].sha256


def test_empty_image_dir_raises(tmp_path):
    (tmp_path / "imgs").mkdir()
    with pytest.raises(NoSlidesError):
        freeze(str(tmp_path / "imgs"), tmp_path / "out", width=W, height=H)


def test_unsupported_source(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(ValueError):
        freeze(str(tmp_path / "a.txt"), tmp_path / "out")


@needs_chromium
@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="pdftoppm missing")
def test_pdf_source(tmp_path):
    from playwright.sync_api import sync_playwright

    pdf = tmp_path / "deck.pdf"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content(
            "<h1 style='page-break-after:always'>P1</h1><h1>P2</h1>"
        )
        page.pdf(path=str(pdf))
        browser.close()
    slides = freeze(str(pdf), tmp_path / "out", width=W, height=H)
    assert len(slides) == 2
    assert all(png_size(tmp_path / "out" / s.file) == (W, H) for s in slides)
