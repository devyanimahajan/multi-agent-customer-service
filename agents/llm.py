# agents/llm.py
from __future__ import annotations

import os
from typing import List, Dict, Any, Optional

from openai import OpenAI

_client: Optional[OpenAI] = None

def get_client() -> OpenAI:
    global _client
    if _client is None:
        # OPENAI_API_KEY is read from env
        _client = OpenAI()
    return _client

def chat_text(
    system: str,
    user: str,
    model: Optional[str] = None,
    temperature: float = 0.2,
) -> str:
    """
    Minimal helper that returns a single text response.
    Uses the Chat Completions API for simplicity.
    """
    client = get_client()
    model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

    resp = client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()
