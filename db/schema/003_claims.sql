-- EU register of nutrition and health claims (European Commission, Food and Feed
-- Information Portal). Used only to FLAG unmatched health statements for the human
-- approver. This is a retrieval heuristic, never a compliance decision.
-- Only authorised claims get an embedding: they are what a draft is matched against.
CREATE TABLE IF NOT EXISTS claims (
    policy_item_code     text PRIMARY KEY,
    entry_id             text,
    claim_type           text,
    subject              text NOT NULL,       -- nutrient, substance, food or food category
    claim                text NOT NULL,
    status               text NOT NULL CHECK (status IN ('authorised', 'non_authorised', 'revoked')),
    conditions_of_use    text,
    health_relationship  text,
    efsa_reference       text,
    embedding            vector(384),
    ingested_at          timestamptz NOT NULL DEFAULT now()
);
