from __future__ import annotations

import json
import re
from typing import Any

try:
    from openai import OpenAI
except ImportError:  # demo mode can still run before dependencies are installed
    OpenAI = None

from .config import (
    DASHSCOPE_API_KEY,
    QWEN_BASE_URL,
    QWEN_MODEL,
    QWEN_EMBEDDING_MODEL,
    QWEN_EMBEDDING_DIM,
    WITH_QWEN,
)


class QwenGateway:
    def __init__(self) -> None:
        self.enabled = WITH_QWEN and OpenAI is not None
        self.model = QWEN_MODEL
        self.embedding_model = QWEN_EMBEDDING_MODEL
        self.embedding_dim = QWEN_EMBEDDING_DIM
        self.client = None
        if self.enabled:
            self.client = OpenAI(api_key=DASHSCOPE_API_KEY, base_url=QWEN_BASE_URL, timeout=90.0, max_retries=2)

    def chat(self, messages: list[dict[str, Any]], tools=None, tool_choice='auto', temperature=0.2):
        if not self.enabled or self.client is None:
            raise RuntimeError('Qwen API is not configured')
        kwargs: dict[str, Any] = {
            'model': self.model,
            'messages': messages,
            'temperature': temperature,
            'extra_body': {'enable_thinking': False},
        }
        if tools is not None:
            kwargs['tools'] = tools
            kwargs['tool_choice'] = tool_choice
        return self.client.chat.completions.create(**kwargs)

    def embeddings(self, texts: list[str]) -> list[list[float]]:
        if not self.enabled or self.client is None:
            raise RuntimeError('Qwen API is not configured')
        response = self.client.embeddings.create(
            model=self.embedding_model,
            input=texts,
            dimensions=self.embedding_dim,
        )
        return [item.embedding for item in response.data]

    @staticmethod
    def parse_json(text: str, fallback: dict | None = None) -> dict:
        fallback = fallback or {}
        if not text:
            return fallback
        cleaned = text.strip()
        cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.I)
        cleaned = re.sub(r'\s*```$', '', cleaned)
        try:
            return json.loads(cleaned)
        except Exception:
            m = re.search(r'\{.*\}', cleaned, flags=re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    pass
        return fallback
