"""Pattern-based PII redaction, applied before any customer text reaches a model.

Catches structured identifiers: email addresses, IBANs, payment card numbers
(Luhn-checked), phone numbers, German postcodes followed by a town. It does NOT
catch personal names or street names; that needs NER and is a documented gap
(GOVERNANCE.md), not something this module claims to do.

The sender's address never needs to pass through the model: n8n keeps it from
the trigger metadata for the reply. So redaction is one-way, with no mapping back.
"""
import re

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b")
CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")
PHONE = re.compile(r"(?<![\w/])(?:\+|00)?\d[\d ()/-]{7,}\d(?![\w/])")
POSTCODE_TOWN = re.compile(r"\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß]+(?:[ -][A-ZÄÖÜ][a-zäöüß]+)?\b")


def _luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def redact(text: str) -> tuple[str, dict[str, int]]:
    """Returns (redacted text, count per PII type). Order matters: specific patterns
    first, so an IBAN or card number is not half-eaten by the phone pattern."""
    counts: dict[str, int] = {}

    def sub(pattern, label, text, check=None):
        def repl(m):
            if check and not check(m.group()):
                return m.group()
            counts[label] = counts.get(label, 0) + 1
            return f"[{label}]"
        return pattern.sub(repl, text)

    text = sub(EMAIL, "EMAIL", text)
    text = sub(IBAN, "IBAN", text)
    text = sub(CARD, "CARD", text, check=lambda s: _luhn_ok(re.sub(r"\D", "", s)))
    text = sub(PHONE, "PHONE", text, check=lambda s: 8 <= len(re.sub(r"\D", "", s)) <= 15)
    text = sub(POSTCODE_TOWN, "POSTCODE_TOWN", text)
    return text, counts
