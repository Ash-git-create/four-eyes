import pytest

from retrieval.redaction import redact


@pytest.mark.parametrize("text,expected,label", [
    ("Mail me at jana.muster@example.de please", "Mail me at [EMAIL] please", "EMAIL"),
    ("IBAN DE89 3704 0044 0532 0130 00 bitte", "IBAN [IBAN] bitte", "IBAN"),
    ("IBAN DE89370400440532013000", "IBAN [IBAN]", "IBAN"),
    ("Card 4111 1111 1111 1111 was charged", "Card [CARD] was charged", "CARD"),
    ("Ruf mich an: +49 151 23456789", "Ruf mich an: [PHONE]", "PHONE"),
    ("Tel. 0621 / 123 4567", "Tel. [PHONE]", "PHONE"),
    ("Lieferung nach 69117 Heidelberg", "Lieferung nach [POSTCODE_TOWN]", "POSTCODE_TOWN"),
])
def test_redacts(text, expected, label):
    out, counts = redact(text)
    assert out == expected
    assert counts == {label: 1}


@pytest.mark.parametrize("text", [
    "Order 12345 arrived",                      # order number, not PII
    "The bar has 20 g protein per 100 g",
    "I ordered 2 tubs on 12.09.2026",           # dates are not phone numbers
    "Card-like but fails Luhn: 4111 1111 1111 1112",
])
def test_leaves_non_pii_alone(text):
    out, counts = redact(text)
    assert out == text
    assert counts == {}


def test_counts_multiple():
    out, counts = redact("a@b.de and c@d.com, call 0151 2345 6789")
    assert counts == {"EMAIL": 2, "PHONE": 1}
    assert "@" not in out


def test_names_are_not_redacted_documented_gap():
    # Pattern matching cannot find names. This test pins the known limitation so
    # nobody believes otherwise; GOVERNANCE.md says the same.
    out, _ = redact("Hallo, ich bin Jana Muster.")
    assert "Jana Muster" in out
