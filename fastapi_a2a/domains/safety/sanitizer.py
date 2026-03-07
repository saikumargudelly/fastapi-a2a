"""
Prompt Sanitizer — Runtime inline sanitization middleware (§18.1, §19.1).

Implements 8 canonical rules (R01–R08) across 8 mandatory surfaces (S01–S08):
  R01: Instruction injection patterns
  R02: System prompt/role override
  R03: Unicode bidirectional character stripping
  R04: Excessive whitespace / invisible characters
  R05: Base64 encoded payload detection and scanning
  R06: Truncation at max_field_length (default 2048)
  R07: HTML/script tag stripping
  R08: Nested format escape sequences

Each rule returns (cleaned_text, rule_triggered, incremental_score).
Aggregate score is the max of all rule scores (bounded [0, 1]).
"""
from __future__ import annotations

import base64
import re
import unicodedata
from dataclasses import dataclass

BIDI_CONTROL_CHARS: frozenset[str] = frozenset(
    chr(c) for c in (
        0x200F, 0x200E, 0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
        0x2066, 0x2067, 0x2068, 0x2069,  # Bidi isolate/override
        0x200B, 0x200C, 0x200D,  # Zero-width chars
        0xFEFF,  # BOM
    )
)

_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.I),
    re.compile(r"your\s+new\s+(role|persona|instructions?|directive)\s+is", re.I),
    re.compile(r"\byou\s+are\s+now\s+(?:DAN|GPT|an?\s+AI\s+without)", re.I),
    re.compile(r"\[(?:INST|SYS|SYSTEM)\]\s*override", re.I),
    re.compile(r"do\s+not\s+(?:show|tell|reveal|disclose)\s+this\s+to\s+the\s+user", re.I),
]

_SYSTEM_OVERRIDE_PATTERNS = [
    re.compile(r'"role"\s*:\s*"system"\s*,\s*"content"\s*:\s*"', re.I),
    re.compile(r"<\|im_start\|>system", re.I),
    re.compile(r"<<SYS>>", re.I),
    re.compile(r"\[SYSTEM\]\s*:", re.I),
]

_HTML_PATTERNS = [
    re.compile(r"<script[^>]*>.*?</script>", re.I | re.S),
    re.compile(r"<iframe[^>]*>.*?</iframe>", re.I | re.S),
    re.compile(r"javascript\s*:", re.I),
    re.compile(r"on\w+\s*=\s*['\"]", re.I),
    re.compile(r"<[a-z][a-z0-9]*\s[^>]*>", re.I),
]

_ESCAPE_SEQUENCE_PATTERNS = [
    re.compile(r"\\u[0-9a-fA-F]{4}"),
    re.compile(r"\\x[0-9a-fA-F]{2}"),
    re.compile(r"%[0-9a-fA-F]{2}"),
]

_PII_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PII_PHONE = re.compile(r"\+?[\d\s\-().]{9,20}")


@dataclass
class RuleResult:
    rule_id: str
    triggered: bool
    score: float
    original_excerpt: str | None = None
    cleaned_excerpt: str | None = None


@dataclass
class SanitizeResult:
    original_text: str
    cleaned_text: str
    rules_triggered: list[str]
    aggregate_score: float
    pii_found: bool
    total_redactions: int
    truncated: bool


