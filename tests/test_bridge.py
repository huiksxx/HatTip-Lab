from __future__ import annotations

import unittest
import json
import tempfile
import urllib.request
from pathlib import Path

from agent_providers import ChatResult, HermesError
from hermes_vpet_bridge import BridgeServer, create_app
from model_library import ModelLibrary


class FakeBackend:
    def ask(self, text: str) -> ChatResult:
        if not str(text).strip():
            raise HermesError("消息不能为空")
        return ChatResult(reply=f"回复：{text}", emotion="happy")


class BridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        app = create_app(FakeBackend())
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_health(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["status"], "ok")
        self.assertEqual(response.json["service"], "hattip-lab-bridge")

    def test_chat(self) -> None:
        response = self.client.post("/chat", json={"text": "你好", "stream": False})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["reply"], "回复：你好")
        self.assertEqual(response.json["emotion"], "neutral")

    def test_chat_validation_error(self) -> None:
        response = self.client.post("/chat", json={"text": "", "stream": False})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json["ok"])

    def test_legacy_sse_endpoint(self) -> None:
        response = self.client.post("/hyper_stream/success", json={"text": "你好"})
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/event-stream")
        self.assertIn("回复：你好", body)
        self.assertIn('"done": true', body)

    def test_chat_streams_sse_by_default(self) -> None:
        response = self.client.post("/chat", json={"text": "你好"})
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/event-stream")
        self.assertIn("回复：你好", body)
        self.assertIn('"done": true', body)

    def test_provider_list(self) -> None:
        response = self.client.get("/providers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["providers"][0]["id"], "default")

    def test_serves_only_registered_model_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            model_root = base / "source"
            model_root.mkdir()
            (model_root / "avatar.moc3").write_bytes(b"moc")
            (model_root / "texture.png").write_bytes(b"png")
            entry = model_root / "avatar.model3.json"
            entry.write_text(
                json.dumps({"FileReferences": {"Moc": "avatar.moc3", "Textures": ["texture.png"]}}),
                encoding="utf-8",
            )
            library = ModelLibrary(base / "data")
            model = library.import_model(entry)
            app = create_app(FakeBackend(), model_library=library)
            app.config.update(TESTING=True)
            client = app.test_client()

            response = client.get(f"/models/{model.id}/texture.png")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b"png")
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "*")
            response.close()
            self.assertEqual(client.get("/models/missing/texture.png").status_code, 404)

    def test_serves_ui_from_explicit_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pet.html").write_text("<title>Pet Test</title>", encoding="utf-8")
            app = create_app(FakeBackend(), static_root=root)
            app.config.update(TESTING=True)
            response = app.test_client().get("/ui/pet.html")
            self.assertEqual(response.status_code, 200)
            self.assertIn(b"Pet Test", response.data)
            response.close()

    def test_server_selects_an_ephemeral_port(self) -> None:
        server = BridgeServer(backend=FakeBackend(), port=0)
        try:
            server.start()
            self.assertGreater(server.port, 0)
            with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/health", timeout=2) as response:
                self.assertEqual(response.status, 200)
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
