from __future__ import annotations
import os
from typing import List
import stripe
from .classifier import IntentClassifier
from .audit import AuditLog
from .models import PaymentPolicy, PaymentRequest, PaymentIntent, IntentVerdict


class PaymentBlocked(Exception):
    def __init__(self, verdict: IntentVerdict):
        self.verdict = verdict
        super().__init__(
            f"Payment blocked (coherence={verdict.coherence_score:.2f}): "
            f"{verdict.reasoning}"
        )


class PaymentGuard:
    def __init__(
        self,
        policy: PaymentPolicy,
        classifier: IntentClassifier,
        audit: AuditLog,
        mock_execution: bool = True,   # flip to False for real Stripe calls
    ):
        self.policy = policy
        self.classifier = classifier
        self.audit = audit
        self.mock_execution = mock_execution

    def pay(
        self,
        vendor: str,
        amount: float,
        payment_type: str,
        reasoning_trace: List[str],
        account_hint: str | None = None,
    ) -> IntentVerdict:
        intent = PaymentIntent(
            agent_id=self.policy.agent_id,
            task_context=self.policy.task_description,
            reasoning_trace=reasoning_trace,
            payment=PaymentRequest(
                vendor=vendor,
                amount=amount,
                payment_type=payment_type,
                account_hint=account_hint,
            ),
        )

        verdict = self.classifier.evaluate(intent, self.policy)

        if verdict.decision == "BLOCK":
            self.audit.record(intent, verdict)
            raise PaymentBlocked(verdict)

        if not self.mock_execution:
            stripe_pi_id = self._execute_stripe(intent.payment, intent.agent_id, verdict.audit_id)
            verdict.stripe_payment_intent_id = stripe_pi_id

        self.audit.record(intent, verdict)
        return verdict

    def _execute_stripe(self, payment: PaymentRequest, agent_id: str, audit_id: str) -> str:
        """
        Create a real Stripe PaymentIntent in test mode.
        Returns the Stripe PaymentIntent ID (pi_...).

        Payment type routing:
          card  → standard card PaymentIntent
          ach   → us_bank_account PaymentIntent (requires bank account attachment to confirm)
          wire  → us_bank_account PaymentIntent with wire metadata
                  (production: routes via Stripe Treasury; test: creates the PI object)
        """
        stripe.api_key = os.environ["STRIPE_SECRET_KEY"]

        # Map payment types to Stripe method families
        if payment.payment_type == "card":
            method_types = ["card"]
        else:
            # ACH and wire both use bank account rails in Stripe
            method_types = ["us_bank_account"]

        pi = stripe.PaymentIntent.create(
            amount=int(payment.amount * 100),  # Stripe expects cents
            currency="usd",
            payment_method_types=method_types,
            metadata={
                "vendor":       payment.vendor,
                "payment_type": payment.payment_type,
                "agent_id":     agent_id,
                "audit_id":     audit_id,
                **({"account_hint": payment.account_hint} if payment.account_hint else {}),
            },
            description=f"PaymentGuard approved payment to {payment.vendor}",
        )
        return pi["id"]
