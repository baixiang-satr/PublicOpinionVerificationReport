"""Secure, platform-scoped authentication state management."""

from src.auth.models import AuthProfile, AuthProbeResult, AuthStatus, PlatformAuthPolicy
from src.auth.registry import AUTH_POLICIES, auth_policy_for_key, auth_policy_for_url
from src.auth.store import AuthProfileStore

__all__ = [
    "AUTH_POLICIES",
    "AuthProfile",
    "AuthProfileStore",
    "AuthProbeResult",
    "AuthStatus",
    "PlatformAuthPolicy",
    "auth_policy_for_key",
    "auth_policy_for_url",
]
