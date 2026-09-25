"""
PaymentGuard REST API

POST /verify   — verify a payment intent before execution
GET  /health   — liveness check

Auth: X-Api-Key header (set API_KEYS env var as comma-separated list)

Deploy to Railway:
    1. Push to GitHub
    2. New project → Deploy from GitHub repo
    3. Set env vars: ANTHROPIC_API_KEY, HMAC_SECRET, API_KEYS
    4. Railway auto-detects Procfile and deploys
"""
from __future__ import annotations
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "../.env"))

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional
import anthropic

from payment_guard import PaymentGuard, PaymentBlocked, PaymentPolicy, IntentClassifier, AuditLog

# ── Auth ──────────────────────────────────────────────────────────────────────
_VALID_KEYS = set(k.strip() for k in os.getenv("API_KEYS", "dev-key").split(",") if k.strip())

def _check_key(key: str | None):
    if not key or key not in _VALID_KEYS:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Api-Key")


# ── Shared Anthropic client ───────────────────────────────────────────────────
_client    = anthropic.Anthropic()
_classifier = IntentClassifier(_client)
_audit     = AuditLog()


# ── Request / response schemas ────────────────────────────────────────────────
class PolicyIn(BaseModel):
    agent_id:                 str
    task_description:         str
    allowed_vendors:          list[str]
    allowed_categories:       list[str]
    max_amount_usd:           float
    allow_wire_transfers:     bool = False
    require_known_counterparty: bool = True


class VerifyRequest(BaseModel):
    policy:          PolicyIn
    reasoning_trace: list[str]
    vendor:          str
    amount:          float
    payment_type:    str           # "card" | "ach" | "wire"
    account_hint:    Optional[str] = None


class VerifyResponse(BaseModel):
    decision:        str           # "APPROVE" | "BLOCK"
    coherence_score: float
    risk_signals:    list[str]
    reasoning:       str
    audit_id:        str


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="PaymentGuard API",
    description=(
        "Semantic verification layer for AI agent payments. "
        "Detects prompt injection and reasoning discontinuities "
        "before payment execution."
    ),
    version="0.1.0",
)


@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}


@app.post("/verify", response_model=VerifyResponse)
def verify(req: VerifyRequest, x_api_key: str = Header(default=None)):
    _check_key(x_api_key)

    policy = PaymentPolicy(**req.policy.model_dump())
    guard  = PaymentGuard(
        policy=policy,
        classifier=_classifier,
        audit=_audit,
        mock_execution=True,   # API layer never executes — caller does after approval
    )

    try:
        verdict = guard.pay(
            vendor=req.vendor,
            amount=req.amount,
            payment_type=req.payment_type,
            account_hint=req.account_hint,
            reasoning_trace=req.reasoning_trace,
        )
        return VerifyResponse(
            decision=verdict.decision,
            coherence_score=verdict.coherence_score,
            risk_signals=verdict.risk_signals,
            reasoning=verdict.reasoning,
            audit_id=verdict.audit_id,
        )
    except PaymentBlocked as e:
        v = e.verdict
        return VerifyResponse(
            decision=v.decision,
            coherence_score=v.coherence_score,
            risk_signals=v.risk_signals,
            reasoning=v.reasoning,
            audit_id=v.audit_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.exception_handler(Exception)
async def global_handler(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": str(exc)})
