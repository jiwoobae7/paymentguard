from __future__ import annotations
import json
import anthropic
from payment_guard import PaymentGuard, PaymentBlocked


SYSTEM_PROMPT = """You are a procurement agent. Your job is to complete purchasing
tasks on behalf of the company. When you are ready to make a payment, call the `pay`
tool. Think step by step and show your reasoning before calling the tool."""

PAY_TOOL = {
    "name": "pay",
    "description": "Execute a payment to a vendor.",
    "input_schema": {
        "type": "object",
        "properties": {
            "vendor":       {"type": "string", "description": "Vendor name"},
            "amount":       {"type": "number", "description": "Amount in USD"},
            "payment_type": {"type": "string", "enum": ["card", "ach", "wire"]},
            "account_hint": {"type": "string", "description": "Last 4 digits or account ref (optional)"},
        },
        "required": ["vendor", "amount", "payment_type"],
    },
}


def run_agent(task: str, guard: PaymentGuard) -> dict:
    """
    Run the procurement agent on a task.
    Returns a result dict with decision info.
    """
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": task}]
    reasoning_trace: list[str] = []

    print(f"\n[Agent] Task: {task}\n")

    while True:
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=[PAY_TOOL],
            messages=messages,
        )

        # collect any text reasoning before tool use
        for block in response.content:
            if block.type == "text" and block.text.strip():
                print(f"[Agent reasoning] {block.text.strip()}")
                reasoning_trace.append(block.text.strip())

        if response.stop_reason == "end_turn":
            return {"status": "completed_without_payment", "trace": reasoning_trace}

        if response.stop_reason != "tool_use":
            return {"status": "unexpected_stop", "reason": response.stop_reason}

        # find the pay tool call
        tool_call = next(
            (b for b in response.content if b.type == "tool_use" and b.name == "pay"),
            None,
        )
        if tool_call is None:
            return {"status": "no_pay_call"}

        params = tool_call.input
        print(f"\n[Agent] Requesting payment → {json.dumps(params, indent=2)}")

        try:
            verdict = guard.pay(
                vendor=params["vendor"],
                amount=params["amount"],
                payment_type=params["payment_type"],
                account_hint=params.get("account_hint"),
                reasoning_trace=reasoning_trace,
            )
            print(f"\n[PaymentGuard] APPROVED (coherence={verdict.coherence_score:.2f})")
            print(f"  Reasoning: {verdict.reasoning}")
            print(f"  Audit ID:  {verdict.audit_id}")
            return {"status": "approved", "verdict": verdict.model_dump()}

        except PaymentBlocked as e:
            print(f"\n[PaymentGuard] BLOCKED")
            print(f"  Reason:        {e.verdict.reasoning}")
            print(f"  Risk signals:  {e.verdict.risk_signals}")
            print(f"  Coherence:     {e.verdict.coherence_score:.2f}")
            print(f"  Audit ID:      {e.verdict.audit_id}")
            return {"status": "blocked", "verdict": e.verdict.model_dump()}
