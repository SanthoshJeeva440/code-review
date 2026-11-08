"""Free reviews through GitHub Models (OpenAI-compatible API, authenticated with the workflow's GITHUB_TOKEN)."""
from __future__ import annotations

import json
import re

from . import http
from .claude import REVIEW_TOOL

API_URL = "https://models.github.ai/inference/chat/completions"


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise RuntimeError("Model reply did not contain JSON")
    return json.loads(text[start:end + 1])


class GitHubModels:
    def __init__(self, token: str, model: str, max_tokens: int = 4000, api_url: str = API_URL):
        self.model = model
        self.max_tokens = max_tokens
        self.api_url = api_url
        self.headers = {
            "authorization": f"Bearer {token}",
            "accept": "application/json",
            "user-agent": "claude-code-review-action",
        }
        self.usage = {"input_tokens": 0, "output_tokens": 0}
        self._tools_supported = True

    def _call(self, payload: dict) -> dict:
        resp = http.request("POST", self.api_url, self.headers, payload)
        u = resp.get("usage") or {}
        self.usage["input_tokens"] += int(u.get("prompt_tokens", 0) or 0)
        self.usage["output_tokens"] += int(u.get("completion_tokens", 0) or 0)
        return resp

    def review(self, system: str, user: str) -> dict:
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if self._tools_supported:
            payload = {
                "model": self.model,
                "max_tokens": self.max_tokens,
                "messages": messages,
                "tools": [{"type": "function", "function": {
                    "name": REVIEW_TOOL["name"], "description": REVIEW_TOOL["description"],
                    "parameters": REVIEW_TOOL["input_schema"]}}],
                "tool_choice": {"type": "function", "function": {"name": REVIEW_TOOL["name"]}},
            }
            try:
                resp = self._call(payload)
                msg = (resp.get("choices") or [{}])[0].get("message") or {}
                for call in msg.get("tool_calls") or []:
                    fn = call.get("function") or {}
                    if fn.get("name") == REVIEW_TOOL["name"]:
                        return _normalise(json.loads(fn.get("arguments") or "{}"))
                if msg.get("content"):
                    return _normalise(_extract_json(msg["content"]))
            except http.HttpError as e:
                if e.status != 400:
                    raise
                # Some models on GitHub Models do not support tool calling: fall back to plain JSON.
                self._tools_supported = False
        schema = json.dumps(REVIEW_TOOL["input_schema"])
        json_messages = [
            {"role": "system", "content": system.replace(
                "Always respond by calling the submit_review tool.",
                "Respond with ONLY a JSON object (no prose, no code fences) matching this JSON schema:\n" + schema)},
            {"role": "user", "content": user.replace("call submit_review", "reply with the JSON review")},
        ]
        resp = self._call({"model": self.model, "max_tokens": self.max_tokens, "messages": json_messages})
        content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        return _normalise(_extract_json(content))


def _normalise(data: dict) -> dict:
    data = data if isinstance(data, dict) else {}
    data.setdefault("summary", "")
    if not isinstance(data.get("comments"), list):
        data["comments"] = []
    return data
