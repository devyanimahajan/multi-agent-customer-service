# agents/mcp_client.py
from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

class MCPStdioClient:
    def __init__(self, mcp_script_path: str):
        self.mcp_script_path = mcp_script_path
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return

        script = str(Path(self.mcp_script_path).resolve())
        self.proc = subprocess.Popen(
            [os.environ.get("PYTHON", "python"), script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        self.start()
        assert self.proc and self.proc.stdin and self.proc.stdout

        req_id = str(uuid.uuid4())
        req = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }

        with self._lock:
            self.proc.stdin.write(json.dumps(req) + "\n")
            self.proc.stdin.flush()

            # Read one line response
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("MCP server returned no output")
            resp = json.loads(line)

        if "error" in resp:
            raise RuntimeError(f"MCP error: {resp['error']}")
        return resp.get("result", {})
