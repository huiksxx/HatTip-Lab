from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from voice_services import (
    EdgeTtsService,
    PiperTTSService,
    PushToTalkHotkey,
    ResilientTtsService,
    SpeechUnavailable,
    TtsService,
    parse_hotkey,
)


class FakePiperVoice:
    def __init__(self) -> None:
        self.options: dict[str, float] = {}

    def synthesize(
        self,
        _text: str,
        wav_file: object,
        length_scale: float = 1.0,
        noise_scale: float = 0.667,
        noise_w: float = 0.8,
    ) -> None:
        self.options = {
            "length_scale": length_scale,
            "noise_scale": noise_scale,
            "noise_w": noise_w,
        }
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(b"\x00\x00" * 500)


class FakeEdgeCommunicate:
    calls: list[dict[str, str]] = []

    def __init__(self, text: str, voice: str, **options: str) -> None:
        self.calls.append({"text": text, "voice": voice, **options})

    async def save(self, path: str) -> None:
        Path(path).write_bytes(b"ID3fake-edge-audio")


class VoiceServiceTests(unittest.TestCase):
    def test_parses_default_push_to_talk_hotkey(self) -> None:
        modifiers, key = parse_hotkey("Alt+Space")
        self.assertEqual(modifiers, frozenset({0x12}))
        self.assertEqual(key, 0x20)
        self.assertEqual(PushToTalkHotkey.validate("alt+space"), "Alt+Space")

    def test_rejects_hotkey_without_modifier(self) -> None:
        with self.assertRaises(ValueError):
            parse_hotkey("Space")

    def test_tts_tokens_cannot_escape_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = TtsService(Path(directory))
            self.assertIsNone(service.resolve("../settings"))
            self.assertIsNone(service.resolve("not-a-token"))

    def test_piper_generates_wav_and_maps_happy_speed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "voice.onnx"
            model.write_bytes(b"model")
            model.with_suffix(".onnx.json").write_text("{}", encoding="utf-8")
            fake = FakePiperVoice()
            service = PiperTTSService(root / "cache", model, lambda _path: fake)
            artifact = service.synthesize_result("你好", emotion="happy")
            self.assertEqual(artifact.engine, "piper")
            self.assertEqual(artifact.extension, "wav")
            self.assertTrue(service.resolve(artifact.token, "wav"))
            self.assertAlmostEqual(fake.options["length_scale"], 0.91)

    def test_edge_generates_mp3_without_a_voice_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            FakeEdgeCommunicate.calls.clear()
            service = EdgeTtsService(Path(directory), FakeEdgeCommunicate)
            artifact = service.synthesize_result("你好", emotion="happy")
            self.assertEqual(artifact.engine, "edge")
            self.assertEqual(artifact.extension, "mp3")
            self.assertTrue(service.resolve(artifact.token, "mp3"))
            self.assertEqual(FakeEdgeCommunicate.calls[-1]["rate"], "+8%")

    def test_resilient_service_falls_back_from_sovits_to_piper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            model = root / "voice.onnx"
            model.write_bytes(b"model")
            model.with_suffix(".onnx.json").write_text("{}", encoding="utf-8")
            service = ResilientTtsService(root / "cache", model, lambda _path: FakePiperVoice())
            service.configure(engine="gpt-sovits", api_url="http://127.0.0.1:9880")
            service.gpt_sovits.synthesize_result = lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("offline")
            )
            artifact = service.synthesize_result("自动降级")
            self.assertEqual(artifact.engine, "piper")
            self.assertTrue(artifact.fallback)

    def test_missing_piper_model_falls_back_to_edge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = ResilientTtsService(
                root / "cache",
                root / "missing.onnx",
                edge_communicate_factory=FakeEdgeCommunicate,
            )
            service.configure(engine="piper", api_url="")
            artifact = service.synthesize_result("无需先安装模型")
            self.assertEqual(artifact.engine, "edge")
            self.assertTrue(artifact.fallback)

    def test_resilient_service_reports_text_only_when_all_engines_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = ResilientTtsService(
                root / "cache", root / "missing.onnx", edge_enabled=False
            )
            service.configure(api_url="")
            with self.assertRaises(SpeechUnavailable):
                service.synthesize_result("仍然保留文字")


if __name__ == "__main__":
    unittest.main()
