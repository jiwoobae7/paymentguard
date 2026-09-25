"""
Fetch real DAO governance proposals from the Snapshot.org GraphQL API.

No API key required. Results are stored in data/blockchain/dao_proposals.json.

Spaces fetched:
  - uniswap              (Uniswap governance)
  - compound-governance  (Compound Finance governance)

Usage:
    python data/blockchain/fetch_dao_transactions.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.env"))

import json
import requests
from pathlib import Path
from datetime import datetime, timezone


SNAPSHOT_GRAPHQL_URL = "https://hub.snapshot.org/graphql"

PROPOSALS_QUERY = """
query {
  proposals(
    first: 20
    skip: 0
    where: { space_in: ["uniswapgovernance.eth", "comp-vote.eth"] }
    orderBy: "created"
    orderDirection: desc
  ) {
    id
    title
    body
    choices
    state
    scores
    scores_total
    space { id name }
    created
  }
}
"""

OUTPUT_PATH = Path(__file__).parent / "dao_proposals.json"


def fetch_proposals() -> list[dict]:
    """Fetch proposals from Snapshot GraphQL API and return raw list."""
    print("[fetch] Querying Snapshot.org GraphQL API...")
    print(f"[fetch] Endpoint: {SNAPSHOT_GRAPHQL_URL}")
    print("[fetch] Spaces:   uniswapgovernance.eth, comp-vote.eth\n")

    try:
        response = requests.post(
            SNAPSHOT_GRAPHQL_URL,
            json={"query": PROPOSALS_QUERY},
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout:
        print("[ERROR] Request timed out after 30 seconds.")
        return []
    except requests.exceptions.ConnectionError as exc:
        print(f"[ERROR] Could not connect to Snapshot API: {exc}")
        return []
    except requests.exceptions.HTTPError as exc:
        print(f"[ERROR] HTTP error from Snapshot API: {exc}")
        return []

    payload = response.json()

    if "errors" in payload:
        print(f"[ERROR] GraphQL errors: {payload['errors']}")
        return []

    proposals = payload.get("data", {}).get("proposals", [])
    print(f"[fetch] Retrieved {len(proposals)} proposals.\n")
    return proposals


def clean_proposal(raw: dict) -> dict:
    """Normalise a raw Snapshot proposal into a compact record."""
    body_full = raw.get("body") or ""
    body_preview = body_full[:500]

    scores = raw.get("scores") or []
    scores_total = raw.get("scores_total") or 0.0

    created_ts = raw.get("created")
    if created_ts:
        created_str = datetime.fromtimestamp(created_ts, tz=timezone.utc).isoformat()
    else:
        created_str = None

    space = raw.get("space") or {}

    return {
        "id": raw.get("id"),
        "title": raw.get("title"),
        "body_preview": body_preview,
        "choices": raw.get("choices") or [],
        "state": raw.get("state"),
        "scores": scores,
        "scores_total": scores_total,
        "space_id": space.get("id"),
        "space_name": space.get("name"),
        "created": created_str,
    }


def main():
    raw_proposals = fetch_proposals()

    if not raw_proposals:
        print("[warn] No proposals fetched. Writing empty file.")
        cleaned = []
    else:
        cleaned = [clean_proposal(p) for p in raw_proposals]

        # Print a preview of each proposal
        for i, p in enumerate(cleaned, start=1):
            print(f"[{i:02d}] [{p['space_name']}] {p['title']}")
            print(f"      State: {p['state']}  |  Scores total: {p['scores_total']:,.0f}")
            print(f"      Choices: {p['choices']}")
            if p["body_preview"]:
                preview = p["body_preview"].replace("\n", " ")[:120]
                print(f"      Body:   {preview}...")
            print()

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(cleaned, fh, indent=2, ensure_ascii=False)

    print(f"[done] Saved {len(cleaned)} proposals → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
