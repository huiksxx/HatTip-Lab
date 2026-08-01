"""Windows push-to-talk plus Edge, Piper, and GPT-SoVITS speech support."""

from __future__ import annotations

import asyncio
import ctypes
import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests


HOTKEY_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    **{chr(code): code for code in range(ord("A"), ord("Z") + 1)},
    **{str(number): ord(str(number)) for number in range(10)},
    **{f"f{number}": 0x6F + number for number in range(1, 13)},
}
HOTKEY_MODIFIERS = {
    "alt": 0x12,
    "ctrl": 0x11,
    "control": 0x11,
    "shift": 0x10,
    "win": 0x5B,
    "windows": 0x5B,
}


def parse_hotkey(value: str) -> tuple[frozenset[int], int]:
    parts = [part.strip().lower() for part in str(value).split("+") if part.strip()]
    if len(parts) < 2:
        raise ValueError("快捷键至少需要一个修饰键，例如 Alt+Space")
    modifiers: set[int] = set()
    main: int | None = None
    for part in parts:
        if part in HOTKEY_MODIFIERS:
            modifiers.add(HOTKEY_MODIFIERS[part])
        elif part in HOTKEY_KEYS and main is None:
            main = HOTKEY_KEYS[part]
        else:
            raise ValueError(f"不支持的快捷键：{value}")
    if not modifiers or main is None:
        raise ValueError("快捷键必须包含修饰键和一个主键")
    return frozenset(modifiers), main


