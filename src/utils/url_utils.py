"""URL extraction, validation and stable normalization helpers."""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
TRAILING_PUNCTUATION = ".,;:!?，。；：！？、\"'）】》〉}>"


class UrlNormalizationError(ValueError):
    """Raised when a token is not a valid HTTP(S) URL."""


def extract_urls(text: str) -> list[str]:
    return [trim_url(token) for token in URL_PATTERN.findall(text) if trim_url(token)]


def trim_url(value: str) -> str:
    return value.strip().rstrip(TRAILING_PUNCTUATION)


def normalize_url(value: str) -> str:
    candidate = trim_url(value)
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise UrlNormalizationError(f"Invalid HTTP(S) URL: {value!r}")
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise UrlNormalizationError(f"Invalid hostname: {value!r}") from error
    try:
        port = parsed.port
    except ValueError as error:
        raise UrlNormalizationError(f"Invalid port: {value!r}") from error
    if port and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
        hostname = f"{hostname}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), hostname, path, parsed.query, ""))
