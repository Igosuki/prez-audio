import difflib
import re
from dataclasses import dataclass, field


def normalize_words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def wer(reference: str, hypothesis: str) -> float:
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1] / len(ref)


def word_diff(reference: str, hypothesis: str) -> list[str]:
    ref, hyp = normalize_words(reference), normalize_words(hypothesis)
    ops = difflib.SequenceMatcher(a=ref, b=hyp, autojunk=False).get_opcodes()
    return [f"expected '{' '.join(ref[i1:i2])}' / heard '{' '.join(hyp[j1:j2])}'"
            for tag, i1, i2, j1, j2 in ops if tag != "equal"]


@dataclass
class VerifyRow:
    slide: int
    status: str
    wer: float | None = None
    reason: str = ""
    heard: str = ""
    diff: list[str] = field(default_factory=list)
