"""Transactional, encrypted persistence for platform-scoped login states."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from threading import RLock
from typing import Any

from src.auth.models import AuthProfile, AuthProbeResult, AuthStatus, mask_phone
from src.auth.registry import auth_policy_for_key
from src.auth.protection import StateProtector, default_state_protector


_INDEX_VERSION = 1


class AuthStateStoreError(RuntimeError):
    """Raised when encrypted authentication state cannot be persisted."""


class AuthProfileStore:
    def __init__(
        self,
        root: Path,
        *,
        protector: StateProtector | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.index_path = self.root / "auth_index.json"
        self.states_dir = self.root / "states"
        self._protector = protector or default_state_protector()
        self._lock = RLock()

    def profile_for(self, platform_key: str) -> AuthProfile:
        policy = auth_policy_for_key(platform_key)
        with self._lock:
            profiles = self._read_profiles()
            existing = profiles.get(platform_key)
            if existing is not None:
                return existing
        return AuthProfile(
            profile_id=_profile_id(platform_key),
            platform_key=platform_key,
            auth_scope=policy.auth_scope,
        )

    def profiles(self) -> tuple[AuthProfile, ...]:
        with self._lock:
            return tuple(self._read_profiles().values())

    def has_valid_state(self, platform_key: str) -> bool:
        profile = self.profile_for(platform_key)
        return (
            profile.status == AuthStatus.VALID
            and profile.state_filename is not None
            and (self.states_dir / profile.state_filename).is_file()
        )

    def load_state(self, platform_key: str) -> dict[str, Any] | None:
        profile = self.profile_for(platform_key)
        if profile.status != AuthStatus.VALID or not profile.state_filename:
            return None
        state_path = self.states_dir / profile.state_filename
        if not state_path.is_file():
            return None
        try:
            plaintext = self._protector.unprotect(state_path.read_bytes())
            value = json.loads(plaintext.decode("utf-8"))
        except Exception as error:
            raise AuthStateStoreError(
                f"Unable to restore encrypted state for {platform_key}."
            ) from error
        if not isinstance(value, dict) or not isinstance(value.get("cookies", []), list):
            raise AuthStateStoreError(f"Stored state for {platform_key} is invalid.")
        return value

    def commit_validated_state(
        self,
        platform_key: str,
        storage_state: dict[str, Any],
        result: AuthProbeResult,
        *,
        phone: str | None = None,
    ) -> AuthProfile:
        if result.status != AuthStatus.VALID:
            raise ValueError("Only a successfully validated state may be committed.")
        policy = auth_policy_for_key(platform_key)
        profile = self.profile_for(platform_key)
        filename = f"{profile.profile_id}.dpapi"
        encoded = json.dumps(
            storage_state,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        try:
            encrypted = self._protector.protect(encoded)
        except Exception as error:
            raise AuthStateStoreError(
                f"Unable to protect authentication state for {platform_key}."
            ) from error

        with self._lock:
            self.states_dir.mkdir(parents=True, exist_ok=True)
            state_path = self.states_dir / filename
            _atomic_write_bytes(state_path, encrypted)
            updated = replace(
                profile,
                auth_scope=policy.auth_scope,
                status=AuthStatus.VALID,
                masked_phone=mask_phone(phone) or profile.masked_phone,
                validated_at=result.checked_at.astimezone().isoformat(),
                validation_url=policy.probe_url,
                last_error_code=None,
                last_message=result.message,
                state_filename=filename,
            )
            profiles = self._read_profiles()
            profiles[platform_key] = updated
            self._write_profiles(profiles)
            return updated

    def record_result(self, result: AuthProbeResult) -> AuthProfile:
        policy = auth_policy_for_key(result.platform_key)
        profile = self.profile_for(result.platform_key)
        status = result.status
        if status == AuthStatus.GUEST_OK and profile.status == AuthStatus.VALID:
            status = AuthStatus.VALID
        updated = replace(
            profile,
            status=status,
            validated_at=(
                result.checked_at.astimezone().isoformat()
                if status in {AuthStatus.GUEST_OK, AuthStatus.VALID}
                else profile.validated_at
            ),
            # Keep arbitrary user content URLs and query strings out of the
            # plaintext index; the registry probe URL is sufficient metadata.
            validation_url=policy.probe_url,
            last_error_code=result.barrier_code,
            last_message=result.message,
        )
        with self._lock:
            profiles = self._read_profiles()
            profiles[result.platform_key] = updated
            self._write_profiles(profiles)
        return updated

    def delete_state(self, platform_key: str) -> AuthProfile:
        profile = self.profile_for(platform_key)
        with self._lock:
            if profile.state_filename:
                state_path = (self.states_dir / profile.state_filename).resolve()
                if state_path.parent != self.states_dir.resolve():
                    raise AuthStateStoreError("Authentication state path escaped its store.")
                state_path.unlink(missing_ok=True)
            profiles = self._read_profiles()
            reset = AuthProfile(
                profile_id=profile.profile_id,
                platform_key=profile.platform_key,
                auth_scope=profile.auth_scope,
            )
            profiles[platform_key] = reset
            self._write_profiles(profiles)
            return reset

    def _read_profiles(self) -> dict[str, AuthProfile]:
        if not self.index_path.is_file():
            return {}
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
            if int(payload.get("version", 0)) != _INDEX_VERSION:
                raise ValueError("unsupported index version")
            raw_profiles = payload.get("profiles", {})
            return {
                key: AuthProfile.from_dict(value)
                for key, value in raw_profiles.items()
                if isinstance(value, dict)
            }
        except Exception as error:
            raise AuthStateStoreError("Authentication profile index is unreadable.") from error

    def _write_profiles(self, profiles: dict[str, AuthProfile]) -> None:
        payload = {
            "version": _INDEX_VERSION,
            "updated_at": datetime.now().astimezone().isoformat(),
            "profiles": {
                key: profile.to_dict()
                for key, profile in sorted(profiles.items())
            },
        }
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(
            self.index_path,
            json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        )


def _profile_id(platform_key: str) -> str:
    safe = "".join(
        character if character.isalnum() or character in {"-", "_"} else "-"
        for character in platform_key.casefold()
    )
    return f"{safe}-primary"


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
