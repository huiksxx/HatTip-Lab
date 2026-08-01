"""Unified streaming providers for HatTip Lab."""

from __future__ import annotations

import asyncio
import json
import locale
import os
import re
import shutil
import subprocess
import threading
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Callable

import requests


ANSI_ESCAPE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
REPLY_MARKER = "[[HERMES_PET_REPLY]]"
SYSTEM_PROMPT = (
    "你是一个住在 Windows 桌面上的友好小宠物。直接、简洁、有温度地回答；"
    "默认使用中文。当前为聊天模式，不执行外部操作。"
)


class ProviderError(RuntimeError):
    """Raised when a provider cannot be configured, selected, or called."""


class HermesError(ProviderError):
    """Raised when Hermes CLI cannot produce a usable response."""


@dataclass(frozen=True)
class ChatResult:
    reply: str
    emotion: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str
    available: bool
    capabilities: tuple[str, ...] = ("chat", "stream")
    description: str = ""

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["capabilities"] = list(self.capabilities)
        return result


class AgentProvider(ABC):
    @abstractmethod
    def info(self) -> ProviderInfo:
        """Return display and availability metadata without exposing secrets."""

    @abstractmethod
    async def chat(self, text: str, history: list[dict[str, str]]) -> AsyncIterator[str]:
        """Yield text deltas for one assistant response."""
        if False:  # pragma: no cover - marks this method as an async generator
            yield ""


def detect_emotion(text: str) -> str:
    lowered = text.casefold()
    groups = (
        ("angry", ("生气", "愤怒", "讨厌", "angry", "furious")),
        ("sad", ("难过", "遗憾", "抱歉", "伤心", "sad", "sorry")),
        ("surprised", ("居然", "竟然", "惊讶", "哇", "wow", "amazing")),
        ("happy", ("开心", "高兴", "太好了", "当然", "可以", "没问题", "happy", "great")),
    )
    for emotion, words in groups:
        if any(word in lowered for word in words):
            return emotion
    return "neutral"


def normalize_text(text: str) -> str:
    normalized = " ".join(str(text).strip().split())
    if not normalized:
        raise ProviderError("消息不能为空")
    if len(normalized) > 2000:
        raise ProviderError("消息不能超过 2000 个字符")
    return normalized


