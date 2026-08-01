"""Persistent preferences and Windows-DPAPI protected secrets."""

from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from threading import RLock
from typing import Any, Callable


DEFAULTS: dict[str, Any] = {
    "mode": "gif",
    "provider": "hermes",
    "model_id": None,
    "scale": 1.0,
    "on_top": True,
    "window_x": None,
    "window_y": None,
    "openai_model": "gpt-4o-mini",
    "openai_base_url": "https://api.openai.com/v1",
    "http_endpoint": "",
    "http_model": "",
    "temperature": 0.8,
    "max_tokens": 1200,
    "voice_input_enabled": True,
    "push_to_talk_hotkey": "Alt+Space",
    "tts_enabled": False,
    "tts_engine": "edge",
    "tts_voice": "zh-CN-XiaoxiaoNeural",
    "piper_model": "",
    "gpt_sovits_url": "",
    "gpt_sovits_voice": "",
    "gpt_sovits_prompt_text": "",
    "gpt_sovits_prompt_lang": "zh",
    "gpt_sovits_text_lang": "zh",
    "idle_animations": True,
    "minimize_to_tray": True,
}

SECRET_NAMES = frozenset({"openai_api_key", "http_api_key"})
_SECRET_PREFIX = "dpapi:"


class SecretProtectionError(RuntimeError):
    """Raised when a secret cannot be protected for the current Windows user."""


class _DataBlob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte)))


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    value = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return value, buffer


def protect_secret(plain: str) -> str:
    """Encrypt a value with Windows DPAPI, scoped to the current user."""

    if os.name != "nt":
        raise SecretProtectionError("API Key 加密存储仅支持 Windows")
    raw = str(plain).encode("utf-8")
    source, source_buffer = _blob(raw)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = (
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    )
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        "HatTip Lab",
        None,
        None,
        None,
        0,
        ctypes.byref(output),
    ):
        raise SecretProtectionError(f"DPAPI 加密失败（错误 {kernel32.GetLastError()}）")
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
        return _SECRET_PREFIX + base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer


def unprotect_secret(encrypted: str) -> str:
    """Decrypt a current-user DPAPI value."""

    if not encrypted:
        return ""
    if os.name != "nt" or not encrypted.startswith(_SECRET_PREFIX):
        raise SecretProtectionError("无法读取 API Key 加密数据")
    try:
        raw = base64.b64decode(encrypted[len(_SECRET_PREFIX) :], validate=True)
    except (ValueError, TypeError) as exc:
        raise SecretProtectionError("API Key 加密数据已损坏") from exc
    source, source_buffer = _blob(raw)
    output = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = (
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    )
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = (wintypes.HLOCAL,)
    kernel32.LocalFree.restype = wintypes.HLOCAL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise SecretProtectionError(f"DPAPI 解密失败（错误 {kernel32.GetLastError()}）")
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecretProtectionError("API Key 解密结果无效") from exc
    finally:
        kernel32.LocalFree(output.pbData)
        del source_buffer


def default_settings_path() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    current = base / "HatTipLab"
    legacy = base / "HermesPet"
    root = legacy if legacy.exists() and not current.exists() else current
    return root / "settings.json"


class SettingsStore:
    def __init__(
        self,
        path: Path | None = None,
        protector: Callable[[str], str] = protect_secret,
        unprotector: Callable[[str], str] = unprotect_secret,
    ) -> None:
        self.path = path or default_settings_path()
        self._lock = RLock()
        self._values = dict(DEFAULTS)
        self._secrets: dict[str, str] = {}
        self._protector = protector
        self._unprotector = unprotector
        self._load()

    def _load(self) -> None:
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return
        if not isinstance(saved, dict):
            return
        self._values.update({key: value for key, value in saved.items() if key in DEFAULTS})
        secrets = saved.get("secrets")
        if isinstance(secrets, dict):
            self._secrets = {
                key: value
                for key, value in secrets.items()
                if key in SECRET_NAMES and isinstance(value, str) and value.startswith(_SECRET_PREFIX)
            }

    def _write_payload_locked(
        self, values: dict[str, Any], secrets: dict[str, str]
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(values)
        if secrets:
            payload["secrets"] = dict(secrets)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def _write_locked(self) -> None:
        self._write_payload_locked(self._values, self._secrets)

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def update(self, **changes: Any) -> dict[str, Any]:
        return self.apply(changes=changes)

    def apply(
        self,
        changes: dict[str, Any] | None = None,
        secret_changes: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Validate/protect all values first, then commit one atomic settings file."""

        with self._lock:
            values = dict(self._values)
            values.update(
                {key: value for key, value in (changes or {}).items() if key in DEFAULTS}
            )
            secrets = dict(self._secrets)
            for name, plain in (secret_changes or {}).items():
                if name not in SECRET_NAMES:
                    raise KeyError(f"Unknown secret: {name}")
                normalized = str(plain).strip()
                if normalized:
                    secrets[name] = self._protector(normalized)
                else:
                    secrets.pop(name, None)
            self._write_payload_locked(values, secrets)
            self._values = values
            self._secrets = secrets
            return dict(self._values)

    def set_secret(self, name: str, plain: str) -> bool:
        if name not in SECRET_NAMES:
            raise KeyError(f"Unknown secret: {name}")
        normalized = str(plain).strip()
        self.apply(secret_changes={name: normalized})
        return bool(normalized)

    def has_secret(self, name: str) -> bool:
        if name not in SECRET_NAMES:
            return False
        with self._lock:
            return bool(self._secrets.get(name))

    def get_secret(self, name: str) -> str:
        if name not in SECRET_NAMES:
            return ""
        with self._lock:
            encrypted = self._secrets.get(name, "")
        if not encrypted:
            return ""
        return self._unprotector(encrypted)
