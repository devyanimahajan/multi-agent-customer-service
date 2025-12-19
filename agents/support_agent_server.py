# agents/support_agent_server.py
from __future__ import annotations

import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from a2a.types import AgentCapabilities, AgentCard, AgentSkill

HOST = os.environ.get("SUPPORT_HOST", "127.0.0.1")
PORT = int(os.environ.get("SUPPORT_PORT", "8002"))
BASE_URL = f"http://{HOST}:{PORT}"


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def build_card() -> AgentCard:
    return AgentCard(
        name="Support Agent",
        description="Handles general support queries (refunds, shipping, cancellations) and escalates with structured next steps.",
        url=BASE_URL,
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="support_triage",
                name="Support triage",
                description="Triage refunds/shipping/cancellations/billing issues and propose next steps.",
                tags=["support", "triage"],
                examples=[
                    "I've been charged twice, please refund immediately!",
                    "I want to cancel my subscription but I'm having billing issues",
                    "Where is my package?",
                ],
            )
        ],
    )


CARD = build_card()


def extract_customer_id(text: str) -> Optional[int]:
    m = re.search(r"\bcustomer\s*(?:id)?\s*[:#]?\s*(\d+)\b", text, re.I)
    return int(m.group(1)) if m else None


async def handle_support(user_text: str) -> str:
    t = (user_text or "").strip()
    t_lower = t.lower()
    cid = extract_customer_id(t)

    urgent = any(w in t_lower for w in ["charged twice", "fraud", "immediately", "urgent", "refund now"])
    cancel = any(w in t_lower for w in ["cancel", "cancellation", "subscription"])
    billing = any(w in t_lower for w in ["billing", "charge", "charged", "invoice", "payment"])
    shipping = any(w in t_lower for w in ["shipping", "package", "delivery", "tracking"])

    lines = []
    lines.append(f"Support Agent (triage) [{now_iso()}]")

    if urgent:
        lines.append("I hear this is urgent. I can help you quickly.")
        lines.append("Next steps:")
        lines.append("1) Confirm the order or subscription identifier (if you have it).")
        lines.append("2) Confirm the dates and amounts of the duplicate charges.")
        lines.append("3) I will initiate a refund request and escalation to billing support.")
    elif cancel and billing:
        lines.append("It sounds like you have two issues: cancellation and billing.")
        lines.append("Next steps:")
        lines.append("1) Confirm what you want cancelled (plan name or order).")
        lines.append("2) Tell me what billing issue you see (unexpected charge, failed payment, etc.).")
        lines.append("3) Once I have your details, I’ll propose a resolution path.")
    elif cancel:
        lines.append("I can help with cancellation.")
        lines.append("Next steps: confirm what you want cancelled and the effective date you prefer.")
    elif shipping:
        lines.append("I can help with shipping and delivery.")
        lines.append("Next steps: share a tracking number or order id if available.")
    else:
        lines.append("Tell me what happened (refund, shipping, cancellation, damaged item, billing).")
        if cid is not None:
            lines.append(f"I see customer ID {cid} in your message. If you want, also include an order id.")

    return "\n".join(lines)


async def agent_card_json(_request: Request) -> JSONResponse:
    return JSONResponse(CARD.model_dump())


async def rpc_root(request: Request) -> JSONResponse:
    try:
        req = await request.json()
    except Exception:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}, status_code=400)

    jsonrpc = req.get("jsonrpc", "2.0")
    req_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    if method != "message/send":
        return JSONResponse({"jsonrpc": jsonrpc, "id": req_id, "error": {"code": -32601, "message": "Method not found"}})

    msg = params.get("message") or {}
    parts = msg.get("parts") or []
    user_text = ""
    for p in parts:
        if isinstance(p, dict) and p.get("kind") == "text":
            user_text += p.get("text", "")
        elif isinstance(p, dict) and "text" in p:
            user_text += p["text"]

    reply_text = await handle_support(user_text)

    result = {
        "kind": "message",
        "role": "agent",
        "messageId": str(uuid.uuid4()),
        "parts": [{"kind": "text", "text": reply_text}],
    }
    return JSONResponse({"jsonrpc": jsonrpc, "id": req_id, "result": result})


routes = [
    Route("/", rpc_root, methods=["POST"]),
    Route("/.well-known/agent-card.json", agent_card_json, methods=["GET"]),
]

app = Starlette(routes=routes)
