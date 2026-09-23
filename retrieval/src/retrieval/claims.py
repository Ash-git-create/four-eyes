"""EU health-claims register: parsing, storage, and the draft claims check.

The check flags sentences in a draft that read like a health claim but do not match
an authorised entry in the EU register. It is a heuristic aid for the approver.
It is NOT a compliance check and must never be described as one.
"""
import json
import re
from dataclasses import dataclass
from pathlib import Path

STATUS = {"HCCS_AUTHORISED": "authorised", "HCCS_NON_AUTHORISED": "non_authorised",
          "HCCS_REVOKED": "revoked"}

# Words that suggest a sentence asserts a health effect. Deliberately small and
# explicit: a longer list would hide what the flag actually reacts to.
HEALTH_CUES_EN = ("health", "immune", "muscle", "bone", "energy", "metabolism", "recovery",
                  "digestion", "gut", "heart", "cholesterol", "blood", "skin", "hair",
                  "concentration", "fatigue", "weight loss", "performance", "supports",
                  "contributes to", "helps", "maintenance", "normal function")
HEALTH_CUES_DE = ("gesundheit", "immunsystem", "muskel", "knochen", "energie", "stoffwechsel",
                  "regeneration", "verdauung", "darm", "herz", "cholesterin", "blut", "haut",
                  "haare", "konzentration", "müdigkeit", "abnehmen", "leistung", "unterstützt",
                  "trägt bei", "hilft", "erhaltung", "normale funktion")
HEALTH_CUES = HEALTH_CUES_EN + HEALTH_CUES_DE

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
TAGS = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class Claim:
    policy_item_code: str
    entry_id: str | None
    claim_type: str | None
    subject: str
    claim: str
    status: str
    conditions_of_use: str | None
    health_relationship: str | None
    efsa_reference: str | None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    text = TAGS.sub(" ", value).replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text).strip() or None


def _flatten(node: dict, out: dict | None = None) -> dict:
    """The portal returns a nested value tree; collapse it to identifier -> value."""
    out = {} if out is None else out
    for child in node.get("childrenValues", []):
        key = child.get("valueIdentifier")
        if child.get("value") is not None:
            out.setdefault(key, child["value"])
        _flatten(child, out)
    return out


def parse_register(path: Path) -> list[Claim]:
    records = json.loads(path.read_text(encoding="utf-8"))
    claims = []
    for record in records:
        r = _flatten(record)
        status = STATUS.get(r.get("hcClaimStatus"))
        claim, code = _clean(r.get("hcClaim")), r.get("policyItemCode")
        if not (status and claim and code):
            continue  # a row we cannot cite or classify is not usable
        claims.append(Claim(
            policy_item_code=code,
            entry_id=str(r["hcEntryId"]) if r.get("hcEntryId") else None,
            claim_type=r.get("hcClaimType"),
            subject=_clean(r.get("hcNutSubFoodCat")) or "",
            claim=claim,
            status=status,
            conditions_of_use=_clean(r.get("hcCondOfUse")),
            health_relationship=_clean(r.get("hcHealthRelationship")),
            efsa_reference=_clean(r.get("hcEfsaQuestionNbr")),
        ))
    return claims


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENTENCE_SPLIT.split(text) if s.strip()]


def looks_like_health_claim(sentence: str) -> bool:
    low = sentence.lower()
    return any(cue in low for cue in HEALTH_CUES)


class ClaimsChecker:
    """Flags health-claim-like sentences with no close authorised match.

    match_threshold 0.85 is PROVISIONAL: picked from five hand-written example sentences
    so that a correctly stated German claim (0.875) passes while an invented English one
    (0.845) is flagged. Five examples is not a calibration. No precision or recall number
    may be claimed until it is set on a hand-labelled sample (EVIDENCE.md).

    Known weakness: the register is English only, so a correct claim written in German
    scores lower than the same claim in English and sits close to the threshold.
    """

    def __init__(self, embedder, nearest_authorised, match_threshold: float = 0.85):
        self.embedder = embedder
        self.nearest_authorised = nearest_authorised
        self.match_threshold = match_threshold

    def check(self, draft: str) -> dict:
        sentences = []
        for sentence in split_sentences(draft):
            if not looks_like_health_claim(sentence):
                sentences.append({"text": sentence, "health_claim_like": False,
                                  "flagged": False, "best_match": None})
                continue
            match = self.nearest_authorised(self.embedder.embed_query(sentence))
            matched = bool(match) and match["score"] >= self.match_threshold
            sentences.append({"text": sentence, "health_claim_like": True,
                              "flagged": not matched, "best_match": match})
        return {
            "sentences": sentences,
            "flagged_count": sum(s["flagged"] for s in sentences),
            "match_threshold": self.match_threshold,
            "disclaimer": "Heuristic flag for the approver. Not a compliance check.",
        }
