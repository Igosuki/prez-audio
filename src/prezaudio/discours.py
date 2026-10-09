import re

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_LINE_MARK = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+|[-*+][ \t]+|\d+\.[ \t]+|>[ \t]?)", re.M)
_EMPHASIS = re.compile(r"\*+|`+|(?<!\w)_+|_+(?!\w)")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def split_title(raw: str) -> tuple[str | None, str]:
    text = _COMMENT.sub("", raw).lstrip()
    first, _, rest = text.partition("\n")
    if first.startswith("# "):
        return first[2:].strip(), rest
    return None, text


def tts_text(raw: str) -> str:
    _, body = split_title(raw)
    body = _LINK.sub(r"\1", body)
    body = _LINE_MARK.sub("", body)
    body = _EMPHASIS.sub("", body)
    return " ".join(body.split())


def sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_END.split(text) if s]


def stub(title: str) -> str:
    return f"# {title}\n\n"