class PushToTalkHotkey:
    """Low-level keyboard hook that exposes key-down and key-up separately."""

    def __init__(self, on_state: Callable[[bool], None]) -> None:
        self._on_state = on_state
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._hook = None
        self._callback = None
        self._stop_event = threading.Event()
        self._modifiers: frozenset[int] = frozenset()
        self._main_key = 0
        self._pressed: set[int] = set()
        self._active = False
        self._lock = threading.RLock()

    @staticmethod
    def validate(value: str) -> str:
        parse_hotkey(value)
        return "+".join(part.strip().title() for part in str(value).split("+") if part.strip())

    @property
    def available(self) -> bool:
        return os.name == "nt"

    def start(self, hotkey: str) -> bool:
        if not self.available:
            return False
        modifiers, main_key = parse_hotkey(hotkey)
        self.stop()
        with self._lock:
            self._modifiers = modifiers
            self._main_key = main_key
            self._pressed.clear()
            self._active = False
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run, name="hattip-lab-hotkey", daemon=True)
            self._thread.start()
        return True

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            thread_id = self._thread_id
            self._thread = None
            self._stop_event.set()
        if os.name == "nt" and thread_id:
            ctypes.windll.user32.PostThreadMessageW(thread_id, 0x0012, 0, 0)
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)
        self._set_active(False)

    def _set_active(self, active: bool) -> None:
        with self._lock:
            if self._active == active:
                return
            self._active = active
        threading.Thread(target=self._on_state, args=(active,), daemon=True).start()

    def _run(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self._thread_id = kernel32.GetCurrentThreadId()

        class KbdLlHookStruct(ctypes.Structure):
            _fields_ = (
                ("vkCode", wintypes.DWORD),
                ("scanCode", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
            )

        low_level_proc = ctypes.WINFUNCTYPE(
            ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
        )
        user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int,
            low_level_proc,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        )
        user32.SetWindowsHookExW.restype = wintypes.HANDLE
        user32.CallNextHookEx.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        user32.CallNextHookEx.restype = ctypes.c_longlong
        user32.UnhookWindowsHookEx.argtypes = (wintypes.HANDLE,)
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        key_down = {0x0100, 0x0104}
        key_up = {0x0101, 0x0105}

        def callback(code: int, message: int, data: int) -> int:
            if code < 0:
                return user32.CallNextHookEx(self._hook, code, message, data)
            event = ctypes.cast(data, ctypes.POINTER(KbdLlHookStruct)).contents
            key = int(event.vkCode)
            suppress = False
            if int(message) in key_down:
                self._pressed.add(key)
                if key == self._main_key and self._modifiers.issubset(self._pressed):
                    self._set_active(True)
                    suppress = True
                elif self._active and key in self._modifiers:
                    suppress = True
            elif int(message) in key_up:
                was_active = self._active
                if key == self._main_key or (was_active and key in self._modifiers):
                    self._set_active(False)
                    suppress = was_active or key == self._main_key
                self._pressed.discard(key)
            if suppress:
                return 1
            return user32.CallNextHookEx(self._hook, code, message, data)

        self._callback = low_level_proc(callback)
        self._hook = user32.SetWindowsHookExW(13, self._callback, kernel32.GetModuleHandleW(None), 0)
        if not self._hook:
            self._thread_id = 0
            return
        message = wintypes.MSG()
        try:
            while not self._stop_event.is_set() and user32.GetMessageW(
                ctypes.byref(message), None, 0, 0
            ) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._callback = None
            self._thread_id = 0


@dataclass(frozen=True)
class SpeechArtifact:
    token: str
    extension: str
    engine: str
    fallback: bool = False


class SpeechUnavailable(RuntimeError):
    """Raised only after every configured speech backend has failed."""


EMOTION_LABELS = {
    "happy": "高兴",
    "sad": "悲伤",
    "surprised": "惊讶",
    "angry": "愤怒",
    "neutral": "平静",
    "thinking": "平静",
}

PIPER_LENGTH_SCALE = {
    "happy": 0.91,
    "sad": 1.18,
    "surprised": 0.94,
    "angry": 0.92,
    "neutral": 1.0,
    "thinking": 1.0,
}


class PiperTTSService:
    """Generate WAV files with a user-approved, local Piper voice model."""

    TOKEN = re.compile(r"^[a-f0-9]{32}$")
    AUDIO_EXTENSIONS = frozenset({"mp3", "wav", "ogg", "aac"})

    def __init__(
        self,
        output_dir: Path,
        model_path: Path,
        voice_loader: Callable[[str], Any] | None = None,
        runtime_path: Path | None = None,
    ) -> None:
        self.output_dir = output_dir
        self._lock = threading.Lock()
        self.model_path = Path(model_path)
        self._voice_loader = voice_loader
        self.runtime_path = Path(runtime_path) if runtime_path else None
        self._voice: Any | None = None
        self._voice_lock = threading.Lock()

    @property
    def available(self) -> bool:
        runtime_ready = bool(self.runtime_path and self.runtime_path.is_file())
        package_ready = self._voice_loader is not None or importlib.util.find_spec("piper") is not None
        return bool((runtime_ready or package_ready) and self.model_path.is_file() and self.model_path.with_suffix(".onnx.json").is_file())

    def configure(self, model_path: str = "", runtime_path: str = "", **_: Any) -> None:
        candidate = Path(str(model_path)).expanduser() if model_path else self.model_path
        if candidate != self.model_path:
            with self._voice_lock:
                self.model_path = candidate
                self._voice = None
        if runtime_path:
            self.runtime_path = Path(str(runtime_path))

    def _load_voice(self) -> Any:
        with self._voice_lock:
            if self._voice is not None:
                return self._voice
            if not self.available:
                raise RuntimeError("Piper 组件或本地语音模型不可用")
            if self._voice_loader is not None:
                self._voice = self._voice_loader(str(self.model_path))
                return self._voice
            try:
                from piper import PiperVoice
            except ImportError:
                from piper.voice import PiperVoice
            self._voice = PiperVoice.load(str(self.model_path))
            return self._voice

    @staticmethod
    def _write_voice(voice: Any, message: str, wav_file: Any, emotion: str) -> None:
        length_scale = PIPER_LENGTH_SCALE.get(emotion, PIPER_LENGTH_SCALE["neutral"])
        noise_scale = 0.72 if emotion == "surprised" else 0.667
        noise_w = 0.9 if emotion == "surprised" else 0.8
        if hasattr(voice, "synthesize_wav"):
            try:
                from piper import SynthesisConfig

                config = SynthesisConfig(
                    length_scale=length_scale,
                    noise_scale=noise_scale,
                    noise_w_scale=noise_w,
                )
                voice.synthesize_wav(message, wav_file, syn_config=config)
                return
            except (ImportError, TypeError):
                pass
        synthesize = getattr(voice, "synthesize", None)
        if synthesize is None:
            raise RuntimeError("当前 Piper 版本不支持 WAV 合成")
        parameters = inspect.signature(synthesize).parameters
        options: dict[str, float] = {}
        for name, value in (
            ("length_scale", length_scale),
            ("noise_scale", noise_scale),
            ("noise_w", noise_w),
            ("noise_w_scale", noise_w),
        ):
            if name in parameters:
                options[name] = value
        synthesize(message, wav_file, **options)

    def synthesize_result(
        self, text: str, voice: str = "", emotion: str = "neutral"
    ) -> SpeechArtifact:
        del voice
        message = " ".join(str(text).strip().split())[:1600]
        if not message:
            raise ValueError("朗读内容为空")
        token = uuid.uuid4().hex
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = self.output_dir / f"{token}.wav"
        temporary = self.output_dir / f"{token}.tmp.wav"
        try:
            if self.runtime_path and self.runtime_path.is_file() and self._voice_loader is None:
                length_scale = PIPER_LENGTH_SCALE.get(
                    str(emotion).casefold(), PIPER_LENGTH_SCALE["neutral"]
                )
                noise_scale = 0.72 if str(emotion).casefold() == "surprised" else 0.667
                noise_w = 0.9 if str(emotion).casefold() == "surprised" else 0.8
                command = [
                    str(self.runtime_path),
                    "--model",
                    str(self.model_path),
                    "--config",
                    str(self.model_path.with_suffix(".onnx.json")),
                    "--output_file",
                    str(temporary),
                    "--length_scale",
                    str(length_scale),
                    "--noise_scale",
                    str(noise_scale),
                    "--noise_w",
                    str(noise_w),
                ]
                with self._lock:
                    result = subprocess.run(
                        command,
                        input=(message + "\n").encode("utf-8"),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        cwd=str(self.runtime_path.parent),
                        timeout=120,
                        check=False,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                if result.returncode:
                    detail = result.stderr.decode("utf-8", errors="replace").strip()[-500:]
                    raise RuntimeError(f"Piper 运行失败：{detail or result.returncode}")
            else:
                model = self._load_voice()
                with self._lock, wave.open(str(temporary), "wb") as wav_file:
                    self._write_voice(model, message, wav_file, str(emotion).casefold())
            if not temporary.is_file() or temporary.stat().st_size <= 44:
                raise RuntimeError("Piper 没有生成有效音频")
            os.replace(temporary, target)
            self._cleanup()
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return SpeechArtifact(token=token, extension="wav", engine="piper")

    def synthesize(self, text: str, voice: str = "", emotion: str = "neutral") -> str:
        return self.synthesize_result(text, voice, emotion).token

    def status(self, probe: bool = False) -> dict[str, Any]:
        del probe
        return {
            "available": self.available,
            "engine": "piper",
            "piper_available": self.available,
            "piper_model": self.model_path.name if self.model_path.is_file() else "",
            "piper_runtime": "native" if self.runtime_path and self.runtime_path.is_file() else "python",
            "gpt_sovits_configured": False,
            "gpt_sovits_online": False,
        }

    def resolve(self, token: str, extension: str | None = None) -> Path | None:
        if not self.TOKEN.fullmatch(str(token)):
            return None
        extensions = (extension,) if extension else tuple(self.AUDIO_EXTENSIONS)
        for suffix in extensions:
            if suffix not in self.AUDIO_EXTENSIONS:
                continue
            candidate = (self.output_dir / f"{token}.{suffix}").resolve()
            try:
                candidate.relative_to(self.output_dir.resolve())
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
        return None

    def _cleanup(self) -> None:
        cutoff = time.time() - 24 * 60 * 60
        for suffix in self.AUDIO_EXTENSIONS:
            for path in self.output_dir.glob(f"*.{suffix}"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                except OSError:
                    continue


class TtsService:
    """Shared local-audio cache contract for concrete TTS backends."""

    TOKEN = re.compile(r"^[a-f0-9]{32}$")
    AUDIO_EXTENSIONS = frozenset({"mp3", "wav", "ogg", "aac"})

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return False

    def synthesize(self, text: str, voice: str, emotion: str = "neutral") -> str:
        del text, voice, emotion
        raise SpeechUnavailable("尚未配置本地语音引擎")

    def synthesize_result(
        self, text: str, voice: str, emotion: str = "neutral"
    ) -> SpeechArtifact:
        self.synthesize(text, voice, emotion)
        raise SpeechUnavailable("尚未配置本地语音引擎")

    def resolve(self, token: str, extension: str | None = None) -> Path | None:
        if not self.TOKEN.fullmatch(str(token)):
            return None
        extensions = (extension,) if extension else tuple(self.AUDIO_EXTENSIONS)
        for suffix in extensions:
            if suffix not in self.AUDIO_EXTENSIONS:
                continue
            candidate = (self.output_dir / f"{token}.{suffix}").resolve()
            try:
                candidate.relative_to(self.output_dir.resolve())
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
        return None

    def status(self, probe: bool = False) -> dict[str, Any]:
        return {
            "available": False,
            "engine": "none",
            "gpt_sovits_configured": False,
            "gpt_sovits_online": False,
        }

    def configure(self, **_: Any) -> None:
        """Compatibility hook used by the cascading service."""

    def _cleanup(self) -> None:
        cutoff = time.time() - 24 * 60 * 60
        for suffix in self.AUDIO_EXTENSIONS:
            for path in self.output_dir.glob(f"*.{suffix}"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                except OSError:
                    continue


EDGE_EMOTION_PROSODY = {
    "happy": ("+8%", "+3Hz"),
    "sad": ("-12%", "-3Hz"),
    "surprised": ("+12%", "+5Hz"),
    "angry": ("+5%", "+2Hz"),
    "neutral": ("+0%", "+0Hz"),
    "thinking": ("-4%", "+0Hz"),
}


class EdgeTtsService(TtsService):
    """Generate an MP3 with Microsoft Edge's online speech service."""

    def __init__(
        self,
        output_dir: Path,
        communicate_factory: Callable[..., Any] | None = None,
        *,
        enabled: bool = True,
    ) -> None:
        super().__init__(output_dir)
        self._communicate_factory = communicate_factory
        self.enabled = bool(enabled)

    @property
    def available(self) -> bool:
        return bool(
            self.enabled
            and (
                self._communicate_factory is not None
                or importlib.util.find_spec("edge_tts") is not None
            )
        )

    def synthesize_result(
        self, text: str, voice: str = "", emotion: str = "neutral"
    ) -> SpeechArtifact:
        message = " ".join(str(text).strip().split())[:1600]
        if not message:
            raise ValueError("朗读内容为空")
        if not self.available:
            raise RuntimeError("Edge 在线语音组件不可用")
        safe_voice = str(voice).strip() or "zh-CN-XiaoxiaoNeural"
        rate, pitch = EDGE_EMOTION_PROSODY.get(
            str(emotion).casefold(), EDGE_EMOTION_PROSODY["neutral"]
        )
        factory = self._communicate_factory
        if factory is None:
            import edge_tts

            factory = edge_tts.Communicate
        token = uuid.uuid4().hex
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = self.output_dir / f"{token}.mp3"
        temporary = self.output_dir / f"{token}.tmp.mp3"

        async def generate() -> None:
            communicate = factory(message, safe_voice, rate=rate, pitch=pitch)
            await communicate.save(str(temporary))

        try:
            with self._lock:
                asyncio.run(generate())
                if not temporary.is_file() or temporary.stat().st_size == 0:
                    raise RuntimeError("Edge 在线语音没有生成有效音频")
                os.replace(temporary, target)
                self._cleanup()
        finally:
            temporary.unlink(missing_ok=True)
        return SpeechArtifact(token=token, extension="mp3", engine="edge")

    def synthesize(self, text: str, voice: str = "", emotion: str = "neutral") -> str:
        return self.synthesize_result(text, voice, emotion).token

    def status(self, probe: bool = False) -> dict[str, Any]:
        del probe
        return {
            "available": self.available,
            "engine": "edge",
            "edge_available": self.available,
            "edge_voice": "zh-CN-XiaoxiaoNeural",
        }


class GPTSoVITSService(TtsService):
    """Adapter for a separately deployed GPT-SoVITS-compatible local API."""

    MAX_AUDIO_BYTES = 64 * 1024 * 1024

    def __init__(
        self,
        output_dir: Path,
        api_url: str = "http://127.0.0.1:9880",
        ref_audio_path: str = "",
        prompt_text: str = "",
        prompt_lang: str = "zh",
        text_lang: str = "zh",
        voice: str = "",
        http_post: Callable[..., Any] | None = None,
        http_get: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(output_dir)
        self.api_url = str(api_url).strip().rstrip("/")
        self.ref_audio_path = str(ref_audio_path).strip()
        self.prompt_text = str(prompt_text).strip()
        self.prompt_lang = str(prompt_lang).strip() or "zh"
        self.text_lang = str(text_lang).strip() or "zh"
        self.voice = str(voice).strip()
        self._post = http_post or requests.post
        self._get = http_get or requests.get
        self._probe_cache = (0.0, False)

    @property
    def tts_url(self) -> str:
        return self.api_url if self.api_url.endswith("/tts") else f"{self.api_url}/tts"

    @property
    def available(self) -> bool:
        return bool(self.api_url)

    def configure(
        self,
        api_url: str = "",
        ref_audio_path: str = "",
        prompt_text: str = "",
        prompt_lang: str = "zh",
        text_lang: str = "zh",
        voice: str = "",
        **_: Any,
    ) -> None:
        with self._lock:
            self.api_url = str(api_url).strip().rstrip("/")
            self.ref_audio_path = str(ref_audio_path).strip()
            self.prompt_text = str(prompt_text).strip()
            self.prompt_lang = str(prompt_lang).strip() or "zh"
            self.text_lang = str(text_lang).strip() or "zh"
            self.voice = str(voice).strip()
            self._probe_cache = (0.0, False)

    def online(self, force: bool = False) -> bool:
        if not self.available:
            return False
        checked_at, result = self._probe_cache
        if not force and time.monotonic() - checked_at < 5:
            return result
        base = self.api_url[:-4] if self.api_url.endswith("/tts") else self.api_url
        result = False
        for path in ("/health", "/openapi.json"):
            try:
                response = self._get(f"{base}{path}", timeout=0.7)
                if int(getattr(response, "status_code", 0)) < 500:
                    result = True
                    break
            except (requests.RequestException, OSError):
                continue
        self._probe_cache = (time.monotonic(), result)
        return result

    def _payload(self, message: str, emotion: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "text": message,
            "emotion": EMOTION_LABELS.get(emotion, EMOTION_LABELS["neutral"]),
        }
        if self.voice:
            payload["voice"] = self.voice
        if self.ref_audio_path:
            payload.update(
                {
                    "text_lang": self.text_lang,
                    "ref_audio_path": self.ref_audio_path,
                    "prompt_text": self.prompt_text,
                    "prompt_lang": self.prompt_lang,
                    "text_split_method": "cut5",
                    "media_type": "wav",
                    "streaming_mode": False,
                }
            )
        return payload

    @staticmethod
    def _response_bytes(response: Any) -> bytes:
        if hasattr(response, "iter_content"):
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > GPTSoVITSService.MAX_AUDIO_BYTES:
                    raise RuntimeError("GPT-SoVITS 返回的音频过大")
                chunks.append(bytes(chunk))
            return b"".join(chunks)
        data = bytes(getattr(response, "content", b""))
        if len(data) > GPTSoVITSService.MAX_AUDIO_BYTES:
            raise RuntimeError("GPT-SoVITS 返回的音频过大")
        return data

    def synthesize_result(
        self, text: str, voice: str = "", emotion: str = "neutral"
    ) -> SpeechArtifact:
        message = " ".join(str(text).strip().split())[:1600]
        if not message:
            raise ValueError("朗读内容为空")
        if not self.available:
            raise RuntimeError("尚未配置 GPT-SoVITS 服务地址")
        if voice:
            self.voice = str(voice).strip()
        try:
            response = self._post(
                self.tts_url,
                json=self._payload(message, str(emotion).casefold()),
                stream=True,
                timeout=(2, 120),
            )
            response.raise_for_status()
            content_type = str(getattr(response, "headers", {}).get("Content-Type", ""))
            audio = self._response_bytes(response)
        except requests.RequestException as exc:
            raise RuntimeError(f"GPT-SoVITS 请求失败：{exc}") from exc
        if not audio or (
            "audio/" not in content_type.casefold()
            and not audio.startswith((b"RIFF", b"OggS", b"ID3", b"\xff\xfb", b"\xff\xf3"))
        ):
            raise RuntimeError("GPT-SoVITS 没有返回有效音频")
        extension = "wav"
        lowered = content_type.casefold()
        if "ogg" in lowered or audio.startswith(b"OggS"):
            extension = "ogg"
        elif "aac" in lowered:
            extension = "aac"
        elif "mpeg" in lowered or audio.startswith((b"ID3", b"\xff\xfb", b"\xff\xf3")):
            extension = "mp3"
        token = uuid.uuid4().hex
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = self.output_dir / f"{token}.{extension}"
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_bytes(audio)
        os.replace(temporary, target)
        self._cleanup()
        return SpeechArtifact(token=token, extension=extension, engine="gpt-sovits")

    def synthesize(self, text: str, voice: str = "", emotion: str = "neutral") -> str:
        return self.synthesize_result(text, voice, emotion).token

    def status(self, probe: bool = False) -> dict[str, Any]:
        return {
            "available": self.available,
            "engine": "gpt-sovits",
            "gpt_sovits_configured": self.available,
            "gpt_sovits_online": self.online(force=probe) if self.available else False,
            "edge_available": False,
        }


class ResilientTtsService(TtsService):
    """Honor the preferred engine and cascade through the other speech layers."""

    def __init__(
        self,
        output_dir: Path,
        piper_model: Path,
        piper_voice_loader: Callable[[str], Any] | None = None,
        piper_runtime: Path | None = None,
        edge_communicate_factory: Callable[..., Any] | None = None,
        edge_enabled: bool = True,
    ) -> None:
        super().__init__(output_dir)
        self.gpt_sovits = GPTSoVITSService(output_dir)
        self.piper = PiperTTSService(output_dir, piper_model, piper_voice_loader, piper_runtime)
        self.edge = EdgeTtsService(
            output_dir, edge_communicate_factory, enabled=edge_enabled
        )
        self.engine = "edge"
        self._cascade_lock = threading.Lock()

    @property
    def available(self) -> bool:
        return self.edge.available or self.piper.available or self.gpt_sovits.available

    def configure(self, **options: Any) -> None:
        engine = str(options.get("engine", "edge")).casefold()
        if engine == "auto":
            engine = "gpt-sovits"
        self.engine = engine if engine in {"edge", "piper", "gpt-sovits"} else "edge"
        self.piper.configure(**options)
        self.gpt_sovits.configure(**options)

    def synthesize_result(
        self, text: str, voice: str = "", emotion: str = "neutral"
    ) -> SpeechArtifact:
        failures: list[str] = []
        orders = {
            "edge": ("edge", "piper", "gpt-sovits"),
            "piper": ("piper", "edge", "gpt-sovits"),
            "gpt-sovits": ("gpt-sovits", "piper", "edge"),
        }
        with self._cascade_lock:
            for engine in orders[self.engine]:
                service: TtsService
                service_voice = voice
                if engine == "edge":
                    service = self.edge
                elif engine == "piper":
                    service = self.piper
                else:
                    service = self.gpt_sovits
                    service_voice = ""
                if not service.available:
                    failures.append(f"{engine} 尚未配置或不可用")
                    continue
                try:
                    artifact = service.synthesize_result(text, service_voice, emotion)
                    return SpeechArtifact(
                        token=artifact.token,
                        extension=artifact.extension,
                        engine=artifact.engine,
                        fallback=bool(failures),
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    failures.append(str(exc))
        raise SpeechUnavailable("；".join(failures) or "没有可用的 TTS 服务")

    def synthesize(self, text: str, voice: str = "", emotion: str = "neutral") -> str:
        return self.synthesize_result(text, voice, emotion).token

    def status(self, probe: bool = False) -> dict[str, Any]:
        gpt_status = self.gpt_sovits.status(probe=probe)
        return {
            "available": self.available,
            "engine": self.engine,
            "gpt_sovits_configured": gpt_status["gpt_sovits_configured"],
            "gpt_sovits_online": gpt_status["gpt_sovits_online"],
            "piper_available": self.piper.available,
            "piper_model": self.piper.model_path.name if self.piper.model_path.is_file() else "",
            "edge_available": self.edge.available,
            "fallback_order": {
                "edge": ["edge", "piper", "gpt-sovits", "text"],
                "piper": ["piper", "edge", "gpt-sovits", "text"],
                "gpt-sovits": ["gpt-sovits", "piper", "edge", "text"],
            }[self.engine],
        }


class SovitsInstaller:
    """Install GPT-SoVITS into an isolated per-user virtual environment."""

    REPOSITORY = "https://github.com/RVC-Boss/GPT-SoVITS.git"

    def __init__(self, install_root: Path) -> None:
        self.root = Path(install_root)
        self.source = self.root / "repository"
        self.environment = self.root / ".venv"
        self.status_file = self.root / "install-status.json"
        self.log_file = self.root / "install.log"
        self._process: subprocess.Popen[str] | None = None

    @property
    def python(self) -> Path:
        return self.environment / "Scripts" / "python.exe"

    @property
    def installed(self) -> bool:
        return bool(self.python.is_file() and (self.source / "api_v2.py").is_file())

    def status(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        try:
            loaded = json.loads(self.status_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            pass
        data.update(
            {
                "installed": self.installed,
                "running": bool(self._process and self._process.poll() is None),
                "path": str(self.root),
            }
        )
        return data

    def _record(self, progress: int, stage: str, message: str = "") -> dict[str, Any]:
        payload = {
            "progress": max(0, min(int(progress), 100)),
            "stage": str(stage),
            "message": str(message),
            "installed": self.installed,
            "updated_at": int(time.time()),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.status_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.status_file)
        return payload

    @staticmethod
    def _system_python() -> list[str]:
        candidates: list[list[str]] = []
        launcher = shutil.which("py")
        if launcher:
            candidates.extend(([launcher, "-3.10"], [launcher, "-3.11"]))
        for name in ("python3.10", "python3.11", "python"):
            executable = shutil.which(name)
            if executable and Path(executable).resolve() != Path(sys.executable).resolve():
                candidates.append([executable])
        for command in candidates:
            try:
                result = subprocess.run(
                    [*command, "-c", "import sys; raise SystemExit(0 if (3,10)<=sys.version_info[:2]<=(3,11) else 1)"],
                    capture_output=True,
                    timeout=8,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode == 0:
                    return command
            except (OSError, subprocess.SubprocessError):
                continue
        raise RuntimeError("未找到 Python 3.10/3.11；请先安装后再使用一键安装")

    @staticmethod
    def _run(
        command: list[str],
        cwd: Path,
        log: Any,
        environment: dict[str, str] | None = None,
        pulse: Callable[[], None] | None = None,
    ) -> None:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        while process.poll() is None:
            if pulse:
                pulse()
            time.sleep(1)
        return_code = int(process.returncode or 0)
        if return_code:
            raise RuntimeError(f"安装命令失败（退出码 {return_code}），详情见 {log.name}")

    def install(self, progress: Callable[[dict[str, Any]], None] | None = None) -> None:
        notify = progress or (lambda _payload: None)

        def update(percent: int, stage: str, message: str = "") -> None:
            notify(self._record(percent, stage, message))

        self.root.mkdir(parents=True, exist_ok=True)
        try:
            update(3, "准备环境", "检查 Git、PowerShell 和 Python 3.10/3.11")
            git = shutil.which("git")
            powershell = shutil.which("pwsh") or shutil.which("powershell")
            if not git:
                raise RuntimeError("未找到 Git，请先安装 Git for Windows")
            if not powershell:
                raise RuntimeError("未找到 PowerShell")
            python_command = self._system_python()
            with self.log_file.open("a", encoding="utf-8", errors="replace") as log:
                if not (self.source / ".git").is_dir():
                    update(10, "克隆仓库", "正在下载 GPT-SoVITS 源码")
                    if self.source.exists():
                        raise RuntimeError("安装目录已存在但不是完整仓库，请移走后重试")
                    self._run([git, "clone", "--depth", "1", self.REPOSITORY, str(self.source)], self.root, log)
                if not self.python.is_file():
                    update(24, "创建独立环境", "不会修改 HatTip Lab 自身依赖")
                    self._run([*python_command, "-m", "venv", str(self.environment)], self.root, log)
                script = self.source / "install.ps1"
                if not script.is_file():
                    raise RuntimeError("当前 GPT-SoVITS 仓库缺少官方 install.ps1")
                update(38, "安装依赖和模型", "CPU 通用版下载较大，通常需要 5–10 分钟")
                environment = dict(os.environ)
                environment["VIRTUAL_ENV"] = str(self.environment)
                environment["PATH"] = f"{self.environment / 'Scripts'}{os.pathsep}{environment.get('PATH', '')}"
                dependency_started = time.monotonic()

                def pulse() -> None:
                    elapsed = max(0, time.monotonic() - dependency_started)
                    percent = min(92, 38 + int(elapsed / 12))
                    update(percent, "安装依赖和模型", "正在下载和配置，请不要关闭程序")

                self._run(
                    [
                        powershell,
                        "-NoLogo",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(script),
                        "--Device",
                        "CPU",
                        "--Source",
                        "HF",
                    ],
                    self.source,
                    log,
                    environment,
                    pulse,
                )
            update(100, "安装完成", "请导入一段你有权使用的 5–15 秒参考音频")
        except Exception as exc:
            update(0, "安装失败", str(exc))
            raise

    def start(self, host: str = "127.0.0.1", port: int = 9880) -> dict[str, Any]:
        if self._process and self._process.poll() is None:
            return self.status()
        if not self.installed:
            raise RuntimeError("GPT-SoVITS 尚未完成安装")
        log = self.log_file.open("a", encoding="utf-8", errors="replace")
        self._process = subprocess.Popen(
            [str(self.python), str(self.source / "api_v2.py"), "-a", host, "-p", str(port)],
            cwd=str(self.source),
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self._record(100, "服务启动中", f"http://{host}:{port}")
        return self.status()

    def stop(self) -> None:
        process = self._process
        self._process = None
        if process and process.poll() is None:
            process.terminate()
