"""Input masking and output safety checks (Kickoff 4, Blueprint B4 step [8]).

- ``mask_sensitive``: remove ID/bank/card numbers and similar from text before it goes into
  an LLM prompt.
- ``sensitive_topics``: price / contract / money topics. Any hit forces ``need_approval``
  (Kickoff 3 #4, CLAUDE.md rule 3) whatever the permission rules say.
- ``check_output``: post-LLM filter. Flags make an auto reply a draft for a human.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# 9+ digits, optionally grouped with spaces/dots/dashes: CCCD (12), CMND (9), bank accounts,
# card numbers, phone numbers. They never need to reach the model verbatim.
_LONG_NUMBER = re.compile(r"(?<![\w])(?:\d[ .\-]?){8,}\d(?![\w])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_SECRET_HINT = re.compile(
    r"(?i)\b(otp|mã xác (?:nhận|thực)|mật khẩu|password|pin)\b\s*[:là]*\s*\S+",
)


def mask_sensitive(text: str) -> str:
    if not text:
        return text
    text = _SECRET_HINT.sub(lambda m: m.group(1) + " [đã ẩn]", text)
    text = _EMAIL.sub("[email đã ẩn]", text)
    return _LONG_NUMBER.sub("[số đã ẩn]", text)


def _fold(text: str) -> str:
    """Lowercase and strip Vietnamese diacritics so 'Giá', 'gia', 'GIÁ' all match."""
    text = unicodedata.normalize("NFD", text.lower()).replace("đ", "d")
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


# Topic code -> phrases. A phrase with Vietnamese diacritics ("giá") only matches accented
# text, so "gia đình" is not a price question; an ASCII phrase ("bao gia") also matches the
# text with diacritics removed, because people often type without accents.
SENSITIVE_TOPICS: dict[str, tuple[str, ...]] = {
    "gia": (
        "giá",
        "bao gia",
        "bang gia",
        "gia ca",
        "gia bao nhieu",
        "bao nhieu tien",
        "het bao nhieu",
        "chiet khau",
        "giam gia",
        "khuyen mai",
        "uu dai",
        "price",
        "pricing",
        "discount",
        "quote",
    ),
    "hop_dong": ("hop dong", "ky ket", "dieu khoan", "contract", "cam ket"),
    "tien": (
        "tiền",
        "thanh toan",
        "chuyen khoan",
        "hoa don",
        "cong no",
        "hoan tien",
        "dat coc",
        "tam ung",
        "stk",
        "so tai khoan",
        "payment",
        "invoice",
        "refund",
    ),
}

# Contact asks for a person: always hand over (escalate).
HANDOFF_PHRASES: tuple[str, ...] = (
    "gap nguoi that",
    "noi chuyen voi nguoi",
    "gap truc tiep",
    "nhan vien tu van",
    "goi cho toi",
    "goi cho em",
    "goi lai cho",
    "human",
    "real person",
)

_AI_SELF_DISCLOSURE = (
    "la mot ai",
    "la tri tue nhan tao",
    "mo hinh ngon ngu",
    "language model",
    "as an ai",
    "toi la ai",
    "claude",
    "anthropic",
)
_PROMISE_WORDS = ("cam ket", "dam bao", "chac chan", "bao hanh", "mien phi")
_URL = re.compile(r"https?://|www\.", re.I)
_MONEY = re.compile(r"(?i)\d[\d.,]*\s*(?:k|nghìn|ngàn|tr|triệu|tỷ|ty|đ|vnd|vnđ|usd|\$)(?![a-zà-ỹ])")


def _contains(haystack: str, phrase: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(phrase)}(?![\w])", haystack) is not None


def _has(text: str, phrase: str) -> bool:
    lowered = unicodedata.normalize("NFC", text.lower())
    if phrase.isascii():
        return _contains(_fold(text), phrase)
    return _contains(lowered, unicodedata.normalize("NFC", phrase.lower()))


def sensitive_topics(text: str, extra_topics: list[str] | None = None) -> list[str]:
    """Topic codes found in ``text``. ``extra_topics`` = owner's topics_to_avoid."""
    text = text or ""
    hits = [code for code, words in SENSITIVE_TOPICS.items() if any(_has(text, w) for w in words)]
    for topic in extra_topics or []:
        topic = str(topic or "").strip()
        if topic and (_has(text, topic) or _contains(_fold(text), _fold(topic))):
            hits.append(f"avoid:{_fold(topic)[:40]}")
    return hits


def asks_for_human(text: str) -> bool:
    return any(_has(text or "", p) for p in HANDOFF_PHRASES)


@dataclass
class OutputCheck:
    text: str
    flags: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.flags


def check_output(text: str, *, max_chars: int, extra_topics: list[str] | None = None):
    """Clean the model's reply and list the reasons a human must look at it first."""
    cleaned = (text or "").strip().strip('"').strip()
    flags: list[str] = []
    if not cleaned:
        return OutputCheck("", ["empty"])
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rsplit(" ", 1)[0] + "…"
        flags.append("too_long")
    flags += [f"sensitive:{code}" for code in sensitive_topics(cleaned, extra_topics)]
    if _MONEY.search(cleaned):
        flags.append("money_amount")
    if any(_has(cleaned, w) for w in _PROMISE_WORDS):
        flags.append("promise")
    if _URL.search(cleaned):
        flags.append("link")
    if _LONG_NUMBER.search(cleaned) or _EMAIL.search(cleaned):
        flags.append("personal_data")
    if any(_has(cleaned, w) for w in _AI_SELF_DISCLOSURE):
        flags.append("ai_meta")
    return OutputCheck(cleaned, sorted(set(flags)))