def sanitize_text(
    text: str,
    *,
    max_length: int = 2048,
    redact_placeholder: str = "[REDACTED:injection]",
    pii_redact_placeholder: str = "[REDACTED:pii]",
) -> SanitizeResult:
    """
    Apply all 8 sanitizer rules to `text` and return a SanitizeResult.
    Rules are applied in order; each rule operates on the running cleaned text.
    """
    cleaned = text
    rules_triggered: list[str] = []
    total_redactions = 0
    pii_found = False
    max_score: float = 0.0
    truncated = False

    # R03: Unicode bidi / zero-width stripping (apply first — denormalization)
    # Replace bidi control chars between word characters with a space to preserve
    # word boundaries (so "ignore\u202Eprevious" → "ignore previous", not "ignoreprevious")
    cleaned_r03 = re.sub(
        r"([^\s])[" + "".join(BIDI_CONTROL_CHARS) + r"]+([^\s])",
        r"\1 \2",
        cleaned,
    )
    # Also strip any remaining standalone bidi chars
    cleaned_r03 = "".join(c for c in cleaned_r03 if c not in BIDI_CONTROL_CHARS)
    if cleaned_r03 != cleaned:
        rules_triggered.append("R03")
        total_redactions += 1
        max_score = max(max_score, 0.30)
    cleaned = cleaned_r03

    # NFC normalization after bidi stripping (ensures R01 matches on normalized text)
    cleaned = unicodedata.normalize("NFC", cleaned)

    # R04: Excessive invisible whitespace
    cleaned_r04 = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]+", " ", cleaned)
    if cleaned_r04 != cleaned:
        rules_triggered.append("R04")
        total_redactions += 1
        max_score = max(max_score, 0.10)
    cleaned = cleaned_r04

    # R07: HTML / script tag stripping
    cleaned_r07 = cleaned
    for pattern in _HTML_PATTERNS:
        new = pattern.sub("[REDACTED:html]", cleaned_r07)
        if new != cleaned_r07:
            cleaned_r07 = new
            total_redactions += 1
    if cleaned_r07 != cleaned:
        rules_triggered.append("R07")
        max_score = max(max_score, 0.20)
    cleaned = cleaned_r07

    # R05: Base64 payload detection
    b64_pattern = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
    cleaned_r05 = cleaned
    for match in b64_pattern.finditer(cleaned):
        try:
            decoded = base64.b64decode(match.group() + "==").decode("utf-8", errors="ignore")
            # Re-run injection check on decoded payload
            for inj_pat in _INJECTION_PATTERNS:
                if inj_pat.search(decoded):
                    cleaned_r05 = cleaned_r05.replace(match.group(), "[REDACTED:base64_injection]")
                    total_redactions += 1
                    max_score = max(max_score, 0.50)
                    break
        except Exception:  # noqa: S110, BLE001
            pass  # Not valid base64 — ignore
    if cleaned_r05 != cleaned:
        rules_triggered.append("R05")
    cleaned = cleaned_r05

    # R08: Nested escape sequences (URL / unicode)
    cleaned_r08 = cleaned
    # Only strip clearly synthesized escape sequences likely used for bypassing
    cleaned_r08 = re.sub(r"(%[0-9a-fA-F]{2}){3,}", "[REDACTED:escape]", cleaned_r08)
    if cleaned_r08 != cleaned:
        rules_triggered.append("R08")
        total_redactions += 1
        max_score = max(max_score, 0.15)
    cleaned = cleaned_r08

    # R01: Instruction injection patterns
    cleaned_r01 = cleaned
    for pattern in _INJECTION_PATTERNS:
        new = pattern.sub(redact_placeholder, cleaned_r01)
        if new != cleaned_r01:
            cleaned_r01 = new
            total_redactions += 1
    if cleaned_r01 != cleaned:
        rules_triggered.append("R01")
        max_score = max(max_score, 0.40)
    cleaned = cleaned_r01

    # R02: System prompt / role override
    cleaned_r02 = cleaned
    for pattern in _SYSTEM_OVERRIDE_PATTERNS:
        new = pattern.sub("[REDACTED:system_override]", cleaned_r02)
        if new != cleaned_r02:
            cleaned_r02 = new
            total_redactions += 1
    if cleaned_r02 != cleaned:
        rules_triggered.append("R02")
        max_score = max(max_score, 0.35)
    cleaned = cleaned_r02

    # PII detection (email + phone — informational; does not alter text but sets flag)
    if _PII_EMAIL.search(cleaned) or _PII_PHONE.search(cleaned):
        pii_found = True
        # PII in prompts doesn't get auto-redacted here (trace policy handles it)
        # but we set the flag so callers can decide

    # R06: Truncation at max_length
    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length] + " [TRUNCATED]"
        rules_triggered.append("R06")
        total_redactions += 1
        max_score = max(max_score, 0.0)  # Truncation is not itself a security event
        truncated = True

    return SanitizeResult(
        original_text=text,
        cleaned_text=cleaned,
        rules_triggered=list(dict.fromkeys(rules_triggered)),  # preserve order, dedupe
        aggregate_score=round(max_score, 4),
        pii_found=pii_found,
        total_redactions=total_redactions,
        truncated=truncated,
    )
