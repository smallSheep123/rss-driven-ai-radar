from __future__ import annotations

import json
import re
import httpx


class AIClient:
    def __init__(self, *, base_url: str, api_key: str, model: str, system_prompt: str, timeout: int = 60):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.system_prompt = system_prompt
        self.timeout = timeout

    def complete(self, user_content: str) -> str:
        if not self.api_key:
            raise RuntimeError("AI API key is empty; set the configured environment variable")
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        with httpx.Client(timeout=self.timeout, follow_redirects=False) as client:
            response = client.post(
                self.base_url + "/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("AI API returned empty content")
        return content.strip()

    @staticmethod
    def _strip_fence(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        return text.strip()

    def score_batch(self, items: list[dict]) -> list[dict]:
        prompt = (
            "下面是 RSS 候选项。只根据给定字段做第一轮筛选。严格返回 JSON 数组，不要 Markdown。\n\n"
            + json.dumps(items, ensure_ascii=False)
        )
        data = json.loads(self._strip_fence(self.complete(prompt)))
        if not isinstance(data, list):
            raise ValueError("AI scorer did not return a JSON array")
        return [x for x in data if isinstance(x, dict)]
