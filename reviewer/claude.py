"""Call the Claude Messages API and get a structured review back via a forced tool call."""
from __future__ import annotations

from . import http

API_URL = "https://api.anthropic.com/v1/messages"

REVIEW_TOOL = {
    "name": "submit_review",
    "description": "Submit the code review for this part of the pull request.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "2-5 sentence overview of what these changes do and the overall quality. Markdown allowed.",
            },
            "comments": {
                "type": "array",
                "description": "Specific, actionable findings tied to a line in the diff. Empty if nothing is worth flagging.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path exactly as shown in the diff header."},
                        "line": {"type": "integer", "description": "New-file line number from the left column of the annotated diff. Must be a line that appears in the diff."},
                        "start_line": {"type": "integer", "description": "Optional first line when the finding spans several lines (must be < line, same hunk)."},
                        "severity": {"type": "string", "enum": ["critical", "high", "medium", "low", "nit"]},
                        "category": {"type": "string", "enum": ["bug", "security", "performance", "error-handling", "concurrency", "maintainability", "testing", "style", "docs"]},
                        "title": {"type": "string", "description": "One-line statement of the problem."},
                        "body": {"type": "string", "description": "Why it is a problem and how to fix it. Markdown allowed."},
                        "suggestion": {"type": "string", "description": "Optional exact replacement code for lines start_line..line (or just line). Only when you are confident; no markdown fences."},
                    },
                    "required": ["path", "line", "severity", "category", "title", "body"],
                },
            },
        },
        "required": ["summary", "comments"],
    },
}


class Claude:
    def __init__(self, api_key: str, model: str, max_tokens: int = 8000, api_url: str = API_URL):
        self.model = model
        self.max_tokens = max_tokens
        self.api_url = api_url
        self.headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "user-agent": "claude-code-review-action",
        }
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    def review(self, system: str, user: str) -> dict:
        payload = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "tools": [REVIEW_TOOL],
            "tool_choice": {"type": "tool", "name": "submit_review"},
            "messages": [{"role": "user", "content": user}],
        }
        resp = http.request("POST", self.api_url, self.headers, payload)
        for k in self.usage:
            self.usage[k] += int(resp.get("usage", {}).get(k, 0) or 0)
        for block in resp.get("content", []):
            if block.get("type") == "tool_use" and block.get("name") == "submit_review":
                data = block.get("input") or {}
                data.setdefault("summary", "")
                data.setdefault("comments", [])
                return data
        raise RuntimeError(f"Claude did not return a review (stop_reason={resp.get('stop_reason')})")
