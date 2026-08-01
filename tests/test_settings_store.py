from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path

from settings_store import SecretProtectionError, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            store.update(mode="live2d", scale=1.2, unknown="ignored")
            loaded = SettingsStore(path).as_dict()
            self.assertEqual(loaded["mode"], "live2d")
            self.assertEqual(loaded["scale"], 1.2)
            self.assertNotIn("unknown", loaded)

    def test_secrets_are_protected_and_not_returned_as_settings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(
                path,
                protector=lambda value: f"dpapi:{value[::-1]}",
                unprotector=lambda value: value.removeprefix("dpapi:")[::-1],
            )
            store.set_secret("openai_api_key", "sk-private")
            self.assertEqual(store.get_secret("openai_api_key"), "sk-private")
            self.assertNotIn("openai_api_key", store.as_dict())
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertNotIn("sk-private", json.dumps(raw))
            self.assertTrue(raw["secrets"]["openai_api_key"].startswith("dpapi:"))

    def test_atomic_apply_does_not_partially_save_when_protection_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"

            def fail(_: str) -> str:
                raise SecretProtectionError("failed")

            store = SettingsStore(path, protector=fail)
            store.update(provider="hermes")
            before = path.read_text(encoding="utf-8")
            with self.assertRaises(SecretProtectionError):
                store.apply(
                    changes={"provider": "openai"},
                    secret_changes={"openai_api_key": "sk-private"},
                )
            self.assertEqual(store.as_dict()["provider"], "hermes")
            self.assertEqual(path.read_text(encoding="utf-8"), before)


if __name__ == "__main__":
    unittest.main()
