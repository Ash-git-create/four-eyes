"""Load the EU claims register into Postgres. Usage: uv run ingest-claims"""
import glob
import json
from pathlib import Path

from . import config, store
from .claims import parse_register


def main() -> None:
    from .models import Embedder

    files = sorted(glob.glob(str(config.REPO_ROOT / "data" / "raw" / "eu_claims_register_*.json")))
    if not files:
        raise SystemExit("no data/raw/eu_claims_register_*.json found")
    path = Path(files[-1])
    claims = parse_register(path)
    authorised = [c for c in claims if c.status == "authorised"]
    # Embed claim + subject: "contributes to normal energy-yielding metabolism" alone
    # is ambiguous without knowing which substance it is about.
    vectors = dict(zip(
        [c.policy_item_code for c in authorised],
        Embedder().embed_passages([f"{c.subject}: {c.claim}" for c in authorised]),
    ))
    with store.connect(config.database_url()) as conn:
        store.apply_schema(conn)
        store.replace_claims(conn, claims, vectors)
        by_status = dict(conn.execute("SELECT status, count(*) FROM claims GROUP BY status").fetchall())
    print(json.dumps({"file": path.name, "parsed": len(claims), "embedded_authorised": len(vectors),
                      "by_status": by_status}, indent=1))


if __name__ == "__main__":
    main()
