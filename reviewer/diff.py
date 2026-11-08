"""Parse unified diffs into files and hunks, and work out which lines can take a comment."""
from __future__ import annotations

import fnmatch
import os
import re
from dataclasses import dataclass, field

HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

# Files that are rarely worth an AI review: lockfiles, build output, vendored code, binaries.
DEFAULT_EXCLUDES = [
    "*.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "Cargo.lock",
    "go.sum", "composer.lock", "Gemfile.lock", "Pipfile.lock", "*.min.js", "*.min.css", "*.map",
    "dist/*", "build/*", "vendor/*", "node_modules/*", "*.snap", "*.svg", "*.png", "*.jpg",
    "*.jpeg", "*.gif", "*.ico", "*.pdf", "*.woff", "*.woff2", "*.ttf", "*.eot", "*.zip",
    "*.jar", "*.class", "*.exe", "*.dll", "*.so", "*.dylib", "*.pb.go", "*_pb2.py", "*.generated.*",
]

LANGUAGES = {
    ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript (React)", ".ts": "TypeScript",
    ".tsx": "TypeScript (React)", ".mjs": "JavaScript", ".cjs": "JavaScript", ".java": "Java",
    ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala", ".go": "Go", ".rs": "Rust", ".c": "C",
    ".h": "C/C++ header", ".cc": "C++", ".cpp": "C++", ".cxx": "C++", ".hpp": "C++", ".cs": "C#",
    ".fs": "F#", ".vb": "Visual Basic", ".rb": "Ruby", ".php": "PHP", ".swift": "Swift",
    ".m": "Objective-C", ".mm": "Objective-C++", ".dart": "Dart", ".lua": "Lua", ".pl": "Perl",
    ".r": "R", ".jl": "Julia", ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang", ".hs": "Haskell",
    ".clj": "Clojure", ".groovy": "Groovy", ".sh": "Shell", ".bash": "Bash", ".zsh": "Zsh",
    ".ps1": "PowerShell", ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".vue": "Vue", ".svelte": "Svelte", ".yml": "YAML", ".yaml": "YAML", ".json": "JSON",
    ".toml": "TOML", ".xml": "XML", ".tf": "Terraform", ".hcl": "HCL", ".robot": "Robot Framework",
    ".md": "Markdown", ".gradle": "Gradle", ".sol": "Solidity", ".zig": "Zig", ".nim": "Nim",
}
SPECIAL_NAMES = {"Dockerfile": "Dockerfile", "Makefile": "Makefile", "Jenkinsfile": "Groovy (Jenkins)"}


def detect_language(path: str) -> str:
    name = os.path.basename(path)
    if name in SPECIAL_NAMES:
        return SPECIAL_NAMES[name]
    return LANGUAGES.get(os.path.splitext(name)[1].lower(), "Text")


@dataclass
class FileDiff:
    path: str
    old_path: str | None = None
    status: str = "modified"  # added | deleted | renamed | modified | binary
    hunks: list[str] = field(default_factory=list)
    # New-file line numbers that appear in the diff (added or context) -> can take an inline comment.
    commentable_lines: set[int] = field(default_factory=set)
    added_lines: set[int] = field(default_factory=set)

    @property
    def language(self) -> str:
        return detect_language(self.path)

    @property
    def patch(self) -> str:
        return "\n".join(self.hunks)

    def annotated_patch(self) -> str:
        """The patch with new-file line numbers on each line, so the model can cite exact lines."""
        out = []
        for hunk in self.hunks:
            new_line = 0
            for raw in hunk.split("\n"):
                m = HUNK_RE.match(raw)
                if m:
                    new_line = int(m.group(3))
                    out.append(raw)
                elif raw.startswith("+"):
                    out.append(f"{new_line:>5} {raw}")
                    new_line += 1
                elif raw.startswith("-"):
                    out.append(f"{'':>5} {raw}")
                elif raw.startswith("\\"):
                    out.append(f"{'':>5} {raw}")
                else:
                    out.append(f"{new_line:>5} {raw}")
                    new_line += 1
        return "\n".join(out)


def parse_unified_diff(text: str) -> list[FileDiff]:
    files: list[FileDiff] = []
    current: FileDiff | None = None
    hunk_lines: list[str] = []
    new_line = 0

    def flush_hunk():
        nonlocal hunk_lines
        if current is not None and hunk_lines:
            current.hunks.append("\n".join(hunk_lines))
        hunk_lines = []

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            flush_hunk()
            m = re.match(r"diff --git a/(.*) b/(.*)$", raw)
            path = m.group(2) if m else raw.split()[-1]
            current = FileDiff(path=path, old_path=m.group(1) if m else None)
            files.append(current)
            continue
        if current is None:
            continue
        if raw.startswith("new file mode"):
            current.status = "added"
        elif raw.startswith("deleted file mode"):
            current.status = "deleted"
        elif raw.startswith("rename from "):
            current.status = "renamed"
        elif raw.startswith("Binary files ") or raw.startswith("GIT binary patch"):
            current.status = "binary"
        elif raw.startswith("+++ ") or raw.startswith("--- "):
            if not hunk_lines:  # file header, not a content line
                if raw.startswith("+++ b/"):
                    current.path = raw[6:]
                continue
            hunk_lines.append(raw)
            if raw.startswith("+"):
                current.commentable_lines.add(new_line)
                current.added_lines.add(new_line)
                new_line += 1
        elif raw.startswith("@@"):
            flush_hunk()
            m = HUNK_RE.match(raw)
            new_line = int(m.group(3)) if m else 0
            hunk_lines.append(raw)
        elif hunk_lines:
            hunk_lines.append(raw)
            if raw.startswith("+"):
                current.commentable_lines.add(new_line)
                current.added_lines.add(new_line)
                new_line += 1
            elif raw.startswith("-") or raw.startswith("\\"):
                pass
            else:
                current.commentable_lines.add(new_line)
                new_line += 1
    flush_hunk()
    return files


def is_excluded(path: str, patterns: list[str]) -> bool:
    name = os.path.basename(path)
    for pat in patterns:
        pat = pat.strip()
        if not pat:
            continue
        if fnmatch.fnmatch(path, pat) or fnmatch.fnmatch(name, pat):
            return True
        # "dir/*" should match nested paths like "a/dir/x" and "dir/a/b".
        if pat.endswith("/*") and (path.startswith(pat[:-1]) or f"/{pat[:-1]}" in f"/{path}"):
            return True
    return False


def filter_files(files: list[FileDiff], include: list[str], exclude: list[str]) -> tuple[list[FileDiff], list[str]]:
    kept, skipped = [], []
    for f in files:
        if f.status in ("deleted", "binary") or not f.hunks:
            skipped.append(f.path)
        elif include and not is_excluded(f.path, include):
            skipped.append(f.path)
        elif is_excluded(f.path, exclude):
            skipped.append(f.path)
        else:
            kept.append(f)
    return kept, skipped


def chunk_files(files: list[FileDiff], max_chars: int) -> list[list[FileDiff]]:
    """Group files into batches whose annotated patches fit within max_chars each.
    A single file larger than the limit gets a batch of its own and is truncated later."""
    batches: list[list[FileDiff]] = []
    current: list[FileDiff] = []
    size = 0
    for f in files:
        n = len(f.annotated_patch())
        if current and size + n > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(f)
        size += n
    if current:
        batches.append(current)
    return batches
