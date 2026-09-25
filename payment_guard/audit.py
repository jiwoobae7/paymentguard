from __future__ import annotations
import hashlib
import hmac
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from .models import PaymentIntent, IntentVerdict


DB_PATH = Path(__file__).parent.parent / "db" / "audit.db"


def _get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id    TEXT PRIMARY KEY,
            timestamp   TEXT NOT NULL,
            agent_id    TEXT NOT NULL,
            decision    TEXT NOT NULL,
            vendor      TEXT NOT NULL,
            amount      REAL NOT NULL,
            payment_type TEXT NOT NULL,
            coherence_score REAL NOT NULL,
            risk_signals TEXT NOT NULL,
            reasoning   TEXT NOT NULL,
            task_context TEXT NOT NULL,
            reasoning_trace TEXT NOT NULL,
            stripe_payment_intent_id TEXT,
            signature   TEXT NOT NULL
        )
    """)
    # migrate existing DBs that predate the stripe column
    cols = {r[1] for r in conn.execute("PRAGMA table_info(audit_log)").fetchall()}
    if "stripe_payment_intent_id" not in cols:
        conn.execute("ALTER TABLE audit_log ADD COLUMN stripe_payment_intent_id TEXT")
    conn.commit()
    return conn


def _sign(payload: str) -> str:
    secret = os.getenv("HMAC_SECRET", "dev-secret").encode()
    return hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()


class AuditLog:
    def record(self, intent: PaymentIntent, verdict: IntentVerdict) -> str:
        timestamp = datetime.now(timezone.utc).isoformat()

        # canonical payload for signing — deterministic ordering
        payload = json.dumps({
            "audit_id": verdict.audit_id,
            "timestamp": timestamp,
            "agent_id": intent.agent_id,
            "decision": verdict.decision,
            "vendor": intent.payment.vendor,
            "amount": intent.payment.amount,
            "payment_type": intent.payment.payment_type,
        }, sort_keys=True)

        signature = _sign(payload)

        conn = _get_conn()
        conn.execute(
            """INSERT INTO audit_log
               (audit_id, timestamp, agent_id, decision, vendor, amount,
                payment_type, coherence_score, risk_signals, reasoning,
                task_context, reasoning_trace, stripe_payment_intent_id, signature)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                verdict.audit_id,
                timestamp,
                intent.agent_id,
                verdict.decision,
                intent.payment.vendor,
                intent.payment.amount,
                intent.payment.payment_type,
                verdict.coherence_score,
                json.dumps(verdict.risk_signals),
                verdict.reasoning,
                intent.task_context,
                json.dumps(intent.reasoning_trace),
                verdict.stripe_payment_intent_id,
                signature,
            ),
        )
        conn.commit()
        conn.close()
        return verdict.audit_id

    def fetch_all(self) -> list[dict]:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM audit_log ORDER BY timestamp DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
