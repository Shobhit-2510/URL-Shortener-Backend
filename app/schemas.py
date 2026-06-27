"""Pydantic request/response models + URL validation."""
from __future__ import annotations

import ipaddress
import re
from datetime import datetime
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from app.config import get_settings

_ALIAS_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class ShortenRequest(BaseModel):
    url: str = Field(..., description="The long URL to shorten.")
    custom_alias: str | None = Field(
        default=None, description="Optional human-friendly alias instead of a generated code."
    )
    expires_at: datetime | None = Field(
        default=None, description="Optional expiry; redirects return 410 after this time."
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        settings = get_settings()
        v = v.strip()
        if len(v) > settings.max_url_length:
            raise ValueError(f"url exceeds max length of {settings.max_url_length}")
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("url must use http or https scheme")
        if not parsed.netloc:
            raise ValueError("url must include a host")
        # Basic SSRF hygiene: reject links pointing at the loopback/link-local space.
        # (Full SSRF defense also resolves DNS at request time — noted in README.)
        host = parsed.hostname or ""
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_loopback or ip.is_link_local or ip.is_private:
                raise ValueError("url host is not allowed")
        except ValueError:
            # Not a literal IP (a hostname) — allowed at this layer.
            pass
        return v

    @field_validator("custom_alias")
    @classmethod
    def validate_alias(cls, v: str | None) -> str | None:
        if v is None:
            return None
        settings = get_settings()
        v = v.strip()
        if not (settings.custom_alias_min_len <= len(v) <= settings.custom_alias_max_len):
            raise ValueError(
                f"custom_alias must be {settings.custom_alias_min_len}-"
                f"{settings.custom_alias_max_len} characters"
            )
        if not _ALIAS_RE.match(v):
            raise ValueError("custom_alias may only contain letters, digits, '-' and '_'")
        return v


class ShortenResponse(BaseModel):
    code: str
    short_url: str
    long_url: str
    is_custom_alias: bool
    created_at: datetime
    expires_at: datetime | None


class LinkStatsResponse(BaseModel):
    code: str
    long_url: str
    click_count: int
    created_at: datetime
    expires_at: datetime | None