def normalize_history(history: Iterable[dict[str, Any]] | None) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for item in list(history or [])[-20:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip().lower()
        content = str(item.get("content", "")).strip()
        if role not in {"user", "assistant"} or not content:
            continue
        result.append({"role": role, "content": content[:4000]})
    return result


def _text_chunks(text: str, size: int = 32) -> Iterable[str]:
    for index in range(0, len(text), size):
        yield text[index : index + size]


def _decode_output(data: bytes) -> str:
    for encoding in dict.fromkeys(("utf-8", locale.getpreferredencoding(False), "gb18030")):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _default_runner(command: Sequence[str], timeout: int) -> str:
    creationflags = 0
    startupinfo = None
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        completed = subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
            env=env,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
    except subprocess.TimeoutExpired as exc:
        raise HermesError(f"Hermes 在 {timeout} 秒内没有回复") from exc
    except OSError as exc:
        raise HermesError(f"无法启动 Hermes CLI：{exc}") from exc
    stdout = _decode_output(completed.stdout).strip()
    stderr = _decode_output(completed.stderr).strip()
    if completed.returncode != 0:
        details = stderr or stdout or f"退出码 {completed.returncode}"
        raise HermesError(f"Hermes 调用失败：{details[-800:]}")
    if not stdout:
        raise HermesError("Hermes 返回了空回复")
    return stdout


class HermesProvider(AgentProvider):
    def __init__(
        self,
        executable: str | None = None,
        timeout: int = 120,
        runner: Callable[[Sequence[str], int], str] | None = None,
    ) -> None:
        self.executable = executable or shutil.which("hermes") or "hermes"
        self.timeout = timeout
        self._runner = runner or _default_runner
        self._lock = threading.Lock()

    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="hermes",
            name="Hermes CLI",
            available=bool(shutil.which("hermes") or self._runner is not _default_runner),
            capabilities=("chat", "stream", "history"),
            description="调用本机 hermes chat -q",
        )

    @staticmethod
    def _clean_reply(reply: str) -> str:
        cleaned = ANSI_ESCAPE.sub("", reply).strip()
        if REPLY_MARKER in cleaned:
            cleaned = cleaned.rsplit(REPLY_MARKER, 1)[1].strip()
        else:
            cleaned = re.sub(
                r"^Warning:\s+Unknown toolsets:.*?(?:\r?\n)+", "", cleaned, flags=re.IGNORECASE
            ).strip()
            if "Reasoning " in cleaned and "┌" in cleaned:
                nonempty = [line.strip() for line in cleaned.splitlines() if line.strip()]
                if nonempty:
                    cleaned = nonempty[-1]
        cleaned = re.sub(
            r"\n(?:Session(?: ID)?|会话(?: ID)?)[：:]?\s*[\w-]+\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        return cleaned[:4000]

    def _prompt(self, text: str, history: list[dict[str, str]]) -> str:
        recent = "\n".join(
            f"{'用户' if item['role'] == 'user' else '你'}：{item['content'][:1200]}"
            for item in history[-12:]
        )
        context = f"\n最近对话：\n{recent}\n" if recent else ""
        return (
            f"{SYSTEM_PROMPT} 回复尽量控制在 300 字以内。"
            f"最终回答必须以 {REPLY_MARKER} 开头，标记之前不要放最终回答内容。"
            f"{context}\n用户现在说：{text}"
        )

    async def chat(self, text: str, history: list[dict[str, str]]) -> AsyncIterator[str]:
        normalized = normalize_text(text)
        command = (
            self.executable,
            "chat",
            "-q",
            self._prompt(normalized, normalize_history(history)),
            "--quiet",
            "-t",
            "",
            "--source",
            "tool",
            "--max-turns",
            "1",
        )

        def call() -> str:
            with self._lock:
                return self._clean_reply(self._runner(command, self.timeout))

        reply = await asyncio.to_thread(call)
        if not reply:
            raise HermesError("Hermes 返回了空回复")
        for chunk in _text_chunks(reply):
            yield chunk


class MockProvider(AgentProvider):
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="mock",
            name="界面测试",
            available=True,
            capabilities=("chat", "stream"),
            description="不联网的即时假回复",
        )

    async def chat(self, text: str, history: list[dict[str, str]]) -> AsyncIterator[str]:
        normalized = normalize_text(text)
        reply = f"收到啦！你刚才说的是“{normalized[:80]}”。对话流程运行正常。"
        for chunk in _text_chunks(reply, 10):
            yield chunk


