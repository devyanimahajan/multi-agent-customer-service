# agents/executors.py
from __future__ import annotations

import re
from dataclasses import dataclass

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.utils import new_agent_text_message


def _last_user_text(context: RequestContext) -> str:
    # context.messages is part of the SDK RequestContext; we keep this defensive
    msgs = getattr(context, "messages", []) or []
    for m in reversed(msgs):
        role = getattr(m, "role", None)
        if str(role) == "Role.user" or str(role) == "user":
            parts = getattr(m, "parts", []) or []
            for p in parts:
                root = getattr(p, "root", p)
                text = getattr(root, "text", None)
                if isinstance(text, str) and text.strip():
                    return text.strip()
    return ""


def _extract_customer_id(text: str) -> str | None:
    m = re.search(r"\bCUST[-_ ]?(\d{3,})\b", text, flags=re.I)
    if m:
        return f"CUST-{m.group(1)}"
    m = re.search(r"\bcustomer\s*id[: ]+(\d{3,})\b", text, flags=re.I)
    if m:
        return m.group(1)
    return None


def _extract_ticket_id(text: str) -> str | None:
    m = re.search(r"\bTICK(?:ET)?[-_ ]?(\d{3,})\b", text, flags=re.I)
    if m:
        return f"TICK-{m.group(1)}"
    m = re.search(r"\bticket\s*id[: ]+(\d{3,})\b", text, flags=re.I)
    if m:
        return m.group(1)
    return None


@dataclass
class SimpleDataAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_text = _last_user_text(context)
        cust = _extract_customer_id(user_text)
        tick = _extract_ticket_id(user_text)

        if cust and tick:
            reply = f"Found customer id {cust} and ticket id {tick}. (stub lookup)"
        elif cust:
            reply = f"Found customer id {cust}. (stub lookup)"
        elif tick:
            reply = f"Found ticket id {tick}. (stub lookup)"
        else:
            reply = (
                "I can help with customer or ticket lookups. "
                "Send something like: 'customer id 12345' or 'TICK-9001'."
            )

        await event_queue.enqueue_event(new_agent_text_message(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


@dataclass
class SimpleSupportAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_text = _last_user_text(context).lower()

        if "refund" in user_text:
            reply = (
                "Refunds: please share your order id and reason. "
                "If it is within the return window, I can help start the process."
            )
        elif "shipping" in user_text or "delivery" in user_text:
            reply = "Shipping: share your order id and ZIP/postcode and I will check status."
        elif "cancel" in user_text:
            reply = "Cancellation: share your order id. If it has not shipped, cancellation is usually possible."
        else:
            reply = (
                "Support: tell me what happened (refund, shipping, cancellation, damaged item). "
                "Include an order id if you have one."
            )

        await event_queue.enqueue_event(new_agent_text_message(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")


@dataclass
class SimpleRouterAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        user_text = _last_user_text(context).lower()

        if any(k in user_text for k in ["cust", "customer", "ticket", "tick-"]):
            reply = "Routing: this looks like a data lookup. Please use the Customer Data Agent."
        elif any(k in user_text for k in ["refund", "shipping", "delivery", "cancel", "return", "damaged"]):
            reply = "Routing: this looks like customer support. Please use the Support Agent."
        else:
            reply = (
                "Routing: I can route you to (1) Customer Data Agent for customer/ticket lookups "
                "or (2) Support Agent for refunds/shipping/cancellations. What do you need?"
            )

        await event_queue.enqueue_event(new_agent_text_message(reply))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise Exception("cancel not supported")
