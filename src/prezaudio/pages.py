import base64
import json
import os
import tempfile
from html import escape
from pathlib import Path

from prezaudio.media import ffmpeg
from prezaudio.project import Project
from prezaudio.status import slide_status

THUMB_WIDTH = 960

_CSS = """
:root{--bg:#fff;--fg:#1d1d1f;--muted:#6b6b72;--card:#f4f4f6;--line:#d9d9de;--accent:#2a5bd7;--ok:#1a7f37;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#141417;--fg:#ececf0;--muted:#9a9aa3;--card:#1e1e23;--line:#33333b;--accent:#7aa2ff;--ok:#4cc26b;--bad:#ff7b72}}
*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:16px/1.5 system-ui,sans-serif}
h1{font-size:1.4rem;margin:0 0 16px}.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;margin:0 auto 16px;max-width:1000px}
.card img{width:100%;height:auto;border-radius:4px;display:block}.badge{display:inline-block;padding:0 8px;border-radius:99px;border:1px solid var(--line);font-size:.8rem;margin-right:4px}
.ok{color:var(--ok)}.bad{color:var(--bad)}.muted{color:var(--muted)}pre{white-space:pre-wrap;font:inherit;margin:8px 0 0}
audio{width:100%}code{font-size:.85rem}
"""


def write(path: Path, html: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)


def _doc(title: str, body: str, css: str = _CSS) -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>{escape(title)}</title><style>{css}</style></head><body>{body}</body></html>")


def _json_for_script(obj) -> str:
    return json.dumps(obj).replace("<", "\\u003c")


def thumbnail_jpeg(image: Path) -> bytes:
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "t.jpg"
        ffmpeg("-i", image, "-vf", f"scale='min({THUMB_WIDTH},iw)':-2", "-frames:v", "1",
               "-q:v", "5", out)
        return out.read_bytes()


def _badge(label: str, ok: bool) -> str:
    return f'<span class="badge {"ok" if ok else "bad"}">{escape(label)}</span>'


def review_page(project: Project) -> str:
    cards = []
    for n in project.numbers():
        st = slide_status(project, n)
        thumb = base64.b64encode(thumbnail_jpeg(project.image_path(n))).decode()
        text = project.raw_discours(n)
        body = escape(text.strip()) if text and text.strip() else '<span class="muted">(no discours)</span>'
        badges = (_badge("validated" if st["validated"] else "not validated", st["validated"])
                  + _badge("audio fresh" if st["audio_fresh"] else "audio stale", st["audio_fresh"])
                  + _badge("clip fresh" if st["clip_fresh"] else "clip stale", st["clip_fresh"]))
        cards.append(
            f'<section class="card" id="slide-{project.key(n)}">'
            f'<h2>Slide {n} - {escape(st["title"])}</h2>'
            f'<img alt="Slide {n}" src="data:image/jpeg;base64,{thumb}">'
            f"<p>{badges}</p><pre>{body}</pre></section>")
    return _doc(f"{project.title} - review", f"<h1>{escape(project.title)} - review</h1>" + "".join(cards))


def audition_page(project: Project, variants: list[dict], text: str) -> str:
    cards = []
    for v in variants:
        settings = ", ".join(f"{k}={v[k]}" for k in
                             ("stability", "similarity_boost", "style", "speed", "use_speaker_boost",
                              "model_id", "voice_id"))
        cards.append(
            f'<section class="card" id="variant-{v["k"]}"><h2>Variant {v["k"]}</h2>'
            f'<audio controls preload="none" src="{escape(v["file"], quote=True)}"></audio>'
            f'<p><code>{escape(settings)}</code></p>'
            f'<p class="muted">Adopt with <code>prez voice choose {v["k"]}</code></p></section>')
    intro = f'<p class="muted">Slide {variants[0]["slide"]}: {escape(text)}</p>' if variants else ""
    return _doc(f"{project.title} - voice audition",
                f"<h1>{escape(project.title)} - voice audition</h1>{intro}" + "".join(cards))


_PLAYER_CSS = _CSS + """
.layout{display:grid;gap:16px;grid-template-columns:minmax(0,2fr) minmax(260px,1fr);max-width:1400px;margin:0 auto}
@media (max-width:800px){.layout{grid-template-columns:1fr}}
video{width:100%;background:#000;border-radius:8px}
.side{display:flex;flex-direction:column;gap:12px;min-height:0}
.panel{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px;overflow:auto;position:relative;max-height:36vh}
ol,ul{list-style:none;margin:0;padding:0}li{padding:4px 8px;border-radius:4px;cursor:pointer}
li:hover{background:var(--line)}li.active{background:var(--accent);color:#fff}
.speeds button{margin:2px;padding:4px 10px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg);cursor:pointer}
.speeds button[aria-pressed=true]{background:var(--accent);color:#fff}
"""

_PLAYER_JS = """
const D=JSON.parse(document.getElementById('data').textContent);
const v=document.getElementById('v');
const chapEl=document.getElementById('chapters'),trEl=document.getElementById('transcript'),spEl=document.getElementById('speeds');
function seek(t){v.currentTime=t;v.play()}
D.chapters.forEach((c,i)=>{const li=document.createElement('li');li.textContent=(i+1)+'. '+c.title;li.onclick=()=>seek(c.start);chapEl.appendChild(li)});
D.cues.forEach(c=>{const li=document.createElement('li');li.textContent=c.text.replace(/\\n/g,' ');li.onclick=()=>seek(c.start);trEl.appendChild(li)});
[1,1.25,1.5,2,3,5,10].forEach(s=>{const b=document.createElement('button');b.textContent='\\u00d7'+s;b.setAttribute('aria-pressed',s===1);
b.onclick=()=>{v.playbackRate=s;[...spEl.children].forEach(x=>x.setAttribute('aria-pressed',x===b))};spEl.appendChild(b)});
let curCue=-1,curChap=-1;
function mark(list,idx,prev){if(idx===prev)return prev;if(prev>=0)list.children[prev].classList.remove('active');
if(idx>=0){const el=list.children[idx];el.classList.add('active');const box=list.parentElement;
if(el.offsetTop<box.scrollTop||el.offsetTop+el.offsetHeight>box.scrollTop+box.clientHeight)box.scrollTop=el.offsetTop-box.clientHeight/3}return idx}
function tick(){const t=v.currentTime;
curCue=mark(trEl,D.cues.findIndex(c=>t>=c.start&&t<c.end),curCue);
curChap=mark(chapEl,D.chapters.findIndex(c=>t>=c.start&&t<c.end),curChap)}
v.addEventListener('timeupdate',tick);v.addEventListener('seeked',tick);
"""


def player_page(project: Project, video: Path, chapters: list[dict], cues: list[dict],
                page_dir: Path) -> str:
    rel = os.path.relpath(video, page_dir).replace(os.sep, "/")
    data = _json_for_script({"chapters": chapters, "cues": cues})
    body = (
        f"<h1>{escape(project.title)}</h1><div class=\"layout\">"
        f'<video id="v" controls preload="metadata" src="{escape(rel, quote=True)}"></video>'
        f'<div class="side"><div class="speeds" id="speeds"></div>'
        f'<h2>Chapters</h2><div class="panel"><ol id="chapters"></ol></div>'
        f'<h2>Transcript</h2><div class="panel"><ul id="transcript"></ul></div></div></div>'
        f'<script type="application/json" id="data">{data}</script><script>{_PLAYER_JS}</script>')
    return _doc(project.title, body, _PLAYER_CSS)
