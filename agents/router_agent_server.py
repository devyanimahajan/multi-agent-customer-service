# agents/router_agent_server.py
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from a2a.types import AgentCapabilities, AgentCard, AgentSkill

HOST = os.environ.get("ROUTER_HOST", "127.0.0.1")
PORT = int(os.environ.get("ROUTER_PORT", "8003"))
BASE_URL = f"http://{HOST}:{PORT}"

DATA_URL = os.environ.get("DATA_URL", "http://127.0.0.1:8001")
SUPPORT_URL = os.environ.get("SUPPORT_URL", "http://127.0.0.1:8002")

# Your notebook uses RPC path "/"
DATA_RPC_PATH = os.environ.get("DATA_RPC_PATH", "/")
SUPPORT_RPC_PATH = os.environ.get("SUPPORT_RPC_PATH", "/")
ROUTER_RPC_PATH = os.environ.get("ROUTER_RPC_PATH", "/")


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def build_card() -> AgentCard:
    return AgentCard(
        name="Router Agent",
        description="Routes customer queries to Data Agent and Support Agent using A2A JSON-RPC and coordinates multi-step answers.",
        url=BASE_URL,
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="route_and_coordinate",
                name="Route and coordinate",
                description="Detects intent and coordinates Data and Support agents to answer multi-intent and multi-step queries.",
                tags=["router", "a2a", "coordination"],
                examples=[
                    "I need help with my account, customer ID 12345",
                    "I want to cancel my subscription but I'm having billing issues",
                    "Show me all active customers who have open tickets",
                ],
            )
        ],
    )


CARD = build_card()


def extract_customer_id(text: str) -> Optional[int]:
    m = re.search(r"\bcustomer\s*(?:id)?\s*[:#]?\s*(\d+)\b", text, re.I)
    if m:
        return int(m.group(1))
    m2 = re.search(r"\bID\s*(\d+)\b", text, re.I)
    return int(m2.group(1)) if m2 else None


def detect_intent(text: str) -> Dict[str, bool]:
    t = (text or "").lower()
    return {
        "has_customer_id": extract_customer_id(text) is not None,
        "account_help": any(k in t for k in ["account", "upgrade", "login", "access"]),
        "cancel": any(k in t for k in ["cancel", "cancellation", "subscription"]),
        "billing": any(k in t for k in ["billing", "charge", "charged", "invoice", "payment"]),
        "list_active_open": ("active customers" in t and ("open ticket" in t or "open tickets" in t)),
    }


async def a2a_send(base_url: str, rpc_path: str, user_text: str, timeout_s: float = 15.0) -> Dict[str, Any]:
    """
    Sends JSON-RPC message/send to POST {base_url}{rpc_path}.
    Returns raw JSON response.
    """
    url = base_url.rstrip("/") + (rpc_path if rpc_path.startswith("/") else f"/{rpc_path}")
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "kind": "message",
                "role": "user",
                "parts": [{"kind": "text", "text": user_text}],
            }
        },
    }
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        return r.json()


def extract_text_from_a2a(resp: Dict[str, Any]) -> str:
    if "result" not in resp:
        return json.dumps(resp)
    parts = resp["result"].get("parts", []) or []
    texts = []
    for p in parts:
        if isinstance(p, dict) and p.get("kind") == "text":
            texts.append(p.get("text", ""))
        elif isinstance(p, dict) and "text" in p:
            texts.append(p["text"])
    return "\n".join([t for t in texts if t]).strip() or json.dumps(resp)


