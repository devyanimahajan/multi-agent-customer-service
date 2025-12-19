# Multi-Agent Customer Service System (A2A + MCP)

This repository implements a multi-agent customer service system that coordinates via an A2A-compatible JSON-RPC interface and accesses a SQLite customer database through an MCP server.

Agents:
- **Router Agent**: receives user queries and coordinates calls to specialist agents
- **Customer Data Agent**: fetches and updates customer and ticket data via MCP tools
- **Support Agent**: handles general support, triage, and response drafting, and can request data context

MCP Server:
- Exposes required MCP tools over stdin/stdout JSON-RPC:
  - `get_customer(customer_id)`
  - `list_customers(status, limit)`
  - `update_customer(customer_id, data)`
  - `create_ticket(customer_id, issue, priority)`
  - `get_customer_history(customer_id)`

---

## Project layout

```
multi-agent-customer-service/
  agents/
    data_agent_server.py
    support_agent_server.py
    router_agent_server.py
    executors.py
  mcp_server.py
  database_setup.py
  support.db                # created by database_setup.py
  demo.ipynb                # notebook demo (optional)
  logs/                     # server stdout/stderr logs (optional)
```

---

## Requirements

- Python 3.11
- Recommended: create a dedicated virtual environment

Example (conda):

```bash
conda create -n a2a_mcp python=3.11 -y
conda activate a2a_mcp
```

Install dependencies:

```bash
pip install -r requirements.txt
```

If you do not have `requirements.txt` yet, start with:

```bash
pip install uvicorn starlette httpx pydantic a2a
```

Note: package names may differ depending on your course starter code. Use your environment that already runs the agents successfully.

---

## 1) Create and seed the database

Run the provided setup script to create `support.db` and insert sample data.

```bash
python database_setup.py
```

---

## 2) Start the MCP server

The MCP server communicates over stdin/stdout. In practice, the agents start it as a subprocess, or you can run it manually for debugging.

Manual run (debugging only):

```bash
python mcp_server.py
```

---

## 3) Start the agents (3 terminals)

From the repo root:

### Data Agent (port 8001)

```bash
uvicorn agents.data_agent_server:app --host 127.0.0.1 --port 8001
```

### Support Agent (port 8002)

```bash
uvicorn agents.support_agent_server:app --host 127.0.0.1 --port 8002
```

### Router Agent (port 8003)

```bash
uvicorn agents.router_agent_server:app --host 127.0.0.1 --port 8003
```

Health checks:
- Agent card endpoint (new): `http://127.0.0.1:800X/.well-known/agent-card.json`
- Deprecated but often supported: `http://127.0.0.1:800X/.well-known/agent.json`

---


