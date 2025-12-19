# agents/executors.py
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import threading
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

# OpenAI (uses OPENAI_API_KEY + OPENAI_MODEL from env)
from openai import OpenAI


def _now_iso() -> str:
    # keep simple, no extra deps
    from datetime import datetime

    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _extract_user_text(message: Dict[str, Any]) -> str:
    """
    A2A messages in your servers are shaped like:
      {"kind":"message","role":"user","parts":[{"kind":"text","text":"..."}]}
    """
    parts = (message or {}).get("parts") or []
    out = []
    for p in parts:
        if isinstance(p, dict):
            if p.get("kind") == "text":
                out.append(p.get("text", ""))
            elif "text" in p:
                out.append(str(p.get("text", "")))
    return "\n".join([t for t in out if t]).strip()


def _a2a_text_response(text: str) -> Dict[str, Any]:
    return {
        "kind": "message",
        "role": "agent",
        "parts": [{"kind": "text", "text": text}],
    }


# ----------------------------
# OpenAI helper (matches your env vars)
# ----------------------------
def _openai_client() -> OpenAI:
    # OPENAI_API_KEY must be in env (your notebook sets it)
    return OpenAI()


def _llm_text(system: str, user: str, temperature: float = 0.2) -> str:
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    client = _openai_client()
    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


# ----------------------------
# MCP stdio client (talks to your mcp_server.py exactly)
# ----------------------------
@dataclass
class _MCPProcess:
    proc: subprocess.Popen
    lock: threading.Lock


_MCP_SINGLETON: Optional[_MCPProcess] = None


