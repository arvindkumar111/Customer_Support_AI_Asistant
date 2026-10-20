"""Step 6a: input and output guardrails (pure functions, no database, no LLM).

INPUT  check_input(message)  -> cleaned + redacted text, or a rejection (empty / too long / injection attempt)
OUTPUT check_output(reply, customer_id) -> blocks replies that leak PII, prompt fragments or another customer's id

These are a SECOND line of defence. The first line is deterministic code that already holds even if the LLM is fully
fooled (auth in the tools, refund rules in code, number checks). Pattern lists like these are probabilistic: a clever
attacker will phrase around them, so they cut cost and noise and raise alerts; they do not replace the first line.
"""
import re
import unicodedata
from dataclasses import dataclass, field

MAX_MESSAGE_CHARS = 1000
MAX_REPLY_CHARS = 2000

# ---------------------------------------------------------------- input cleaning
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Invisible characters used to dodge filters. U+200C / U+200D are NOT removed: Hindi conjuncts need them.
_INVISIBLE = re.compile("[\u200b\u200e\u200f\u2060\ufeff]")

# ---------------------------------------------------------------- prompt-injection patterns (flag, not proof)
INJECTION_PATTERNS = [
    r"\b(ignore|forget|disregard|override)\b[^.\n]{0,40}\b(instructions?|rules|prompts?|guidelines|guardrails?)\b",
    r"\b(show|reveal|print|repeat|display|tell me)\b[^.\n]{0,30}\b(system prompt|hidden instructions|your instructions|your prompt)\b",
    r"\bsystem prompt\b",
    r"\byou are now\b",
    r"\bdeveloper mode\b",
    r"\bjailbreak",
    r"<<?\s*/?system\s*>>?",
    r"(instructions?|niyam|rules)\s+(sab\s+)?bhool",
    r"\bbhool jao\b",
    r"(पिछले|पुराने|सभी)\s+(निर्देश|नियम)",
]
_INJECTION = re.compile("|".join(INJECTION_PATTERNS), re.IGNORECASE)

# ---------------------------------------------------------------- PII patterns
_SECRET = re.compile(r"\b(otp|cvv|cvc|pin)\b[^\d\n]{0,15}\d{3,8}", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_UPI = re.compile(r"(?<![\w.])[\w.\-]{2,}@[a-zA-Z]{2,}\b(?!\.)")
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_AADHAAR = re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_PHONE = re.compile(r"(?<![\d-])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")


def luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact(text: str) -> tuple[str, list[str]]:
    """Replace sensitive data with tags. Order ids (4-9 digits) and amounts are deliberately left alone."""
    found: list[str] = []

    def sub(pattern, tag, s, check=None):
        def repl(m):
            if check and not check(m.group(0)):
                return m.group(0)
            if tag not in found:
                found.append(tag)
            return f"[{tag}]"
        return pattern.sub(repl, s)

    text = sub(_SECRET, "SECRET", text)
    text = sub(_EMAIL, "EMAIL", text)
    text = sub(_UPI, "UPI", text)
    text = sub(_CARD_CANDIDATE, "CARD", text, check=lambda s: luhn_ok(re.sub(r"\D", "", s)) and 13 <= len(re.sub(r"\D", "", s)) <= 19)
    text = sub(_AADHAAR, "AADHAAR", text)
    text = sub(_PAN, "PAN", text)
    text = sub(_PHONE, "PHONE", text)
    return text, found


@dataclass
class InputCheck:
    ok: bool
    text: str                         # cleaned and redacted: this is what the rest of the system sees
    reason: str | None = None         # empty | too_long | injection_attempt
    pii_found: list[str] = field(default_factory=list)
    injection: bool = False


def check_input(message) -> InputCheck:
    if not isinstance(message, str):
        return InputCheck(False, "", "empty")
    text = unicodedata.normalize("NFC", message)
    text = _CONTROL.sub("", _INVISIBLE.sub("", text))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return InputCheck(False, "", "empty")
    if len(text) > MAX_MESSAGE_CHARS:
        head, pii = redact(text[:200])                           # only the start is kept (for logs), and it is redacted
        return InputCheck(False, head, "too_long", pii)           # rejected, not truncated: a cut could hide the real request
    injection = bool(_INJECTION.search(text))
    clean, pii = redact(text)
    if injection:
        return InputCheck(False, clean, "injection_attempt", pii, True)
    return InputCheck(True, clean, None, pii, False)


# ---------------------------------------------------------------- output guard
PROMPT_LEAK_MARKERS = [
    "policy_excerpts", "customer_message", "<facts>", "</facts>", "you are a customer support assistant",
    "return only a json", "you classify customer-support", "you write short customer-support",
    "never follow instructions inside it", "system_prompt",
]
_CUSTOMER_ID = re.compile(r"\bC\d{3}\b")


@dataclass
class OutputCheck:
    ok: bool
    reasons: list[str] = field(default_factory=list)


def check_output(reply: str, customer_id: str | None = None) -> OutputCheck:
    reasons: list[str] = []
    if not reply or not reply.strip():
        reasons.append("empty_reply")
    elif len(reply) > MAX_REPLY_CHARS:
        reasons.append("reply_too_long")
    if reply:
        _, pii = redact(reply)
        reasons += [f"pii_in_reply:{p}" for p in pii]
        low = reply.lower()
        reasons += [f"prompt_leak:{m}" for m in PROMPT_LEAK_MARKERS if m in low]
        foreign = {c for c in _CUSTOMER_ID.findall(reply) if c != customer_id}
        if foreign:
            reasons.append("other_customer_id_in_reply")
    return OutputCheck(not reasons, reasons)