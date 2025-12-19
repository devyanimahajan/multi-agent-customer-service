# agents/data_agent_server.py
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from a2a.types import AgentCapabilities, AgentCard, AgentSkill

HOST = os.environ.get("DATA_HOST", "127.0.0.1")
PORT = int(os.environ.get("DATA_PORT", "8001"))
BASE_URL = f"http://{HOST}:{PORT}"

MCP_PY = os.environ.get("MCP_SERVER_PY", "mcp_server.py")  # relative to repo cwd


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def build_card() -> AgentCard:
    return AgentCard(
        name="Customer Data Agent",
        description="Accesses customer database via MCP: get/list/update customers, create tickets, fetch ticket history.",
        url=BASE_URL,
        version="1.0.0",
        defaultInputModes=["text"],
        defaultOutputModes=["text"],
        capabilities=AgentCapabilities(streaming=False),
        skills=[
            AgentSkill(
                id="mcp_customer_tools",
                name="Customer data tools (MCP)",
                description="Uses MCP tools: get_customer, list_customers, update_customer, create_ticket, get_customer_history.",
                tags=["data", "mcp", "customers", "tickets"],
                examples=[
                    "Get customer information for ID 5",
                    "List active customers",
                    "Update customer 5 email to new@email.com",
                    "Show ticket history for customer 5",
                ],
            )
        ],
    )


CARD = build_card()


class MCPClient:
    """
    Minimal stdio MCP client for your mcp_server.py (JSON lines in/out).
    Keeps one subprocess alive for the notebook session.
    """
    def __init__(self, script_path: str):
        self.script_path = script_path
        self.proc: Optional[subprocess.Popen] = None
        self.lock = asyncio.Lock()

    def ensure_started(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            return
        self.proc = subprocess.Popen(
            [os.environ.get("PYTHON", "python"), self.script_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        async with self.lock:
            self.ensure_started()
            assert self.proc is not None
            assert self.proc.stdin is not None
            assert self.proc.stdout is not None

            req_id = str(uuid.uuid4())
            req = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()

            # Read one response line
            line = await asyncio.to_thread(self.proc.stdout.readline)
            if not line:
                raise RuntimeError("MCP server produced no output (process may have died).")
            resp = json.loads(line)

            if "error" in resp:
                raise RuntimeError(f"MCP error: {resp['error']}")
            return resp.get("result", {})


mcp = MCPClient(MCP_PY)


def extract_customer_id(text: str) -> Optional[int]:
    m = re.search(r"\bcustomer\s*(?:id)?\s*[:#]?\s*(\d+)\b", text, re.I)
    if m:
        return int(m.group(1))
    m2 = re.search(r"\bID\s*(\d+)\b", text, re.I)
    if m2:
        return int(m2.group(1))
    return None


def extract_email_update(text: str) -> Optional[str]:
    m = re.search(r"([\w\.\+\-]+@[\w\.\-]+\.\w+)", text)
    return m.group(1) if m else None


async def handle_query(user_text: str) -> str:
    t = (user_text or "").strip()
    t_lower = t.lower()

    # Tool: list_customers(status, limit)
    if "list" in t_lower and "customer" in t_lower and ("active" in t_lower or "disabled" in t_lower):
        status = "active" if "active" in t_lower else "disabled"
        out = await mcp.call_tool("list_customers", {"status": status, "limit": 50})
        return json.dumps(out.get("content", out), indent=2)

    cid = extract_customer_id(t)
    if cid is None:
        return (
            "Data Agent: tell me what you need, e.g.:\n"
            "- 'Get customer information for ID 5'\n"
            "- 'List active customers'\n"
            "- 'Update my email to new@email.com for customer ID 5'\n"
            "- 'Show ticket history for customer ID 5'"
        )

    # Tool: update_customer(customer_id, data)
    new_email = extract_email_update(t)
    if ("update" in t_lower or "change" in t_lower) and new_email:
        out = await mcp.call_tool("update_customer", {"customer_id": cid, "data": {"email": new_email}})
        return f"Updated customer {cid}: {out.get('content', out)}"

    # Tool: get_customer_history(customer_id)
    if "history" in t_lower or ("ticket" in t_lower and "show" in t_lower):
        out = await mcp.call_tool("get_customer_history", {"customer_id": cid})
        return json.dumps(out.get("content", out), indent=2)

    # Tool: get_customer(customer_id)
    out = await mcp.call_tool("get_customer", {"customer_id": cid})
    return json.dumps(out.get("content", out), indent=2)


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

    reply_text = await handle_query(user_text)

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
