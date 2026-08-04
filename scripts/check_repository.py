#!/usr/bin/env python3
"""Small dependency-free public-content and secret-pattern check."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".md", ".py", ".json", ".yml", ".yaml", ".ts", ".txt"}
PATTERNS = {
    "private key": re.compile(r"BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY"),
    "GitHub token": re.compile(r"gh[opsu]_[A-Za-z0-9]{20,}"),
    "OpenAI-style secret": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    "absolute user home": re.compile(r"/home/[A-Za-z0-9._-]+/"),
}


def main() -> int:
    findings: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        if path.suffix not in TEXT_SUFFIXES and path.name not in {"LICENSE"}:
            continue
        text = path.read_text(encoding="utf-8")
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT)}: {label}")
    if findings:
        print("Potential private content found:", file=sys.stderr)
        print("\n".join(findings), file=sys.stderr)
        return 1
    print("Public-content scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
