from __future__ import annotations

import hashlib
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

log = logging.getLogger(__name__)

SETTLE_MS = 300
KEYBOARD_CAP = 500
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
KNOWN_SLIDE_SELECTORS = ("section.slide", ".slide", "[data-slide]")

# Final animation state is kept (no `animation: none`) so fade-in slides stay visible.
FREEZE_CSS = (
    "*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;"
    "animation-iteration-count:1!important;transition:none!important;"
    "scroll-behavior:auto!important;caret-color:transparent!important}"
)

_SHOW_ONLY_JS = """([sel, idx]) => {
  const els = [...document.querySelectorAll(sel)];
  const store = (window.__pzStyles ||= new Map());
  els.forEach(e => { if (!store.has(e)) store.set(e, e.getAttribute('style')); });
  els.forEach((e, j) => {
    const orig = store.get(e);
    if (orig === null) e.removeAttribute('style'); else e.setAttribute('style', orig);
    if (j !== idx) { e.style.setProperty('display', 'none', 'important'); return; }
    if (getComputedStyle(e).display === 'none') e.style.setProperty('display', 'block', 'important');
    e.style.setProperty('opacity', '1', 'important');
    e.style.setProperty('visibility', 'visible', 'important');
  });
}"""

_TITLES_JS = """(sel) => [...document.querySelectorAll(sel)].map(
  e => (e.querySelector('h1, h2')?.textContent || '').trim())"""

_REVEAL_SLIDES_JS = """() => {
  if (Reveal.configure) Reveal.configure({transition: 'none', backgroundTransition: 'none'});
  const idx = (el) => [...el.parentElement.children].indexOf(el);
  return Reveal.getSlides().filter(s => !s.querySelector('section')).map(s => {
    let h, v = 0;
    if (Reveal.getIndices) ({h, v} = Reveal.getIndices(s));
    else if (s.parentElement.tagName === 'SECTION') { h = idx(s.parentElement); v = idx(s); }
    else h = idx(s);
    return {h, v, title: (s.querySelector('h1, h2')?.textContent || '').trim()};
  });
}"""

_VISIBLE_TITLE_JS = """() => {
  for (const e of document.querySelectorAll('h1, h2')) {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    if (r.width > 0 && r.height > 0 && r.right > 0 && r.left < innerWidth && r.bottom > 0
        && r.top < innerHeight && s.visibility !== 'hidden' && s.display !== 'none'
        && +s.opacity > 0) return e.textContent.trim();
  }
  return '';
}"""


@dataclass
class SlideInfo:
    n: int
    source_n: int
    title: str
    file: str
    sha256: str


class NoSlidesError(RuntimeError):
    pass


_Raw = list[tuple[str, bytes]]


def freeze(
    source: str,
    out_dir: Path,
    *,
    width: int = 1920,
    height: int = 1080,
    exclude: Iterable[int] = (),
    selector: str = "",
    deck_dir: Path | None = None,
) -> list[SlideInfo]:
    """Render each slide of `source` to `out_dir/slide-NN.png` (exactly width x height).

    `exclude` uses source numbering and is applied before renumbering. Existing
    `slide-*.png` in `out_dir` are removed. Raises NoSlidesError when nothing is left.
    """
    kind = _kind(source)
    if kind == "html":
        raws = _freeze_html(source, width, height, selector, deck_dir)
    elif kind == "pdf":
        raws = _freeze_pdf(Path(source), width, height)
    else:
        raws = _freeze_images(Path(source), width, height)
    if not raws:
        raise NoSlidesError(f"no slides found in {source}")

    excluded = set(exclude)
    unknown = sorted(n for n in excluded if not 1 <= n <= len(raws))
    if unknown:
        raise ValueError(f"exclude out of range 1..{len(raws)}: {unknown}")
    kept = [(i, title, data) for i, (title, data) in enumerate(raws, 1) if i not in excluded]
    if not kept:
        raise NoSlidesError(f"all {len(raws)} slides of {source} were excluded")

    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.glob("slide-*.png"):
        stale.unlink()
    pad = 3 if len(kept) >= 100 else 2
    infos = []
    for n, (source_n, title, data) in enumerate(kept, 1):
        name = f"slide-{n:0{pad}d}.png"
        (out_dir / name).write_bytes(data)
        infos.append(SlideInfo(n, source_n, title or f"Slide {source_n}", name, hashlib.sha256(data).hexdigest()))
    return infos


def _kind(source: str) -> str:
    if re.match(r"https?://", source):
        return "html"
    path = Path(source)
    if path.is_dir():
        return "images"
    if path.suffix.lower() == ".pdf":
        return "pdf"
    if path.suffix.lower() in (".html", ".htm"):
        return "html"
    raise ValueError(f"unsupported deck source: {source}")


