# agents/router_agent_server.py
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

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

# Notebook uses RPC path "/"
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
                    "I need help with my account, customer ID 5",
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
        "account_help": any(k in t for k in ["account", "upgrade", "login", "access", "help"]),
        "cancel": any(k in t for k in ["cancel", "cancellation", "subscription"]),
        "billing": any(k in t for k in ["billing", "charge", "charged", "invoice", "payment"]),
        "list_active_open": ("active customers" in t and ("open ticket" in t or "open tickets" in t)),
    }


def _join_url(base_url: str, rpc_path: str) -> str:
    # base_url may be "http://127.0.0.1:8001" (no trailing slash)
    # rpc_path in this repo is "/" (root)
    base = base_url.rstrip("/")
    path = rpc_path if rpc_path.startswith("/") else f"/{rpc_path}"
    return base + path


async def a2a_send(base_url: str, rpc_path: str, user_text: str, timeout_s: float = 20.0) -> Dict[str, Any]:
    """
    Sends JSON-RPC message/send to POST {base_url}{rpc_path}.
    """
    url = _join_url(base_url, rpc_path)
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
        return json.dumps(resp, indent=2)
    parts = resp["result"].get("parts", []) or []
    texts: List[str] = []
    for p in parts:
        if isinstance(p, dict) and p.get("kind") == "text":
            texts.append(p.get("text", ""))
        elif isinstance(p, dict) and "text" in p:
            texts.append(p["text"])
    out = "\n".join([t for t in texts if t]).strip()
    return out or json.dumps(resp, indent=2)


