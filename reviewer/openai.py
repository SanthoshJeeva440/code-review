"""OpenAI API implementation using the same structured-review contract."""
from __future__ import annotations

from .github_models import GitHubModels

API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAI(GitHubModels):
    """OpenAI API client; API usage is billed separately from ChatGPT plans."""

    def __init__(self, api_key: str, model: str, max_tokens: int = 4000, api_url: str = API_URL):
        super().__init__(api_key, model, max_tokens=max_tokens, api_url=api_url)
        self.headers["user-agent"] = "santhosh-ai-code-review"
