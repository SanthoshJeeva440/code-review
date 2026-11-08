"""Entry point: review one pull request with Claude and post the result as a GitHub review."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

from . import __version__
from .claude import Claude
from .diff import DEFAULT_EXCLUDES, FileDiff, chunk_files, filter_files, parse_unified_diff
from .github import GitHub
from .github_models import API_URL as GITHUB_MODELS_URL, GitHubModels
from .openai import OpenAI
from .http import HttpError
from .prompt import SYSTEM_PROMPT, build_user_prompt

MARKER = "<!-- claude-code-review"
SEVERITY_ORDER = ["critical", "high", "medium", "low", "nit"]
SEVERITY_ICON = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "nit": "⚪"}
DEFAULT_GUIDELINE_FILES = [".github/code-review.md", "REVIEW_GUIDELINES.md", "CLAUDE.md", "AGENTS.md", ".github/copilot-instructions.md"]


def env(name: str, default: str = "") -> str:
    v = os.environ.get(f"INPUT_{name.upper()}")
    if v is None or v == "":
        v = os.environ.get(name.upper(), default)
    return (v or default).strip()


def env_list(name: str, default: list[str] | None = None) -> list[str]:
    raw = env(name)
    if not raw:
        return list(default or [])
    return [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name).lower()
    return default if raw == "" else raw in ("1", "true", "yes", "on")


def env_int(name: str, default: int) -> int:
    raw = env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def rank(sev: str) -> int:
    return SEVERITY_ORDER.index(sev) if sev in SEVERITY_ORDER else len(SEVERITY_ORDER)


def log(msg: str) -> None:
    print(msg, flush=True)


def find_pr_number(event_name: str, event: dict) -> int | None:
    explicit = env("pr_number")
    if explicit:
        return int(explicit)
    if "pull_request" in event and event["pull_request"]:
        return int(event["pull_request"]["number"])
    if event_name == "issue_comment" and event.get("issue", {}).get("pull_request"):
        return int(event["issue"]["number"])
    if event.get("inputs", {}).get("pr_number"):
        return int(event["inputs"]["pr_number"])
    return None


def validate_comments(raw: list[dict], files: dict[str, FileDiff]) -> tuple[list[dict], list[dict]]:
    """Split model findings into ones GitHub can anchor inline and ones that go in the review body."""
    inline, orphan = [], []
    for c in raw:
        try:
            c["line"] = int(c.get("line"))
        except (TypeError, ValueError):
            orphan.append(c)
            continue
        c["severity"] = c.get("severity") if c.get("severity") in SEVERITY_ORDER else "medium"
        f = files.get(c.get("path", ""))
        if f is None or c["line"] not in f.commentable_lines:
            orphan.append(c)
            continue
        start = c.get("start_line")
        try:
            start = int(start) if start is not None else None
        except (TypeError, ValueError):
            start = None
        if start is not None and (start >= c["line"] or any(n not in f.commentable_lines for n in range(start, c["line"] + 1))):
            start = None
            c.pop("suggestion", None)  # a suggestion for a range we can't anchor would replace the wrong lines
        c["start_line"] = start
        inline.append(c)
    return inline, orphan


def dedupe(comments: list[dict]) -> list[dict]:
    seen, out = set(), []
    for c in comments:
        key = (c.get("path"), c.get("line"), (c.get("title") or "").strip().lower())
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def comment_markdown(c: dict) -> str:
    sev = c.get("severity", "medium")
    text = f"{SEVERITY_ICON.get(sev, '')} **{sev.capitalize()}** · {c.get('category', 'bug')} — **{c.get('title', '').strip()}**\n\n{c.get('body', '').strip()}"
    sug = c.get("suggestion")
    if sug and sug.strip():
        sug = sug.strip("\n").replace("```", "ˋˋˋ")
        text += f"\n\n```suggestion\n{sug}\n```"
    return text


def to_github_comment(c: dict) -> dict:
    gc = {"path": c["path"], "line": c["line"], "side": "RIGHT", "body": comment_markdown(c)}
    if c.get("start_line"):
        gc["start_line"] = c["start_line"]
        gc["start_side"] = "RIGHT"
    return gc


def build_body(*, head_sha: str, model: str, summaries: list[str], inline: list[dict], orphans: list[dict],
               skipped: list[str], reviewed: int, usage: dict, failed_parts: int, dropped: int) -> str:
    counts = {s: 0 for s in SEVERITY_ORDER}
    for c in inline + orphans:
        counts[c.get("severity", "medium")] = counts.get(c.get("severity", "medium"), 0) + 1
    lines = [f"{MARKER} sha={head_sha} -->", "## 🤖 AI code review", ""]
    summaries = [s.strip() for s in summaries if s and s.strip()]
    if len(summaries) == 1:
        lines.append(summaries[0])
    elif summaries:
        lines += [f"**Part {i}:** {s}" for i, s in enumerate(summaries, 1)]
    lines.append("")
    total = sum(counts.values())
    if total:
        lines.append("**Findings:** " + " · ".join(f"{SEVERITY_ICON[s]} {counts[s]} {s}" for s in SEVERITY_ORDER if counts[s]))
    else:
        lines.append("**No issues found.** ✅")
    if orphans:
        lines += ["", "<details><summary>Findings outside the changed lines</summary>", ""]
        for c in orphans:
            loc = f"`{c.get('path', '?')}`" + (f" line {c.get('line')}" if c.get("line") else "")
            lines.append(f"- {loc}: " + comment_markdown(c).replace("\n\n", "\n  ").replace("```suggestion", "```"))
        lines += ["", "</details>"]
    if dropped:
        lines += ["", f"_{dropped} lower-severity finding(s) were left out to respect the comment limit._"]
    if failed_parts:
        lines += ["", f"⚠️ {failed_parts} part(s) of this PR could not be reviewed because the model call failed. See the workflow log."]
    if skipped:
        shown = ", ".join(f"`{p}`" for p in skipped[:30]) + (" …" if len(skipped) > 30 else "")
        lines += ["", f"<sub>Skipped {len(skipped)} file(s) (deleted, binary, generated or excluded): {shown}</sub>"]
    lines += ["", f"<sub>Reviewed {reviewed} file(s) at {head_sha[:7]} with `{model}` · "
              f"{usage.get('input_tokens', 0):,} input / {usage.get('output_tokens', 0):,} output tokens · "
              f"claude-code-review v{__version__}. AI reviews can be wrong; use your judgment.</sub>"]
    return "\n".join(lines)


def load_guidelines(gh: GitHub, base_sha: str) -> str | None:
    paths = env_list("guidelines_files", DEFAULT_GUIDELINE_FILES)
    chunks = []
    for p in paths:
        try:
            text = gh.get_file(p, base_sha)
        except HttpError as e:
            log(f"Could not read guidelines file {p}: {e}")
            continue
        if text:
            log(f"Using review guidelines from {p} (base branch)")
            chunks.append(f"--- {p} ---\n{text}")
    return "\n\n".join(chunks) or None


def load_context(gh: GitHub, files: list[FileDiff], sha: str, budget: int) -> dict[str, str]:
    out: dict[str, str] = {}
    used = 0
    for f in files:
        if used >= budget:
            break
        try:
            text = gh.get_file(f.path, sha)
        except HttpError:
            continue
        if text is None or len(text) > budget - used or "\x00" in text:
            continue
        numbered = "\n".join(f"{i:>5}  {line}" for i, line in enumerate(text.split("\n"), 1))
        out[f.path] = numbered
        used += len(numbered)
    return out


def write_summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def set_output(name: str, value) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{name}={value}\n")


def run() -> int:
    api_key = env("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    openai_api_key = env("openai_api_key") or os.environ.get("OPENAI_API_KEY", "")
    token = env("github_token") or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        log("::error::github_token is not set.")
        return 1

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    event = {}
    if os.environ.get("GITHUB_EVENT_PATH") and os.path.exists(os.environ["GITHUB_EVENT_PATH"]):
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as fh:
            event = json.load(fh)
    repo = env("repository") or os.environ.get("GITHUB_REPOSITORY", "")
    number = find_pr_number(event_name, event)
    if not number:
        log("No pull request found for this event; nothing to review.")
        return 0

    if event_name == "issue_comment":
        trigger = env("comment_trigger", "/review")
        body = (event.get("comment", {}).get("body") or "").strip()
        if not body.startswith(trigger):
            log(f"Comment does not start with {trigger}; skipping.")
            return 0
        assoc = event.get("comment", {}).get("author_association", "")
        if assoc not in env_list("comment_trigger_roles", ["OWNER", "MEMBER", "COLLABORATOR"]):
            log(f"Commenter role {assoc or 'unknown'} may not trigger reviews; skipping.")
            return 0

    gh = GitHub(token, repo, env("github_api_url") or os.environ.get("GITHUB_API_URL", "https://api.github.com"))
    pr = gh.get_pr(number)
    head_sha, base_sha = pr["head"]["sha"], pr["base"]["sha"]
    log(f"Reviewing {repo}#{number} '{pr.get('title')}' at {head_sha[:7]}")

    if pr.get("state") != "open":
        log("PR is not open; skipping.")
        return 0
    if pr.get("draft") and not env_bool("review_drafts", False):
        log("PR is a draft; skipping (set review_drafts: true to review drafts).")
        return 0
    skip_label = env("skip_label", "skip-ai-review")
    if skip_label and any(l.get("name") == skip_label for l in pr.get("labels", [])):
        log(f"PR has label '{skip_label}'; skipping.")
        return 0
    dry_run = env_bool("dry_run", False)
    if event_name != "issue_comment" and not dry_run and not env_bool("rereview_same_commit", False):
        for r in gh.list_reviews(number):
            if f"{MARKER} sha={head_sha}" in (r.get("body") or ""):
                log("This commit was already reviewed; skipping. Comment /review to force a new review.")
                return 0

    diff_text = gh.get_pr_diff(number)
    all_files = parse_unified_diff(diff_text)
    excludes = DEFAULT_EXCLUDES + env_list("exclude") if env_bool("use_default_excludes", True) else env_list("exclude")
    files, skipped = filter_files(all_files, env_list("include"), excludes)
    max_files = env_int("max_files", 100)
    if len(files) > max_files:
        skipped += [f.path for f in files[max_files:]]
        files = files[:max_files]
    if not files:
        log("No reviewable files in this PR.")
        set_output("findings", 0)
        return 0

    provider = env("provider", "github-models").lower()
    free = provider in ("github-models", "github", "github_models")
    if free:
        # GitHub Models' free tier caps each request at about 8k input / 4k output tokens, so keep requests small.
        model = env("model") or "openai/gpt-4.1"
        llm = GitHubModels(token, model, max_tokens=env_int("max_output_tokens", 4000),
                           api_url=env("models_api_url") or GITHUB_MODELS_URL)
        max_chars = env_int("max_chars_per_request", 16000)
        include_context = env_bool("include_full_files", False)
        concurrency = env_int("concurrency", 1)
    elif provider in ("anthropic", "claude"):
        if not api_key:
            log("::error::provider is 'anthropic' but anthropic_api_key is empty. Add an ANTHROPIC_API_KEY secret, or use provider: github-models (free).")
            return 1
        model = env("model") or "claude-opus-5-5"
        llm = Claude(api_key, model, max_tokens=env_int("max_output_tokens", 8000),
                     api_url=env("anthropic_api_url") or "https://api.anthropic.com/v1/messages")
        max_chars = env_int("max_chars_per_request", 120000)
        include_context = env_bool("include_full_files", True)
        concurrency = env_int("concurrency", 3)
    elif provider in ("openai", "openai-api", "chatgpt"):
        if not openai_api_key:
            log("::error::provider is 'openai' but openai_api_key is empty. Add an OPENAI_API_KEY GitHub secret.")
            return 1
        model = env("model") or "gpt-4.1-mini"
        llm = OpenAI(openai_api_key, model, max_tokens=env_int("max_output_tokens", 4000),
                     api_url=env("openai_api_url") or "https://api.openai.com/v1/chat/completions")
        max_chars = env_int("max_chars_per_request", 16000)
        include_context = env_bool("include_full_files", False)
        concurrency = env_int("concurrency", 1)
    else:
        log(f"::error::Unknown provider '{provider}'. Use github-models or anthropic.")
        return 1
    batches = chunk_files(files, max_chars)
    guidelines = load_guidelines(gh, base_sha)
    extra = env("extra_instructions")
    all_paths = [f.path for f in files]
    log(f"{len(files)} file(s) to review in {len(batches)} request(s) with {model}; {len(skipped)} skipped.")

    def review_batch(idx_batch):
        idx, batch = idx_batch
        patch_size = sum(len(f.annotated_patch()) for f in batch)
        context = load_context(gh, batch, head_sha, max(0, max_chars - patch_size)) if include_context else {}
        prompt = build_user_prompt(
            title=pr.get("title", ""), description=pr.get("body") or "", base=pr["base"]["ref"], head=pr["head"]["ref"],
            files=batch, guidelines=guidelines, extra_instructions=extra, context_files=context,
            batch_index=idx, batch_count=len(batches), all_paths=all_paths, max_patch_chars=max_chars)
        return llm.review(SYSTEM_PROMPT, prompt)

    summaries, raw_comments, failed = [], [], 0
    with ThreadPoolExecutor(max_workers=max(1, min(concurrency, len(batches)))) as pool:
        for i, result in enumerate(pool.map(lambda b: _safe(review_batch, b), enumerate(batches, 1))):
            if isinstance(result, Exception):
                failed += 1
                log(f"::warning::Review of part {i + 1} failed: {result}")
                continue
            summaries.append(result.get("summary", ""))
            raw_comments.extend(c for c in result.get("comments", []) if isinstance(c, dict))
    if failed == len(batches):
        log("::error::Every model call failed; no review posted.")
        return 1

    by_path = {f.path: f for f in files}
    min_sev = env("min_severity", "low").lower()
    raw_comments = [c for c in dedupe(raw_comments) if rank(c.get("severity", "medium")) <= rank(min_sev)]
    inline, orphans = validate_comments(raw_comments, by_path)
    inline.sort(key=lambda c: (rank(c["severity"]), c["path"], c["line"]))
    max_comments = env_int("max_comments", 25)
    dropped = max(0, len(inline) - max_comments)
    inline = inline[:max_comments]

    body = build_body(head_sha=head_sha, model=model, summaries=summaries, inline=inline, orphans=orphans,
                      skipped=skipped, reviewed=len(files), usage=llm.usage, failed_parts=failed, dropped=dropped)
    findings = inline + orphans
    worst = min((rank(c["severity"]) for c in findings), default=len(SEVERITY_ORDER))
    fail_on = env("fail_on", "none").lower()
    request_changes_on = env("request_changes_on", "none").lower()
    event_type = "COMMENT"
    if request_changes_on in SEVERITY_ORDER and worst <= rank(request_changes_on):
        event_type = "REQUEST_CHANGES"

    gh_comments = [to_github_comment(c) for c in inline]
    write_summary(body + "\n\n" + "\n\n".join(f"**{c['path']}:{c['line']}** — " + comment_markdown(c) for c in inline))
    set_output("findings", len(findings))
    set_output("worst_severity", SEVERITY_ORDER[worst] if worst < len(SEVERITY_ORDER) else "none")

    if dry_run:
        log("Dry run: not posting.\n" + body)
        for c in gh_comments:
            log(f"\n--- {c['path']}:{c['line']}\n{c['body']}")
    else:
        try:
            gh.create_review(number, head_sha, body, event_type, gh_comments)
        except HttpError as e:
            if e.status != 422 or not gh_comments:
                raise
            # GitHub rejected an inline anchor; fall back to putting everything in the review body.
            log(f"::warning::Inline comments were rejected ({e}); posting them in the review body instead.")
            body = build_body(head_sha=head_sha, model=model, summaries=summaries, inline=[], orphans=orphans + inline,
                              skipped=skipped, reviewed=len(files), usage=llm.usage, failed_parts=failed, dropped=dropped)
            try:
                gh.create_review(number, head_sha, body, event_type, [])
            except HttpError:
                gh.create_issue_comment(number, body)
        log(f"Posted review with {len(gh_comments)} inline comment(s) and {len(orphans)} general finding(s).")

    if fail_on in SEVERITY_ORDER and worst <= rank(fail_on):
        log(f"::error::Found {SEVERITY_ORDER[worst]} issue(s); failing because fail_on={fail_on}.")
        return 1
    return 0


def _safe(fn, arg):
    try:
        return fn(arg)
    except Exception as e:  # noqa: BLE001 - one failed part should not sink the whole review
        return e


def main() -> None:
    try:
        sys.exit(run())
    except HttpError as e:
        hint = ""
        if e.status in (401, 403) and "anthropic" in str(e):
            hint = " Check the ANTHROPIC_API_KEY secret."
        elif e.status in (401, 403) and "models" in str(e):
            hint = " Add 'models: read' to the workflow's permissions so GITHUB_TOKEN can use GitHub Models."
        elif e.status == 429 and "models" in str(e):
            hint = " GitHub Models free-tier rate limit reached; try later, use a smaller model (openai/gpt-4.1-mini) or switch to provider: anthropic."
        elif e.status in (401, 403) and "openai" in str(e).lower():
            hint = " Check the OPENAI_API_KEY secret and OpenAI API project access/billing."
        elif e.status == 413:
            hint = " Request too large for the model's limit; lower max_chars_per_request."
        elif e.status == 403:
            hint = " The GitHub token needs 'pull-requests: write' (and 'contents: read') permissions. PRs from forks get a read-only token on the pull_request event."
        log(f"::error::{e}{hint}")
        sys.exit(1)


if __name__ == "__main__":
    main()
