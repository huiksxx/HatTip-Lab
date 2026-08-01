from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from agent_providers import MockProvider, ProviderRegistry
from desktop_pet import DesktopApi, configure_remote_providers
from model_library import ModelLibrary
from settings_store import SettingsStore


class FakeWindow:
    uid = "master"

    def __init__(self) -> None:
        self.x = 100
        self.y = 200
        self.width = 420
        self.height = 560
        self.moves: list[tuple[int, int]] = []
        self.resizes: list[tuple[int, int]] = []
        self.scripts: list[str] = []

    def move(self, x: int, y: int) -> None:
        self.x = x
        self.y = y
        self.moves.append((x, y))

    def resize(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.resizes.append((width, height))

    def run_js(self, script: str) -> None:
        self.scripts.append(script)


class DesktopApiTests(unittest.TestCase):
    def test_drag_uses_pointer_delta_from_window_origin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            providers = ProviderRegistry([MockProvider()], default_id="mock")
            api = DesktopApi(
                8766,
                "gif",
                "mock",
                SettingsStore(root / "settings.json"),
                providers,
                ModelLibrary(root / "data"),
                mock=True,
            )
            window = FakeWindow()
            api._attach_window(window)

            self.assertTrue(api.begin_drag(50, 60)["ok"])
            self.assertTrue(api.drag_to(74, 91)["ok"])
            self.assertEqual(window.moves, [(124, 231)])

    def test_bubble_is_on_right_when_pet_is_left_of_screen_center(self) -> None:
        result = DesktopApi._calculate_bubble_position(
            (180, 200, 420, 560), (360, 220), (0, 0, 1920, 1040)
        )
        self.assertEqual(result, (612, 301, "right"))

    def test_bubble_flips_left_when_pet_is_on_right(self) -> None:
        result = DesktopApi._calculate_bubble_position(
            (1380, 200, 420, 560), (360, 220), (0, 0, 1920, 1040)
        )
        self.assertEqual(result, (1008, 301, "left"))

    def test_bubble_position_is_clamped_to_work_area(self) -> None:
        x, y, side = DesktopApi._calculate_bubble_position(
            (50, 900, 420, 560), (360, 220), (0, 0, 1280, 1024)
        )
        self.assertEqual((x, y), (482, 804))
        self.assertEqual(side, "right")

    def test_scale_keeps_bottom_center_and_window_on_screen(self) -> None:
        result = DesktopApi._calculate_scaled_window_position(
            (1000, 500, 420, 560), (504, 672), (0, 0, 1536, 864)
        )
        self.assertEqual(result, (958, 192))

    def test_bootstrap_repairs_stale_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            settings.update(provider="removed-provider")
            providers = ProviderRegistry([MockProvider()], default_id="mock")
            api = DesktopApi(
                8766,
                "gif",
                "auto",
                settings,
                providers,
                ModelLibrary(root / "data"),
                mock=True,
            )
            self.assertEqual(api.bootstrap()["provider"], "mock")
            self.assertEqual(settings.as_dict()["provider"], "mock")

    def test_model_switch_can_suppress_duplicate_ui_refresh(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = ModelLibrary(root / "data")
            model_root = library.models_dir / "test-model"
            model_root.mkdir(parents=True)
            (model_root / "test.model3.json").write_text(
                '{"Version":3,"FileReferences":{"Moc":"test.moc3","Textures":["test.png"]}}',
                encoding="utf-8",
            )
            (model_root / "pet-model.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "id": "test-model",
                        "name": "Test model",
                        "entry": "test.model3.json",
                        "format": "cubism4",
                        "license_confirmed_by_user": True,
                    }
                ),
                encoding="utf-8",
            )
            providers = ProviderRegistry([MockProvider()], default_id="mock")
            api = DesktopApi(
                8766,
                "gif",
                "mock",
                SettingsStore(root / "settings.json"),
                providers,
                library,
                mock=True,
            )
            window = FakeWindow()
            api._attach_window(window)

            self.assertTrue(api.set_model("test-model", notify=False)["ok"])
            self.assertEqual(window.scripts, [])
            self.assertTrue(api.set_model("test-model")["ok"])
            self.assertEqual(window.scripts, ["window.onPetSettingsChanged?.();"])

    def test_chat_ignores_stale_unavailable_provider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            settings.update(provider="removed-provider")
            providers = ProviderRegistry([MockProvider()], default_id="mock")
            api = DesktopApi(
                8766,
                "gif",
                "auto",
                settings,
                providers,
                ModelLibrary(root / "data"),
                mock=True,
            )
            response = Mock(ok=True)
            response.json.return_value = {"ok": True, "reply": "ok"}
            with patch("desktop_pet.requests.post", return_value=response) as post:
                self.assertTrue(api.chat("hello")["ok"])
            self.assertEqual(post.call_args.kwargs["json"]["provider"], "mock")
            self.assertEqual(settings.as_dict()["provider"], "mock")

    def test_settings_configure_http_provider_and_protect_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(
                root / "settings.json",
                protector=lambda value: f"dpapi:{value[::-1]}",
                unprotector=lambda value: value.removeprefix("dpapi:")[::-1],
            )
            providers = ProviderRegistry([MockProvider()], default_id="mock")
            configure_remote_providers(providers, settings)
            api = DesktopApi(
                8766,
                "gif",
                "auto",
                settings,
                providers,
                ModelLibrary(root / "data"),
                mock=True,
            )
            result = api.save_settings(
                {
                    "provider": "http",
                    "http_endpoint": "http://127.0.0.1:11434",
                    "http_model": "test-model",
                    "http_api_key": "private-token",
                    "openai_base_url": "https://api.openai.com/v1",
                    "openai_model": "gpt-4o-mini",
                    "temperature": 0.4,
                    "max_tokens": 800,
                    "mode": "gif",
                    "voice_input_enabled": False,
                    "push_to_talk_hotkey": "Alt+Space",
                    "tts_enabled": False,
                }
            )
            self.assertTrue(result["ok"])
            self.assertTrue(providers.has_available("http"))
            self.assertEqual(settings.as_dict()["provider"], "http")
            self.assertEqual(settings.get_secret("http_api_key"), "private-token")
            raw = (root / "settings.json").read_text(encoding="utf-8")
            self.assertNotIn("private-token", raw)

    def test_invalid_provider_settings_are_not_partially_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            providers = ProviderRegistry([MockProvider()], default_id="mock")
            configure_remote_providers(providers, settings)
            api = DesktopApi(
                8766,
                "gif",
                "auto",
                settings,
                providers,
                ModelLibrary(root / "data"),
                mock=True,
            )
            result = api.save_settings(
                {
                    "provider": "http",
                    "http_endpoint": "",
                    "http_model": "",
                    "openai_base_url": "https://api.openai.com/v1",
                    "temperature": 0.5,
                    "max_tokens": 900,
                    "mode": "gif",
                    "voice_input_enabled": True,
                    "push_to_talk_hotkey": "Alt+Space",
                    "tts_enabled": False,
                }
            )
            self.assertFalse(result["ok"])
            self.assertEqual(settings.as_dict()["temperature"], 0.8)

    def test_tts_settings_save_without_a_local_voice_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SettingsStore(root / "settings.json")
            providers = ProviderRegistry([MockProvider()], default_id="mock")
            api = DesktopApi(
                8766,
                "gif",
                "mock",
                settings,
                providers,
                ModelLibrary(root / "data"),
                mock=True,
            )
            result = api.save_settings(
                {
                    "provider": "mock",
                    "openai_base_url": "https://api.openai.com/v1",
                    "mode": "gif",
                    "voice_input_enabled": True,
                    "push_to_talk_hotkey": "Alt+Space",
                    "tts_enabled": True,
                    "tts_engine": "piper",
                    "tts_voice": "zh-CN-XiaoxiaoNeural",
                }
            )
            self.assertTrue(result["ok"])
            self.assertEqual(settings.as_dict()["tts_engine"], "piper")
            self.assertTrue(settings.as_dict()["tts_enabled"])


if __name__ == "__main__":
    unittest.main()