def _freeze_html(source: str, width: int, height: int, selector: str, deck_dir: Path | None) -> _Raw:
    from playwright.sync_api import sync_playwright

    is_url = re.match(r"https?://", source) is not None
    allowed_root = None if is_url else Path(source).resolve().parent
    target = source if is_url else Path(source).resolve().as_uri()

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        try:
            context = browser.new_context(
                viewport={"width": width, "height": height}, device_scale_factor=1
            )
            context.route("**/*", _file_guard(allowed_root))
            page = context.new_page()
            response = page.goto(target, wait_until="load")
            if is_url and deck_dir is not None and response is not None:
                _save_frozen_copy(deck_dir, source, response.text())
            page.add_style_tag(content=FREEZE_CSS)
            page.evaluate("document.fonts.ready.then(() => true)")
            return _detect_and_capture(page, selector)
        finally:
            browser.close()


def _save_frozen_copy(deck_dir: Path, url: str, html: str) -> None:
    target = deck_dir / "deck"
    target.mkdir(parents=True, exist_ok=True)
    (target / "index.html").write_text(html, encoding="utf-8")
    (target / "SOURCE.txt").write_text(url + "\n", encoding="utf-8")


def _file_guard(allowed_root: Path | None):
    def handler(route):
        url = route.request.url
        if url.startswith("file://") and not _is_inside(url, allowed_root):
            log.warning("blocked file:// request outside deck directory: %s", url)
            route.abort()
        else:
            route.continue_()

    return handler


def _is_inside(file_url: str, root: Path | None) -> bool:
    if root is None:
        return False
    path = Path(unquote(urlparse(file_url).path)).resolve()
    return path == root or root in path.parents


def _detect_and_capture(page, selector: str) -> _Raw:
    if selector:
        return _capture_selector(page, selector)
    if _has_reveal(page):
        return _capture_reveal(page)
    for known in KNOWN_SLIDE_SELECTORS:
        if page.query_selector(known):
            return _capture_selector(page, known)
    return _capture_keyboard(page)


def _settle(page) -> None:
    page.wait_for_timeout(SETTLE_MS)


def _shot(page) -> bytes:
    _settle(page)
    return page.screenshot(type="png")


def _capture_selector(page, selector: str) -> _Raw:
    titles = page.evaluate(_TITLES_JS, selector)
    raws = []
    for i, title in enumerate(titles):
        page.evaluate(_SHOW_ONLY_JS, [selector, i])
        raws.append((title, _shot(page)))
    return raws


def _has_reveal(page) -> bool:
    if page.evaluate("typeof window.Reveal !== 'undefined'"):
        return True
    if page.query_selector(".reveal .slides"):
        try:
            page.wait_for_function("typeof window.Reveal !== 'undefined'", timeout=5000)
            return True
        except Exception:
            return False
    return False


def _capture_reveal(page) -> _Raw:
    raws = []
    for s in page.evaluate(_REVEAL_SLIDES_JS):
        page.evaluate("([h, v]) => Reveal.slide(h, v)", [s["h"], s["v"]])
        raws.append((s["title"], _shot(page)))
    return raws


def _capture_keyboard(page) -> _Raw:
    raws: _Raw = []
    previous = None
    while len(raws) < KEYBOARD_CAP:
        data = _shot(page)
        if data == previous:
            break
        raws.append((page.evaluate(_VISIBLE_TITLE_JS), data))
        previous = data
        page.keyboard.press("ArrowRight")
    return raws


def _freeze_pdf(path: Path, width: int, height: int) -> _Raw:
    _require("pdftoppm", "install poppler-utils to freeze PDF decks")
    with tempfile.TemporaryDirectory() as tmp:
        prefix = Path(tmp) / "page"
        subprocess.run(
            ["pdftoppm", "-png", "-scale-to-x", str(width), "-scale-to-y", "-1", str(path), str(prefix)],
            check=True, capture_output=True,
        )
        pages = sorted(Path(tmp).glob("page-*.png"), key=_natural_key)
        return [("", _letterbox(p, width, height, "white")) for p in pages]


def _freeze_images(directory: Path, width: int, height: int) -> _Raw:
    _require("ffmpeg", "install ffmpeg to freeze image decks")
    images = sorted(
        (p for p in directory.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES), key=_natural_key
    )
    return [("", _letterbox(p, width, height, "black")) for p in images]


def _letterbox(src: Path, width: int, height: int, color: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.png"
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color={color}"
        )
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vf", vf, "-frames:v", "1", str(out)],
            check=True, capture_output=True,
        )
        return out.read_bytes()


def _require(binary: str, hint: str) -> None:
    if shutil.which(binary) is None:
        raise RuntimeError(f"{binary} not found on PATH: {hint}")


def _natural_key(path: Path) -> list:
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", path.name)]