async def handle_router(user_text: str) -> str:
    """
    Deterministic routing for your assignment demos:
    - Scenario 1: customer ID + account help -> Data then Support then synthesize
    - Scenario 2: cancel + billing -> Support, and if customer id present then also Data context
    - Scenario 3: active customers with open tickets -> multi-step via Data, plus Support to format
    """
    intent = detect_intent(user_text)
    cid = extract_customer_id(user_text)
    log_lines = []
    log_lines.append(f"[router] time={now_iso()}")
    log_lines.append(f"[router] DATA_URL={DATA_URL}{DATA_RPC_PATH} SUPPORT_URL={SUPPORT_URL}{SUPPORT_RPC_PATH}")

    # Helper to call agents and always log
    async def call_agent(label: str, url: str, path: str, msg: str) -> Tuple[str, str]:
        try:
            resp = await a2a_send(url, path, msg)
            text = extract_text_from_a2a(resp)
            log_lines.append(f"[router] {label} call OK: {msg[:120]!r}")
            return "OK", text
        except Exception as e:
            log_lines.append(f"[router] {label} call FAIL: {type(e).__name__}: {e}")
            return "FAIL", f"{label} call failed: {type(e).__name__}: {e}"

    # Scenario 3: "Show me all active customers who have open tickets"
    if intent["list_active_open"]:
        # Step 1: ask Data for active customers list via MCP
        status1, active_text = await call_agent(
            "DATA",
            DATA_URL,
            DATA_RPC_PATH,
            "List active customers"
        )

        # Parse JSON if possible
        active_ids = []
        try:
            active_json = json.loads(active_text)
            customers = active_json.get("customers") or active_json.get("content", {}).get("customers") or []
            for c in customers:
                if isinstance(c, dict) and "id" in c:
                    active_ids.append(int(c["id"]))
        except Exception:
            customers = None

        # Step 2: for each customer, get ticket history and filter open tickets (best-effort)
        open_hits = []
        if active_ids:
            for c_id in active_ids[:15]:  # keep demo fast
                status2, hist_text = await call_agent(
                    "DATA",
                    DATA_URL,
                    DATA_RPC_PATH,
                    f"Show ticket history for customer ID {c_id}"
                )
                try:
                    hist_json = json.loads(hist_text)
                    tickets = hist_json.get("tickets") or hist_json.get("content", {}).get("tickets") or []
                    for t in tickets:
                        if isinstance(t, dict) and t.get("status") == "open":
                            open_hits.append(
                                {"customer_id": c_id, "ticket_id": t.get("id"), "priority": t.get("priority"), "issue": t.get("issue")}
                            )
                except Exception:
                    continue

        # Step 3: ask Support to present the report
        status3, support_text = await call_agent(
            "SUPPORT",
            SUPPORT_URL,
            SUPPORT_RPC_PATH,
            "You are formatting a report, not triaging. Return ONLY a markdown table with columns: customer_id, ticket_id, priority, issue. No other text."
        )

        

        report = {
            "active_customer_ids_checked": active_ids[:15],
            "open_tickets_found": open_hits,
        }

        final = []
        final.append("Coordinated answer (Router -> Data multi-step, plus Support for formatting):")
        final.append(json.dumps(report, indent=2))
        final.append("")
        final.append("Support formatting guidance:")
        final.append(support_text)
        final.append("")
        final.append("A2A routing log:")
        final.extend(log_lines)
        return "\n".join(final)

    # Scenario 2: cancellation + billing issues
    if intent["cancel"] and intent["billing"]:
        # Ask Support first
        status_s, support_text = await call_agent(
            "SUPPORT",
            SUPPORT_URL,
            SUPPORT_RPC_PATH,
            user_text
        )

        # If customer id present, fetch context
        data_text = ""
        if cid is not None:
            status_d, data_text = await call_agent(
                "DATA",
                DATA_URL,
                DATA_RPC_PATH,
                f"Get customer information for ID {cid}"
            )

        final = []
        final.append("Coordinated answer (Router -> Support, optionally Data context):")
        if data_text:
            final.append("Customer context from Data Agent:")
            final.append(data_text)
            final.append("")
        final.append("Support Agent response:")
        final.append(support_text)
        final.append("")
        final.append("A2A routing log:")
        final.extend(log_lines)
        return "\n".join(final)

    # Scenario 1: account help with customer ID
    if intent["has_customer_id"] and (intent["account_help"] or "help" in (user_text or "").lower()):
        # Step 1: Data context
        status_d, data_text = await call_agent(
            "DATA",
            DATA_URL,
            DATA_RPC_PATH,
            f"Get customer information for ID {cid}"
        )
        # Step 2: Support advice using the context (we pass it as text)
        status_s, support_text = await call_agent(
            "SUPPORT",
            SUPPORT_URL,
            SUPPORT_RPC_PATH,
            f"User asked: {user_text}\n\nCustomer context:\n{data_text}\n\nPlease provide next steps."
        )

        final = []
        final.append("Coordinated answer (Router -> Data then Support):")
        final.append("Customer context from Data Agent:")
        final.append(data_text)
        final.append("")
        final.append("Support Agent response:")
        final.append(support_text)
        final.append("")
        final.append("A2A routing log:")
        final.extend(log_lines)
        return "\n".join(final)

    # Default: pick best single agent
    if intent["has_customer_id"]:
        status_d, data_text = await call_agent("DATA", DATA_URL, DATA_RPC_PATH, user_text)
        return "\n".join(
            ["Routed to Data Agent:", data_text, "", "A2A routing log:"] + log_lines
        )

    status_s, support_text = await call_agent("SUPPORT", SUPPORT_URL, SUPPORT_RPC_PATH, user_text)
    return "\n".join(
        ["Routed to Support Agent:", support_text, "", "A2A routing log:"] + log_lines
    )


async def agent_card_json(_request: Request) -> JSONResponse:
    return JSONResponse(CARD.model_dump())


async def rpc_root(request: Request) -> JSONResponse:
    try:
        req = await request.json()
    except Exception:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )

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

    reply_text = await handle_router(user_text)

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
