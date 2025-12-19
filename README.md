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

## 4) End-to-end demo (Notebook or script)

### Option A: Notebook demo (recommended for submission)

Open and run your demo notebook (for example `demo.ipynb`) from the repo root. The notebook should:

1. Ensure environment variables are set (if needed)
2. Start or verify all three agents are running
3. Send JSON-RPC `message/send` requests to the Router
4. Print outputs for at least **3 A2A coordination scenarios**

Minimum required scenarios (examples):

1. Task allocation:
   - `I need help with my account, customer ID 5`
2. Negotiation / multi-intent:
   - `I want to cancel my subscription but I'm having billing issues`
3. Multi-step:
   - `Show me all active customers who have open tickets`

Your current output shows successful coordination with explicit A2A routing logs.

### Option B: Python script demo

If you prefer a script, create a small runner that sends JSON-RPC to the Router and prints responses.

---

## Troubleshooting

- If ports are in use, kill the existing processes:
  - macOS: `lsof -i :8001` then `kill -9 <PID>`
- If an agent card endpoint warns about deprecation, switch to:
  - `/.well-known/agent-card.json`
- If you see JSON-RPC `Method not found`, confirm:
  - You are calling the correct RPC path (commonly `/`)
  - You are using the correct method name (commonly `message/send`)
