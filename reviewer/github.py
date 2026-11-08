"""The few GitHub REST calls the reviewer needs."""
from __future__ import annotations

from . import http


class GitHub:
    def __init__(self, token: str, repo: str, api_url: str = "https://api.github.com"):
        self.repo = repo
        self.api = api_url.rstrip("/")
        self.headers = {
            "authorization": f"Bearer {token}",
            "accept": "application/vnd.github+json",
            "x-github-api-version": "2022-11-28",
            "user-agent": "claude-code-review-action",
        }

    def _url(self, path: str) -> str:
        return f"{self.api}/repos/{self.repo}{path}"

    def get_pr(self, number: int) -> dict:
        return http.request("GET", self._url(f"/pulls/{number}"), self.headers)

    def get_pr_diff(self, number: int) -> str:
        headers = dict(self.headers, accept="application/vnd.github.v3.diff")
        return http.request("GET", self._url(f"/pulls/{number}"), headers, raw=True)

    def list_reviews(self, number: int) -> list[dict]:
        out, page = [], 1
        while True:
            batch = http.request("GET", self._url(f"/pulls/{number}/reviews?per_page=100&page={page}"), self.headers)
            out.extend(batch)
            if len(batch) < 100:
                return out
            page += 1

    def get_file(self, path: str, ref: str) -> str | None:
        headers = dict(self.headers, accept="application/vnd.github.raw+json")
        try:
            return http.request("GET", self._url(f"/contents/{path}?ref={ref}"), headers, raw=True, retries=1)
        except http.HttpError as e:
            if e.status == 404:
                return None
            raise

    def create_review(self, number: int, commit_id: str, body: str, event: str, comments: list[dict]) -> dict:
        payload = {"commit_id": commit_id, "body": body, "event": event, "comments": comments}
        return http.request("POST", self._url(f"/pulls/{number}/reviews"), self.headers, payload)

    def create_issue_comment(self, number: int, body: str) -> dict:
        return http.request("POST", self._url(f"/issues/{number}/comments"), self.headers, {"body": body})