def _start_mcp() -> _MCPProcess:
    """
    Starts mcp_server.py once and reuses it.
    Your repo has mcp_server.py at repo root, and you run from repo CWD.
    """
    global _MCP_SINGLETON
    if _MCP_SINGLETON and _MCP_SINGLETON.proc.poll() is None:
        return _MCP_SINGLETON

    mcp_script = os.environ.get("MCP_SCRIPT", "mcp_server.py")
    py = os.environ.get("PYTHON", "python")

    proc = subprocess.Popen(
        [py, mcp_script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    _MCP_SINGLETON = _MCPProcess(proc=proc, lock=threading.Lock())
    return _MCP_SINGLETON


def _mcp_call(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """
    Your MCP server expects:
      {"jsonrpc":"2.0","id":..., "method":"tools/call","params":{"name":..., "arguments":...}}
    and prints one JSON response line.
    """
    mcp = _start_mcp()
    proc = mcp.proc
    assert proc.stdin and proc.stdout

    req_id = str(uuid.uuid4())
    req = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }

    with mcp.lock:
        proc.stdin.write(json.dumps(req) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()

    if not line:
        raise RuntimeError("MCP server returned no output (stdout empty).")

    resp = json.loads(line)
    if "error" in resp:
        raise RuntimeError(f"MCP error: {resp['error']}")
    # your server wraps result like: {"result": {"content": result_dict}}
    return resp.get("result", {})


# ----------------------------
# DATA AGENT (LLM + MCP)
# ----------------------------
_TOOL_SCHEMA = {
    "get_customer": {"customer_id": "int"},
    "list_customers": {"status": "str", "limit": "int"},
    "update_customer": {"customer_id": "int", "data": "dict"},
    "create_ticket": {"customer_id": "int", "issue": "str", "priority": "str"},
    "get_customer_history": {"customer_id": "int"},
}


def _safe_json_obj(s: str) -> Optional[Dict[str, Any]]:
    try:
        x = json.loads(s)
        return x if isinstance(x, dict) else None
    except Exception:
        return None


def _heuristic_tool_fallback(user_text: str) -> Tuple[str, Dict[str, Any]]:
    t = (user_text or "").lower()

    # update email example: "Update my email to new@email.com and show my ticket history"
    email = None
    m_email = re.search(r"\b[\w\.-]+@[\w\.-]+\.\w+\b", user_text)
    if m_email:
        email = m_email.group(0)

    m_id = re.search(r"\bcustomer\s*(?:id)?\s*[:#]?\s*(\d+)\b", user_text, re.I)
    cid = int(m_id.group(1)) if m_id else None

    if "history" in t and cid is not None:
        return "get_customer_history", {"customer_id": cid}
    if "update" in t and "email" in t and cid is not None and email:
        return "update_customer", {"customer_id": cid, "data": {"email": email}}
    if "list" in t and "active" in t:
        return "list_customers", {"status": "active", "limit": 50}
    if cid is not None:
        return "get_customer", {"customer_id": cid}

    return "list_customers", {"status": "active", "limit": 10}


class SimpleDataAgentExecutor:
    """
    Compatible with your current servers:
      DefaultRequestHandler(agent_executor=SimpleDataAgentExecutor(), task_store=InMemoryTaskStore())
    This executor:
      1) LLM chooses an MCP tool call
      2) Executes it via stdio MCP server (your mcp_server.py)
      3) LLM summarises result nicely (including customer name when present)
    """

    async def handle_message(self, message: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        user_text = _extract_user_text(message)

        system = (
            "You are the Customer Data Agent. You must answer by calling exactly one MCP tool.\n"
            "Choose the best tool and arguments, then output ONLY JSON with keys tool_name and arguments.\n"
            f"Available tools and argument types:\n{json.dumps(_TOOL_SCHEMA, indent=2)}\n"
            "Rules:\n"
            "- Use integers for customer_id.\n"
            "- For list_customers use status in {'active','disabled'} and an integer limit.\n"
            "- For update_customer put fields under data (example: {'email':'new@email.com'}).\n"
            "- Return JSON only, no markdown, no explanation."
        )

        raw = _llm_text(system=system, user=user_text, temperature=0.0)
        choice = _safe_json_obj(raw)

        if not choice or "tool_name" not in choice or "arguments" not in choice:
            tool_name, args = _heuristic_tool_fallback(user_text)
        else:
            tool_name = str(choice["tool_name"])
            args = choice["arguments"]
            if not isinstance(args, dict):
                tool_name, args = _heuristic_tool_fallback(user_text)

        # Call MCP
        try:
            mcp_result = _mcp_call(tool_name, args)
        except Exception as e:
            return _a2a_text_response(
                f"Data Agent error calling MCP tool '{tool_name}': {type(e).__name__}: {e}"
            )

        # Summarise cleanly
        system2 = (
            "You are the Customer Data Agent. Summarise the MCP result for a user.\n"
            "If customer found, include: id, name, status, email.\n"
            "If tickets, include: id, status, priority, issue.\n"
            "Be concise and factual. Do not invent data.\n"
            "If the MCP result contains content JSON, use it."
        )
        user2 = (
            f"User request:\n{user_text}\n\n"
            f"Tool called: {tool_name}\nArguments: {json.dumps(args)}\n\n"
            f"MCP raw result:\n{json.dumps(mcp_result, indent=2)}"
        )
        summary = _llm_text(system=system2, user=user2, temperature=0.2)

        return _a2a_text_response(summary)

    # extra entrypoints for SDK compatibility
    async def __call__(self, message: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        return await self.handle_message(message, **kwargs)

    def run(self, message: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        return asyncio.run(self.handle_message(message, **kwargs))


# ----------------------------
# SUPPORT AGENT (LLM)
# ----------------------------
class SimpleSupportAgentExecutor:
    """
    Support agent always calls the LLM to generate the support response.
    It can accept customer context that Router includes in the prompt.
    """

    async def handle_message(self, message: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        user_text = _extract_user_text(message)

        system = (
            "You are the Support Agent in a multi-agent customer service system.\n"
            "Handle cancellations, billing issues, refunds, shipping, account support.\n"
            "Use customer context if included in the user message.\n"
            "Be structured:\n"
            "- Acknowledge\n"
            "- Key questions needed\n"
            "- Recommended next steps\n"
            "Do not claim you performed actions you did not perform.\n"
            "Keep it concise."
        )

        reply = _llm_text(system=system, user=user_text, temperature=0.4)
        reply = f"Support Agent [{_now_iso()}]\n{reply}"

        return _a2a_text_response(reply)

    async def __call__(self, message: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        return await self.handle_message(message, **kwargs)

    def run(self, message: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
        return asyncio.run(self.handle_message(message, **kwargs))