def _safe_json_loads(s: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(s)
    except Exception:
        return None


def _md_escape(text: str) -> str:
    # Minimal escape for markdown table cells
    return (text or "").replace("\n", " ").replace("|", "\\|").strip()


def _make_md_table(rows: List[Dict[str, Any]]) -> str:
    header = "| customer_id | customer_name | email | ticket_id | priority | status | issue |\n|---:|---|---|---:|---|---|---|"
    if not rows:
        return header + "\n| (none) | (none) | (none) | (none) | (none) | (none) | No open tickets found |"

    lines = [header]
    for r in rows:
        lines.append(
            "| {customer_id} | {customer_name} | {email} | {ticket_id} | {priority} | {status} | {issue} |".format(
                customer_id=r.get("customer_id", ""),
                customer_name=_md_escape(r.get("customer_name", "")),
                email=_md_escape(r.get("email", "")),
                ticket_id=r.get("ticket_id", ""),
                priority=_md_escape(str(r.get("priority", ""))),
                status=_md_escape(str(r.get("status", ""))),
                issue=_md_escape(r.get("issue", "")),
            )
        )
    return "\n".join(lines)


async def handle_router(user_text: str) -> str:
    """
    Deterministic routing for your assignment demos:
    - Scenario 1: customer ID + account help -> Data then Support then synthesize
    - Scenario 2: cancel + billing -> Support, and if customer id present then also Data context
    - Scenario 3: active customers with open tickets -> multi-step via Data, Router formats table (no Support formatting)
    """
    intent = detect_intent(user_text)
    cid = extract_customer_id(user_text)

    # Short, readable log (for demo screenshots)
    log_short: List[str] = []
    log_short.append(f"[router] time={now_iso()}")
    log_short.append(f"[router] DATA={DATA_URL}{DATA_RPC_PATH} SUPPORT={SUPPORT_URL}{SUPPORT_RPC_PATH}")

    async def call_agent(label: str, url: str, path: str, msg: str) -> Tuple[bool, str]:
        try:
            resp = await a2a_send(url, path, msg)
            text = extract_text_from_a2a(resp)
            log_short.append(f"[router] {label} OK: {_md_escape(msg)[:90]}")
            return True, text
        except Exception as e:
            log_short.append(f"[router] {label} FAIL: {type(e).__name__}: {e}")
            return False, f"{label} call failed: {type(e).__name__}: {e}"

    # Scenario 3
    if intent["list_active_open"]:
        ok_active, active_text = await call_agent("DATA", DATA_URL, DATA_RPC_PATH, "List active customers")

        active_ids: List[int] = []
        customers_by_id: Dict[int, Dict[str, Any]] = {}

        active_json = _safe_json_loads(active_text)
        if active_json:
            # Data agent might return either {"customers":[...]} or {"content":{"customers":[...]}}
            customers = active_json.get("customers") or (active_json.get("content") or {}).get("customers") or []
            for c in customers:
                if isinstance(c, dict) and "id" in c:
                    c_id = int(c["id"])
                    active_ids.append(c_id)
                    customers_by_id[c_id] = c

        # Keep demo fast and stable
        active_ids = active_ids[:15]

        open_rows: List[Dict[str, Any]] = []

        # For each active customer, pull ticket history and filter status == "open"
        for c_id in active_ids:
            ok_hist, hist_text = await call_agent("DATA", DATA_URL, DATA_RPC_PATH, f"Show ticket history for customer ID {c_id}")
            hist_json = _safe_json_loads(hist_text)
            if not hist_json:
                continue

            tickets = hist_json.get("tickets") or (hist_json.get("content") or {}).get("tickets") or []
            for t in tickets:
                if not isinstance(t, dict):
                    continue
                if t.get("status") != "open":
                    continue

                cust = customers_by_id.get(c_id, {})
                open_rows.append(
                    {
                        "customer_id": c_id,
                        "customer_name": cust.get("name", ""),
                        "email": cust.get("email", ""),
                        "ticket_id": t.get("id", ""),
                        "priority": t.get("priority", ""),
                        "status": t.get("status", ""),
                        "issue": t.get("issue", ""),
                    }
                )

        # Sort: high -> medium -> low, then customer id
        priority_rank = {"high": 0, "medium": 1, "low": 2}
        open_rows.sort(key=lambda r: (priority_rank.get(str(r.get("priority", "")).lower(), 9), int(r.get("customer_id", 0))))

        table = _make_md_table(open_rows)

        final_lines: List[str] = []
        final_lines.append("Active customers with open tickets (coordinated via Router -> Data, multi-step):")
        final_lines.append("")
        final_lines.append(table)
        final_lines.append("")
        final_lines.append("A2A log (short):")
        final_lines.extend([f"- {x}" for x in log_short])
        return "\n".join(final_lines)

    # Scenario 2
    if intent["cancel"] and intent["billing"]:
        ok_s, support_text = await call_agent("SUPPORT", SUPPORT_URL, SUPPORT_RPC_PATH, user_text)

        data_text = ""
        if cid is not None:
            ok_d, data_text = await call_agent("DATA", DATA_URL, DATA_RPC_PATH, f"Get customer information for ID {cid}")

        final: List[str] = []
        final.append("Coordinated answer (Router -> Support, with Data context if available):")
        final.append("")
        if data_text:
            final.append("Customer context (Data Agent):")
            final.append(data_text)
            final.append("")
        final.append("Support response:")
        final.append(support_text)
        final.append("")
        final.append("A2A log (short):")
        final.extend([f"- {x}" for x in log_short])
        return "\n".join(final)

    # Scenario 1
    if intent["has_customer_id"] and intent["account_help"]:
        ok_d, data_text = await call_agent("DATA", DATA_URL, DATA_RPC_PATH, f"Get customer information for ID {cid}")
        ok_s, support_text = await call_agent(
            "SUPPORT",
            SUPPORT_URL,
            SUPPORT_RPC_PATH,
            f"User asked: {user_text}\n\nCustomer context:\n{data_text}\n\nPlease provide next steps.",
        )

        final: List[str] = []
        final.append("Coordinated answer (Router -> Data then Support):")
        final.append("")
        final.append("Customer context (Data Agent):")
        final.append(data_text)
        final.append("")
        final.append("Support response:")
        final.append(support_text)
        final.append("")
        final.append("A2A log (short):")
        final.extend([f"- {x}" for x in log_short])
        return "\n".join(final)

    # Default routing
    if intent["has_customer_id"]:
        ok_d, data_text = await call_agent("DATA", DATA_URL, DATA_RPC_PATH, user_text)
        return "\n".join(
            ["Routed to Data Agent:", "", data_text, "", "A2A log (short):"] + [f"- {x}" for x in log_short]
        )

    ok_s, support_text = await call_agent("SUPPORT", SUPPORT_URL, SUPPORT_RPC_PATH, user_text)
    return "\n".join(
        ["Routed to Support Agent:", "", support_text, "", "A2A log (short):"] + [f"- {x}" for x in log_short]
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
