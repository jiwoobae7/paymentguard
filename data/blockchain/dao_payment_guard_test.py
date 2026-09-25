"""
Run fetched DAO governance proposals through PaymentGuard.

Each proposal is mapped to a PaymentIntent:
  - task_context:     "Execute approved DAO governance proposal for treasury management"
  - reasoning_trace:  proposal body split into up to 6 sentences
  - payment:
      vendor:        space name (e.g. "Uniswap DAO")
      amount:        scores_total / 1000, capped at $50,000
      payment_type:  "wire" if scores_total > 5,000,000 else "ach"

This surfaces which real governance proposals look suspicious to the classifier
and validates that the guard handles high-value but policy-compliant payments.

Usage:
    # First fetch proposals:
    python data/blockchain/fetch_dao_transactions.py

    # Then run the guard test:
    python data/blockchain/dao_payment_guard_test.py
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.env"))

import json
import re
import anthropic
from pathlib import Path

from payment_guard import PaymentGuard, PaymentPolicy, IntentClassifier, AuditLog, PaymentBlocked


PROPOSALS_PATH = Path(__file__).parent / "dao_proposals.json"

MAX_AMOUNT_CAP = 50_000.0
WIRE_THRESHOLD = 5_000_000


def load_proposals() -> list[dict]:
    if not PROPOSALS_PATH.exists():
        print(f"[ERROR] {PROPOSALS_PATH} not found.")
        print("[ERROR] Run fetch_dao_transactions.py first.")
        sys.exit(1)

    with open(PROPOSALS_PATH, "r", encoding="utf-8") as fh:
        proposals = json.load(fh)

    print(f"[load] Loaded {len(proposals)} proposals from {PROPOSALS_PATH}\n")
    return proposals


def body_to_trace(body: str, max_steps: int = 6) -> list[str]:
    """Split proposal body into up to max_steps sentence-like chunks."""
    if not body or not body.strip():
        return ["No proposal body provided."]

    # Split on sentence-ending punctuation or newlines
    parts = re.split(r"(?<=[.!?])\s+|\n{2,}", body.strip())
    parts = [p.strip() for p in parts if p.strip()]

    if not parts:
        return [body.strip()[:300]]

    # If we have more than max_steps, keep the first max_steps-1 and a summary
    if len(parts) > max_steps:
        trace = parts[: max_steps - 1]
        remaining = " ".join(parts[max_steps - 1 :])
        trace.append(remaining[:300] + ("..." if len(remaining) > 300 else ""))
    else:
        trace = parts

    return trace


def proposal_to_payment(proposal: dict) -> tuple[str, float, str]:
    """Derive vendor, amount, and payment_type from a proposal."""
    space_name = proposal.get("space_name") or proposal.get("space_id") or "Unknown DAO"
    vendor = space_name if space_name.endswith("DAO") else f"{space_name} DAO"

    scores_total = float(proposal.get("scores_total") or 0.0)
    raw_amount = scores_total / 1000.0
    amount = min(raw_amount, MAX_AMOUNT_CAP)

    # Ensure a minimum non-zero amount so the guard always evaluates something
    if amount < 1.0:
        amount = 1.0

    payment_type = "wire" if scores_total > WIRE_THRESHOLD else "ach"

    return vendor, amount, payment_type


def main():
    proposals = load_proposals()

    if not proposals:
        print("[warn] No proposals to test.")
        return

    policy = PaymentPolicy(
        agent_id="dao-treasury-agent",
        task_description="Execute approved DAO governance proposals for treasury management",
        allowed_vendors=["Uniswap DAO", "Compound DAO", "MakerDAO", "Aave DAO"],
        allowed_categories=["governance", "treasury", "DeFi"],
        max_amount_usd=100_000.0,
        allow_wire_transfers=True,
        require_known_counterparty=False,
    )

    client = anthropic.Anthropic()
    guard = PaymentGuard(
        policy=policy,
        classifier=IntentClassifier(client),
        audit=AuditLog(),
        mock_execution=True,
    )

    print("=" * 70)
    print("  DAO PROPOSAL PAYMENT GUARD TEST")
    print("=" * 70)
    print(f"  Policy agent:    {policy.agent_id}")
    print(f"  Max amount:      ${policy.max_amount_usd:,.0f}")
    print(f"  Wire allowed:    {policy.allow_wire_transfers}")
    print(f"  Known vendor req:{policy.require_known_counterparty}")
    print()

    results = []

    for i, proposal in enumerate(proposals[:5], start=1):
        title = proposal.get("title") or "(no title)"
        space = proposal.get("space_name") or proposal.get("space_id") or "?"
        state = proposal.get("state") or "unknown"
        body_preview = proposal.get("body_preview") or ""

        vendor, amount, payment_type = proposal_to_payment(proposal)
        reasoning_trace = body_to_trace(body_preview)

        print(f"[{i:02d}/{len(proposals):02d}] {space} — {title[:60]}")
        print(f"        State: {state}  |  Amount: ${amount:,.2f}  |  Type: {payment_type}")

        try:
            verdict = guard.pay(
                vendor=vendor,
                amount=amount,
                payment_type=payment_type,
                reasoning_trace=reasoning_trace,
            )
            decision = "APPROVED"
            coherence = verdict.coherence_score
            reasoning = verdict.reasoning
            risk_signals = []
            print(f"        → APPROVED  (coherence={coherence:.2f})")
            print(f"          {reasoning}")

        except PaymentBlocked as e:
            decision = "BLOCKED"
            coherence = e.verdict.coherence_score
            reasoning = e.verdict.reasoning
            risk_signals = e.verdict.risk_signals
            print(f"        → BLOCKED   (coherence={coherence:.2f})")
            print(f"          {reasoning}")
            if risk_signals:
                print(f"          Signals: {risk_signals}")

        print()

        results.append({
            "proposal_id": proposal.get("id"),
            "title": title,
            "space": space,
            "state": state,
            "vendor": vendor,
            "amount": amount,
            "payment_type": payment_type,
            "decision": decision,
            "coherence_score": coherence,
            "reasoning": reasoning,
            "risk_signals": risk_signals,
        })

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    approved = [r for r in results if r["decision"] == "APPROVED"]
    blocked  = [r for r in results if r["decision"] == "BLOCKED"]

    print("=" * 70)
    print("  SUMMARY")
    print("=" * 70)
    print(f"  Total proposals tested: {len(results)}")
    print(f"  Approved:               {len(approved)}")
    print(f"  Blocked:                {len(blocked)}")

    if blocked:
        print("\n  Blocked proposals (may indicate classifier false positives or")
        print("  genuine policy mismatches for real-world DAO content):\n")
        for r in blocked:
            print(f"    [{r['space']}] {r['title'][:55]}")
            print(f"      Coherence: {r['coherence_score']:.2f}  |  {r['reasoning']}")
            if r["risk_signals"]:
                print(f"      Signals: {r['risk_signals']}")
            print()

    if approved:
        print("\n  Approved proposals:\n")
        for r in approved:
            print(f"    [{r['space']}] {r['title'][:55]}")
            print(f"      Coherence: {r['coherence_score']:.2f}  |  ${r['amount']:,.2f} {r['payment_type']}")
            print()

    print("=" * 70)


if __name__ == "__main__":
    main()
