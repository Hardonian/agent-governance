"""Secret detector — regex-based scanning for exposed credentials and keys."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Pattern


@dataclass(frozen=True)
class SecretMatch:
    """A detected secret in scanned text."""
    kind: str           # e.g. "AWS_ACCESS_KEY", "PRIVATE_KEY"
    start: int          # character offset in original text
    end: int            # character offset end
    redacted: str       # the matched text with middle replaced by ***


# Each pattern: (kind, compiled_regex)
# Patterns use named groups or match groups; we redact the full match.
_PATTERNS: List[tuple[str, Pattern[str]]] = [
    # AWS keys
    ("AWS_ACCESS_KEY", re.compile(
        r"(?<![A-Za-z0-9/+=])(?:AKIA|ABIA|ACCA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9/+=])"
    )),
    # Generic API key patterns (common prefixes)
    ("API_KEY", re.compile(
        r"""(?i)(?:api[_-]?key|apikey)\s*[:=]\s*['"]?([A-Za-z0-9\-_]{20,})['"]?"""
    )),
    # Bearer tokens
    ("BEARER_TOKEN", re.compile(
        r"""(?i)(?:bearer|token)\s*[:=]\s*['"]?([A-Za-z0-9\-_.]{20,})['"]?"""
    )),
    # GitHub tokens
    ("GITHUB_TOKEN", re.compile(
        r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}"
    )),
    # GitLab tokens
    ("GITLAB_TOKEN", re.compile(
        r"glpat-[A-Za-z0-9\-_]{20,}"
    )),
    # Slack tokens
    ("SLACK_TOKEN", re.compile(
        r"xox[bporas]-[A-Za-z0-9\-]{10,}"
    )),
    # Stripe keys
    ("STRIPE_KEY", re.compile(
        r"(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{20,}"
    )),
    # OpenAI keys
    ("OPENAI_KEY", re.compile(
        r"sk-[A-Za-z0-9]{20,}"
    )),
    # Anthropic keys
    ("ANTHROPIC_KEY", re.compile(
        r"sk-ant-[A-Za-z0-9\-_]{20,}"
    )),
    # Private keys (PEM)
    ("PRIVATE_KEY", re.compile(
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
        re.IGNORECASE,
    )),
    # Generic password in assignment
    ("PASSWORD", re.compile(
        r"""(?i)(?:password|passwd|pass|pwd)\s*[:=]\s*['"]?(\S{8,})['"]?"""
    )),
    # JWT tokens
    ("JWT_TOKEN", re.compile(
        r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    )),
    # Database connection strings with passwords
    ("DB_CONNECTION_STRING", re.compile(
        r"""(?i)(?:postgres|mysql|mongodb|redis|mssql)(?:ql)?://\S+:\S+@\S+"""
    )),
    # Generic hex secrets (32+ hex chars, unlikely to be hashes in context)
    ("HEX_SECRET", re.compile(
        r"""(?i)(?:secret|key|token)\s*[:=]\s*['"]?([0-9a-f]{32,})['"]?"""
    )),
]


def _redact(text: str, start: int, end: int) -> str:
    """Replace middle of matched text with *** while keeping 4 chars each side."""
    matched = text[start:end]
    if len(matched) <= 12:
        return matched[:2] + "***" + matched[-2:]
    return matched[:4] + "***" + matched[-4:]


class SecretDetector:
    """Scans text for patterns matching API keys, tokens, private keys, passwords."""

    def __init__(self, extra_patterns: list[tuple[str, str]] | None = None):
        """Create detector. extra_patterns: list of (kind, regex_str) to add."""
        self._patterns = list(_PATTERNS)
        if extra_patterns:
            for kind, regex_str in extra_patterns:
                self._patterns.append((kind, re.compile(regex_str)))

    def scan(self, text: str) -> List[SecretMatch]:
        """Return all secret matches found in *text*."""
        matches: List[SecretMatch] = []
        seen_spans: set[tuple[int, int]] = set()

        for kind, pattern in self._patterns:
            for m in pattern.finditer(text):
                span = (m.start(), m.end())
                if span not in seen_spans:
                    seen_spans.add(span)
                    matches.append(SecretMatch(
                        kind=kind,
                        start=m.start(),
                        end=m.end(),
                        redacted=_redact(text, m.start(), m.end()),
                    ))

        # Sort by position.
        matches.sort(key=lambda s: s.start)
        return matches

    def has_secrets(self, text: str) -> bool:
        """Return True if any secret is detected (fast path, stops at first match)."""
        for _, pattern in self._patterns:
            if pattern.search(text):
                return True
        return False
