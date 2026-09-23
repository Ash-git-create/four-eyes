import json
from pathlib import Path

import pytest

from retrieval.claims import ClaimsChecker, looks_like_health_claim, parse_register, split_sentences

from fakes import FakeEmbedder

RECORD = {
    "childrenValues": [
        {"valueIdentifier": "policyItemCode", "value": "POL-HC-1", "childrenValues": []},
        {"valueIdentifier": "hcWrapper", "value": None, "childrenValues": [
            {"valueIdentifier": "hcEntryId", "value": 854, "childrenValues": []},
            {"valueIdentifier": "hcClaim", "value": "<p>Glucomannan contributes to weight loss</p>", "childrenValues": []},
            {"valueIdentifier": "hcClaimStatus", "value": "HCCS_AUTHORISED", "childrenValues": []},
            {"valueIdentifier": "hcNutSubFoodCat", "value": "Glucomannan  (konjac mannan)", "childrenValues": []},
            {"valueIdentifier": "hcCondOfUse", "value": "1 g per portion", "childrenValues": []},
        ]},
    ]
}


def write(tmp_path: Path, records) -> Path:
    p = tmp_path / "eu_claims_register_2026-01-01.json"
    p.write_text(json.dumps(records))
    return p


def test_parse_register_flattens_and_cleans(tmp_path):
    c = parse_register(write(tmp_path, [RECORD]))[0]
    assert c.policy_item_code == "POL-HC-1"
    assert c.entry_id == "854"                                  # numeric id becomes a string
    assert c.claim == "Glucomannan contributes to weight loss"  # HTML stripped
    assert c.subject == "Glucomannan (konjac mannan)"           # whitespace collapsed
    assert c.status == "authorised"                             # code mapped
    assert c.conditions_of_use == "1 g per portion"


def test_parse_register_maps_all_statuses(tmp_path):
    def with_status(code):
        r = json.loads(json.dumps(RECORD))
        r["childrenValues"][1]["childrenValues"][2]["value"] = code
        return r
    statuses = [c.status for c in parse_register(write(tmp_path, [
        with_status("HCCS_AUTHORISED"), with_status("HCCS_NON_AUTHORISED"), with_status("HCCS_REVOKED")]))]
    assert statuses == ["authorised", "non_authorised", "revoked"]


def test_parse_register_skips_rows_without_claim_or_code(tmp_path):
    broken = {"childrenValues": [{"valueIdentifier": "hcClaimStatus", "value": "HCCS_AUTHORISED",
                                  "childrenValues": []}]}
    assert parse_register(write(tmp_path, [broken, RECORD])) == parse_register(write(tmp_path, [RECORD]))


@pytest.mark.parametrize("text,expected", [
    ("Protein supports muscle growth.", True),
    ("Dieses Produkt unterstützt das Immunsystem.", True),
    ("The bar costs 2.49 euro.", False),
    ("Wir liefern in drei Werktagen.", False),
])
def test_health_claim_cues(text, expected):
    assert looks_like_health_claim(text) is expected


def test_split_sentences_handles_punctuation_and_newlines():
    assert split_sentences("One. Two!\nThree?  ") == ["One.", "Two!", "Three?"]


def make_checker(score, threshold=0.85):
    match = {"policy_item_code": "POL-HC-1", "entry_id": "854", "subject": "Glucomannan",
             "claim": "contributes to weight loss", "conditions_of_use": None, "score": score}
    return ClaimsChecker(FakeEmbedder(), lambda vec: match, match_threshold=threshold)


def test_close_match_is_not_flagged():
    out = make_checker(0.95).check("Glucomannan contributes to weight loss.")
    assert out["flagged_count"] == 0
    assert out["sentences"][0]["best_match"]["entry_id"] == "854"


def test_weak_match_is_flagged():
    out = make_checker(0.60).check("This powder boosts your immune system.")
    assert out["flagged_count"] == 1


def test_sentence_without_health_language_is_not_checked():
    out = make_checker(0.10).check("Shipping to Austria costs 8.95 euro.")
    assert out["flagged_count"] == 0
    assert out["sentences"][0]["best_match"] is None


def test_default_threshold_is_documented_as_provisional():
    from retrieval.claims import ClaimsChecker as C
    assert C(FakeEmbedder(), lambda v: None).match_threshold == 0.85


def test_no_authorised_claims_in_db_means_flagged():
    checker = ClaimsChecker(FakeEmbedder(), lambda vec: None)
    assert checker.check("It supports recovery.")["flagged_count"] == 1
