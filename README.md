# AI Code Review

An AI pull request reviewer that works for **any language and any repository**, and is **GitHub Models free by default, with optional OpenAI API or Claude**. Add one workflow file to a repo and every pull request gets an AI review: a summary, plus inline comments on the exact changed lines, with one-click suggested fixes where the model is confident.

- **Free, no API key.** By default it uses [GitHub Models](https://docs.github.com/en/github-models) with the workflow's built-in `GITHUB_TOKEN` (default model `openai/gpt-4.1`). Nothing to sign up for.
- **OpenAI API models.** Set `provider: openai`, add an `OPENAI_API_KEY` secret, and choose an API model such as `gpt-4.1-mini`. OpenAI API usage is billed separately from ChatGPT Plus/Pro subscriptions.
- **Claude.** Set `provider: anthropic` and an `ANTHROPIC_API_KEY` secret to use the Claude models configured in this repository.
- **Any language.** Python, JavaScript/TypeScript, Java, Kotlin, Go, Rust, C/C++, C#, Ruby, PHP, Swift, Dart, SQL, shell, Terraform, YAML, Dockerfiles, Robot Framework and more. The reviewer is told which languages are in each PR and applies that ecosystem's idioms.
- **Inline comments that land.** Every comment is checked against the diff before posting; findings outside the changed lines go into the review summary instead of failing the request.
- **Large PRs.** Big diffs are split across several requests. Lockfiles, build output, images and other generated files are skipped automatically.
- **Your rules.** Put project guidelines in `.github/code-review.md` (or `CLAUDE.md`, `AGENTS.md`, `REVIEW_GUIDELINES.md`). They are read from the **base** branch, so a PR cannot rewrite its own review rules.
- **Safe by default.** PR text and code are treated as untrusted data (prompt-injection resistant), only repo members can trigger `/review`, no dependencies to install (Python standard library only), and the same commit is never reviewed twice.
- **Can gate merges.** Optionally fail the check or submit "Request changes" when a finding reaches a severity you choose.

## Quick start (any repository, free)

Create `.github/workflows/ai-review.yml`:

```yaml
name: AI code review
on:
  pull_request:
    types: [opened, synchronize, reopened, ready_for_review]
  issue_comment:
    types: [created]
permissions:
  contents: read
  pull-requests: write
  models: read
concurrency:
  group: ai-review-${{ github.event.pull_request.number || github.event.issue.number }}
  cancel-in-progress: true
jobs:
  review:
    if: github.event_name == 'pull_request' || (github.event.issue.pull_request && startsWith(github.event.comment.body, '/review'))
    runs-on: ubuntu-latest
    steps:
      - uses: SanthoshJeeva440/code-review@v1
```

That's all. Open a pull request, or comment `/review` on one to get a fresh review. The `models: read` permission is what lets the built-in token use GitHub Models.

More examples are in [`examples/`](examples/), including the [reusable-workflow form](examples/ai-review-reusable.yml) and a [sample guidelines file](examples/code-review.md).

### Free tier limits (GitHub Models)

GitHub Models is free but rate-limited per user/org. At the time of writing, a model like `openai/gpt-4.1` allows roughly 10 requests a minute and 50 a day, with about 8k input and 4k output tokens per request; smaller models such as `openai/gpt-4.1-mini` allow more requests per day. Check [GitHub's current limits](https://docs.github.com/en/github-models/use-github-models/prototyping-with-ai-models#rate-limits). To fit those limits the action, on this provider, reviews about 16k characters of diff per request, skips full-file context, and reviews large PRs one part at a time. A very large PR uses several requests from the daily quota.

### Switch to Claude for higher-quality reviews

1. Get an API key from the [Claude Console](https://console.anthropic.com/) and add it as a repository or organization secret named `ANTHROPIC_API_KEY`.
2. Add to the step:

```yaml
      - uses: SanthoshJeeva440/code-review@v1
        with:
          provider: anthropic
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          model: claude-opus-5-5     # or claude-sonnet-5-5 / claude-haiku-5-5
```

With Claude, requests are much larger (whole changed files are sent as context) and large PRs are reviewed in parallel.

### Or use the reusable workflow

```yaml
permissions:
  contents: read
  pull-requests: write
  models: read
jobs:
  review:
    uses: SanthoshJeeva440/code-review/.github/workflows/review.yml@v1
    with:
      action_ref: v1
```

### Roll it out to every repo in an organization

Add the workflow file to each repo, or (GitHub Team / Enterprise) add the reusable workflow as a **required workflow** through an organization ruleset so it runs on every repo automatically. If you use Claude, store `ANTHROPIC_API_KEY` as an **organization** secret.

## Inputs

| Input | Default | What it does |
|---|---|---|
| `provider` | `github-models` | `github-models` (free), `openai` (OpenAI API), or `anthropic` (Claude). |
| `model` | per provider | `openai/gpt-4.1` for GitHub Models (any [GitHub Models](https://github.com/marketplace?type=models) ID works), `claude-opus-5-5` for Anthropic; `gpt-4.1-mini` for the OpenAI API. |
| `anthropic_api_key` | | Only for `provider: anthropic`. |
| `openai_api_key` | | Only for `provider: openai`; use a GitHub Actions secret. |
| `github_token` | `github.token` | Needs `pull-requests: write`, `contents: read`, and `models: read` for GitHub Models. |
| `min_severity` | `low` | Lowest severity reported: `critical`, `high`, `medium`, `low`, `nit`. |
| `max_comments` | `25` | Cap on inline comments, highest severity first. |
| `fail_on` | `none` | Fail the check if a finding is at or above this severity. |
| `request_changes_on` | `none` | Submit "Request changes" at or above this severity. |
| `include` | | Globs; when set, only matching files are reviewed. |
| `exclude` | | Extra globs to skip (added to the built-in list). |
| `use_default_excludes` | `true` | Skip lockfiles, `dist/`, `build/`, `vendor/`, images, minified files and so on. |
| `guidelines_files` | `.github/code-review.md,REVIEW_GUIDELINES.md,CLAUDE.md,AGENTS.md,.github/copilot-instructions.md` | Review rules read from the base branch. |
| `extra_instructions` | | Extra free-text instructions for the reviewer. |
| `include_full_files` | `false` / `true` | Send full changed files as context (off on GitHub Models to fit the free limits). |
| `max_files` | `100` | Max files reviewed per PR. |
| `max_chars_per_request` | `16000` / `120000` | Size of each request; bigger PRs are split. |
| `max_output_tokens` | `4000` / `8000` | Output tokens per request. |
| `concurrency` | `1` / `3` | Parallel requests for large PRs. |
| `review_drafts` | `false` | Also review draft PRs. |
| `skip_label` | `skip-ai-review` | PRs with this label are skipped. |
| `comment_trigger` | `/review` | Comment prefix that triggers a review. |
| `comment_trigger_roles` | `OWNER,MEMBER,COLLABORATOR` | Who may trigger by comment. |
| `rereview_same_commit` | `false` | Review a commit again even if already reviewed. |
| `dry_run` | `false` | Print the review to the log and job summary without posting. |
| `models_api_url` / `anthropic_api_url` | official endpoints | Override for a proxy or gateway. |

Where two defaults are shown, the first is for `github-models` and the second for `anthropic`.

Outputs: `findings` (count) and `worst_severity`.

## Pull requests from forks

On the `pull_request` event, GitHub gives fork PRs a read-only token, so the review cannot be posted. For public repos that accept outside contributions, either review fork PRs on demand with `/review` (the `issue_comment` event has secrets and a write token), or use `pull_request_target` **without checking out the PR's code** (this action never checks out or runs PR code; it reads the diff through the API).

## Cost

With the default `github-models` provider the action is free, within GitHub Models' rate limits. With `provider: openai`, usage is billed to the OpenAI API project and is separate from ChatGPT subscription limits. With `provider: anthropic`, each review costs one Claude request per ~120k characters of diff and context; lower it with `claude-sonnet-5-5` or `claude-haiku-5-5`, `include_full_files: false`, or tighter `include`/`exclude` patterns. Token usage is printed at the bottom of every review.

## How it works

1. Reads the PR and its unified diff through the GitHub API.
2. Drops deleted, binary and generated files; numbers every diff line with its new-file line number.
3. Sends the annotated diff, optional full-file context and your guidelines to the model, forcing a structured `submit_review` tool call (with a plain-JSON fallback for models without tool calling) (summary + findings with path, line, severity, category, fix and optional suggestion).
4. Validates each finding against the diff, ranks by severity, and posts one GitHub review with inline comments.

Code: [`reviewer/`](reviewer/) (Python 3.9+, standard library only). Tests: `python -m unittest discover -s tests -t .`

## Releasing

Consumers pin `@v1`. After merging changes to `main`, move the tag: `git tag -fa v1 -m v1 && git push -f origin v1` (and create `v1.0.0`-style tags for exact pins).