class OpenAIResponsesProvider(AgentProvider):
    """Streaming Responses API adapter with no persistent server-side storage."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.8,
        max_tokens: int = 1200,
        http_post: Callable[..., Any] | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.temperature = max(0.0, min(float(temperature), 2.0))
        self.max_tokens = max(64, min(int(max_tokens), 128000))
        self._post = http_post or requests.post

    def info(self) -> ProviderInfo:
        configured = bool(self.api_key and self.model)
        return ProviderInfo(
            id="openai",
            name="OpenAI / GPT",
            available=configured,
            capabilities=("chat", "stream", "history"),
            description=f"Responses API · {self.model}" if configured else "请在设置中配置 OpenAI API Key",
        )

    @staticmethod
    def _output_text(payload: dict[str, Any]) -> str:
        direct = payload.get("output_text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        parts: list[str] = []
        for item in payload.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    value = content.get("text")
                    if isinstance(value, str):
                        parts.append(value)
        return "\n".join(parts).strip()

    async def chat(self, text: str, history: list[dict[str, str]]) -> AsyncIterator[str]:
        normalized = normalize_text(text)
        if not self.api_key:
            raise ProviderError("尚未配置 OpenAI API Key")
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": SYSTEM_PROMPT,
            "input": normalize_history(history) + [{"role": "user", "content": normalized}],
            "stream": True,
            "store": False,
            "temperature": self.temperature,
            "max_output_tokens": self.max_tokens,
        }
        try:
            response = await asyncio.to_thread(
                self._post,
                f"{self.base_url}/responses",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=body,
                stream=True,
                timeout=120,
            )
            response.raise_for_status()
            yielded = False
            if hasattr(response, "iter_lines"):
                for raw_line in response.iter_lines(decode_unicode=True):
                    line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    if event.get("type") == "response.output_text.delta":
                        delta = event.get("delta")
                        if isinstance(delta, str) and delta:
                            yielded = True
                            yield delta
                    elif event.get("type") in {"error", "response.failed"}:
                        details = event.get("message") or event.get("error", {}).get("message")
                        raise ProviderError(f"OpenAI API 返回错误：{details or '未知错误'}")
            if not yielded:
                payload = response.json()
                reply = self._output_text(payload)
                if not reply:
                    raise ProviderError("OpenAI API 返回了空回复")
                for chunk in _text_chunks(reply):
                    yield chunk
        except ProviderError:
            raise
        except requests.RequestException as exc:
            message = getattr(getattr(exc, "response", None), "text", "")
            details = str(message)[:400] if message else str(exc)
            raise ProviderError(f"OpenAI API 请求失败：{details}") from exc
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("OpenAI API 返回了无法识别的数据") from exc


class HttpProvider(AgentProvider):
    """OpenAI-compatible Chat Completions adapter for local or hosted endpoints."""

    def __init__(
        self,
        endpoint: str = "",
        api_key: str = "",
        model: str = "",
        temperature: float = 0.8,
        max_tokens: int = 1200,
        http_post: Callable[..., Any] | None = None,
    ) -> None:
        self.endpoint = str(endpoint).strip().rstrip("/")
        self.api_key = str(api_key).strip()
        self.model = str(model).strip()
        self.temperature = max(0.0, min(float(temperature), 2.0))
        self.max_tokens = max(64, min(int(max_tokens), 128000))
        self._post = http_post or requests.post

    @property
    def chat_url(self) -> str:
        if self.endpoint.endswith("/chat/completions"):
            return self.endpoint
        if self.endpoint.endswith("/v1"):
            return f"{self.endpoint}/chat/completions"
        return f"{self.endpoint}/v1/chat/completions"

    def info(self) -> ProviderInfo:
        configured = bool(self.endpoint and self.model)
        return ProviderInfo(
            id="http",
            name="HTTP / OpenAI 兼容",
            available=configured,
            capabilities=("chat", "stream", "history"),
            description=f"{self.model} · {self.endpoint}" if configured else "请在设置中填写服务地址和模型 ID",
        )

    async def chat(self, text: str, history: list[dict[str, str]]) -> AsyncIterator[str]:
        normalized = normalize_text(text)
        if not self.endpoint or not self.model:
            raise ProviderError("尚未配置 HTTP Provider 的地址和模型 ID")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
            + normalize_history(history)
            + [{"role": "user", "content": normalized}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        try:
            response = await asyncio.to_thread(
                self._post,
                self.chat_url,
                headers=headers,
                json=body,
                stream=True,
                timeout=120,
            )
            response.raise_for_status()
            yielded = False
            if hasattr(response, "iter_lines"):
                for raw_line in response.iter_lines(decode_unicode=True):
                    line = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    event = json.loads(data)
                    choices = event.get("choices") or []
                    delta = choices[0].get("delta", {}).get("content") if choices else None
                    if isinstance(delta, str) and delta:
                        yielded = True
                        yield delta
            if not yielded:
                payload = response.json()
                choices = payload.get("choices") or []
                reply = choices[0].get("message", {}).get("content") if choices else ""
                if not isinstance(reply, str) or not reply.strip():
                    raise ProviderError("HTTP Provider 返回了空回复")
                for chunk in _text_chunks(reply.strip()):
                    yield chunk
        except ProviderError:
            raise
        except requests.RequestException as exc:
            message = getattr(getattr(exc, "response", None), "text", "")
            details = str(message)[:400] if message else str(exc)
            raise ProviderError(f"HTTP Provider 请求失败：{details}") from exc
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ProviderError("HTTP Provider 返回了无法识别的数据") from exc


class ProviderRegistry:
    """Thread-safe registry with bounded per-provider chat history."""

    def __init__(self, providers: Iterable[AgentProvider] = (), default_id: str | None = None):
        self._providers: dict[str, AgentProvider] = {}
        self._histories: dict[str, deque[dict[str, str]]] = {}
        self._lock = threading.RLock()
        for provider in providers:
            self.register(provider)
        if not self._providers:
            raise ValueError("At least one provider is required")
        self.default_id = default_id or next(iter(self._providers))
        if self.default_id not in self._providers:
            raise ValueError(f"Unknown default provider: {self.default_id}")

    def register(self, provider: AgentProvider) -> None:
        provider_id = provider.info().id
        with self._lock:
            self._providers[provider_id] = provider
            self._histories.setdefault(provider_id, deque(maxlen=20))

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            providers = list(self._providers.values())
        return [provider.info().to_dict() for provider in providers]

    def get(self, provider_id: str | None, require_available: bool = True) -> AgentProvider:
        selected = provider_id or self.default_id
        with self._lock:
            provider = self._providers.get(selected)
        if provider is None:
            raise ProviderError(f"未知智能体：{selected}")
        info = provider.info()
        if require_available and not info.available:
            raise ProviderError(f"智能体当前不可用：{info.name}")
        return provider

    def has_available(self, provider_id: str) -> bool:
        try:
            return self.get(provider_id).info().available
        except ProviderError:
            return False

    async def stream(
        self,
        provider_id: str | None,
        text: str,
        history: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        normalized = normalize_text(text)
        provider = self.get(provider_id)
        selected = provider.info().id
        with self._lock:
            saved_history = list(self._histories.get(selected, ()))
        context = normalize_history(history) if history is not None else saved_history
        pieces: list[str] = []
        try:
            async for piece in provider.chat(normalized, context):
                chunk = str(piece)
                if not chunk:
                    continue
                pieces.append(chunk)
                yield chunk
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"{provider.info().name} 调用失败：{exc}") from exc
        reply = "".join(pieces).strip()
        if not reply:
            raise ProviderError(f"{provider.info().name} 返回了空回复")
        with self._lock:
            stored = self._histories.setdefault(selected, deque(maxlen=20))
            stored.append({"role": "user", "content": normalized})
            stored.append({"role": "assistant", "content": reply[:4000]})

    async def collect(
        self,
        provider_id: str | None,
        text: str,
        history: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        chunks = [chunk async for chunk in self.stream(provider_id, text, history)]
        reply = "".join(chunks).strip()
        return ChatResult(reply=reply, emotion=detect_emotion(reply))

    def ask(
        self,
        provider_id: str | None,
        text: str,
        history: list[dict[str, Any]] | None = None,
    ) -> ChatResult:
        return asyncio.run(self.collect(provider_id, text, history))


class SingleBackendProvider(AgentProvider):
    """Compatibility wrapper for embedders that still expose a synchronous ask()."""

    def __init__(self, backend: object):
        self.backend = backend

    def info(self) -> ProviderInfo:
        return ProviderInfo(id="default", name="Default", available=True)

    async def chat(self, text: str, history: list[dict[str, str]]) -> AsyncIterator[str]:
        result = await asyncio.to_thread(self.backend.ask, normalize_text(text))  # type: ignore[attr-defined]
        reply = result.reply if hasattr(result, "reply") else str(result)
        for chunk in _text_chunks(reply):
            yield chunk
