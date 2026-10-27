"""OpenAI 兼容 LLM 客户端；失败不重试，由调用方回退规则。"""
from __future__ import annotations

import json
import urllib.request


class NoneLLM:
    available = False
    model = "none"

    def complete(self, system: str, user: str) -> str:
        raise RuntimeError("LLM 未配置")


class HttpLLM:
    available = True

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout_s: float = 20):
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_s = float(timeout_s)

    def complete(self, system: str, user: str) -> str:
        payload = json.dumps({
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=payload,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        if self.api_key:
            req.add_header("Authorization", f"Bearer {self.api_key}")
        with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"]


def build_llm(section: dict):
    if (section.get("provider") or "none").lower() != "http":
        return NoneLLM()
    return HttpLLM(
        base_url=section.get("base_url", ""),
        model=section.get("model", ""),
        api_key=section.get("api_key", ""),
        timeout_s=float(section.get("timeout_s", 20)),
    )
