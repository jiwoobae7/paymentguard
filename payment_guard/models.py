from __future__ import annotations
from typing import List, Literal, Optional
from pydantic import BaseModel, Field
from uuid import uuid4


class PaymentPolicy(BaseModel):
    agent_id: str
    task_description: str
    allowed_vendors: List[str]
    allowed_categories: List[str]
    max_amount_usd: float
    allow_wire_transfers: bool = False
    require_known_counterparty: bool = True


class PaymentRequest(BaseModel):
    vendor: str
    amount: float
    payment_type: Literal["card", "ach", "wire"]
    account_hint: Optional[str] = None  # last 4 digits or routing hint


class PaymentIntent(BaseModel):
    agent_id: str
    task_context: str
    reasoning_trace: List[str]
    payment: PaymentRequest


class IntentVerdict(BaseModel):
    decision: Literal["APPROVE", "BLOCK"]
    coherence_score: float = Field(ge=0.0, le=1.0)
    risk_signals: List[str]
    reasoning: str
    audit_id: str = Field(default_factory=lambda: str(uuid4()))
    stripe_payment_intent_id: Optional[str] = None  # set after real Stripe execution
