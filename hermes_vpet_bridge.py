"""Local Flask bridge between HatTip Lab and configured agent providers."""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory, stream_with_context
from werkzeug.serving import BaseWSGIServer, make_server

from agent_providers import (
    ChatResult,
    HermesError,
    HermesProvider,
    ProviderError,
    ProviderRegistry,
    SingleBackendProvider,
    detect_emotion,
    normalize_history,
    normalize_text,
)
from model_library import ModelLibrary
from voice_services import TtsService


class ChatBackend(Protocol):
    def ask(self, text: str) -> ChatResult: ...


def _sse_chunks(result: ChatResult, chunk_size: int = 12) -> Iterator[str]:
    for index in range(0, len(result.reply), chunk_size):
        chunk = result.reply[index : index + chunk_size]
        payload = {
            "content": chunk,
            "emotion": result.emotion,
            "done": False,
        }
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    yield 'data: {"done": true}\n\n'


def _provider_sse_chunks(
    registry: ProviderRegistry,
    provider_id: str | None,
    text: str,
    history: list[dict[str, str]],
) -> Iterator[str]:
    """Drive one async provider iterator from Flask's synchronous response."""

    loop = asyncio.new_event_loop()
    stream = registry.stream(provider_id, text, history)
    reply_parts: list[str] = []
    try:
        while True:
            try:
                chunk = loop.run_until_complete(anext(stream))
            except StopAsyncIteration:
                break
            reply_parts.append(chunk)
            yield f"data: {json.dumps({'content': chunk, 'done': False}, ensure_ascii=False)}\n\n"
        reply = "".join(reply_parts)
        done = {"done": True, "emotion": detect_emotion(reply)}
        yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
    except (HermesError, ProviderError) as exc:
        yield f"data: {json.dumps({'done': True, 'error': str(exc)}, ensure_ascii=False)}\n\n"
    except Exception:
        yield f"data: {json.dumps({'done': True, 'error': '智能体暂时不可用'}, ensure_ascii=False)}\n\n"
    finally:
        try:
            loop.run_until_complete(stream.aclose())
        except Exception:
            pass
        loop.close()


def create_app(
    backend: ChatBackend | None = None,
    registry: ProviderRegistry | None = None,
    model_library: ModelLibrary | None = None,
    static_root: Path | None = None,
    tts_service: TtsService | None = None,
) -> Flask:
    app = Flask(__name__)
    if registry is None:
        registry = (
            ProviderRegistry([SingleBackendProvider(backend)])
            if backend is not None
            else ProviderRegistry([HermesProvider()], default_id="hermes")
        )
    provider_registry = registry

    @app.after_request
    def add_local_headers(response: Response) -> Response:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.path.startswith(("/models/", "/runtime/")):
            response.headers["Access-Control-Allow-Origin"] = "*"
        return response

    @app.get("/health")
    def health() -> Response:
        return jsonify(
            status="ok",
            service="hattip-lab-bridge",
            providers=provider_registry.list(),
            live2d_runtime=bool(model_library and model_library.runtime_available()),
        )

    @app.get("/providers")
    def providers() -> Response:
        return jsonify(providers=provider_registry.list())

    if tts_service is not None:
        speech = tts_service

        @app.get("/audio/<token>.<extension>")
        def tts_audio(token: str, extension: str) -> Response:
            path = speech.resolve(token, extension)
            if path is None:
                abort(404)
            mimetypes = {
                "mp3": "audio/mpeg",
                "wav": "audio/wav",
                "ogg": "audio/ogg",
                "aac": "audio/aac",
            }
            return send_file(
                path, mimetype=mimetypes.get(extension, "application/octet-stream"), conditional=True
            )

    if static_root is not None:
        ui_root = static_root.resolve()

        @app.get("/ui")
        @app.get("/ui/")
        def ui_index() -> Response:
            return send_from_directory(ui_root, "pet.html", conditional=True)

        @app.get("/ui/<path:asset_path>")
        def ui_asset(asset_path: str) -> Response:
            return send_from_directory(ui_root, asset_path, conditional=True)

    @app.post("/chat")
    def chat() -> tuple[Response, int] | Response:
        body = request.get_json(silent=True) or {}
        text = body.get("text", "")
        provider_id = body.get("provider")
        history = normalize_history(body.get("history"))
        try:
            normalized = normalize_text(text)
            provider_registry.get(provider_id)
        except (HermesError, ProviderError) as exc:
            return jsonify(ok=False, error=str(exc)), 400
        if body.get("stream", True) is not False:
            return Response(
                stream_with_context(
                    _provider_sse_chunks(provider_registry, provider_id, normalized, history)
                ),
                mimetype="text/event-stream",
                headers={"Connection": "keep-alive"},
            )
        try:
            result = provider_registry.ask(provider_id, normalized, history)
        except Exception as exc:  # keep implementation details out of the UI
            app.logger.exception("Unexpected HatTip Lab bridge error")
            return jsonify(ok=False, error=f"智能体暂时不可用：{exc}"), 502
        return jsonify(ok=True, **result.to_dict())

    @app.post("/hyper_stream/success")
    def hyper_stream_success() -> tuple[Response, int] | Response:
        body = request.get_json(silent=True) or {}
        text = body.get("text", body.get("message", ""))
        provider_id = body.get("provider")
        history = normalize_history(body.get("history"))
        try:
            normalized = normalize_text(text)
            provider_registry.get(provider_id)
        except (HermesError, ProviderError) as exc:
            return jsonify(error=str(exc)), 400
        return Response(
            stream_with_context(
                _provider_sse_chunks(provider_registry, provider_id, normalized, history)
            ),
            mimetype="text/event-stream",
            headers={"Connection": "keep-alive"},
        )

    if model_library is not None:
        library = model_library

        @app.get("/models/<model_id>/<path:asset_path>")
        def model_asset(model_id: str, asset_path: str) -> Response:
            root = library.model_root(model_id)
            if root is None:
                abort(404)
            return send_from_directory(root, asset_path, conditional=True)

        @app.get("/runtime/live2dcubismcore.min.js")
        def live2d_core() -> Response:
            if not library.runtime_available():
                abort(404)
            return send_from_directory(
                library.runtime_dir,
                "live2dcubismcore.min.js",
                mimetype="text/javascript",
                conditional=True,
            )

        @app.get("/runtime/live2d.min.js")
        def live2d_cubism2() -> Response:
            cubism2 = library.runtime_dir / "live2d.min.js"
            if not cubism2.is_file():
                abort(404)
            return send_from_directory(
                library.runtime_dir,
                "live2d.min.js",
                mimetype="text/javascript",
                conditional=True,
            )

    return app


app = create_app()


class BridgeServer:
    """Small controllable WSGI server suitable for a pywebview background thread."""

    def __init__(
        self,
        backend: ChatBackend | None = None,
        registry: ProviderRegistry | None = None,
        model_library: ModelLibrary | None = None,
        static_root: Path | None = None,
        tts_service: TtsService | None = None,
        host: str = "127.0.0.1",
        port: int = 8766,
    ):
        self.host = host
        self.port = port
        self.app = create_app(
            backend=backend,
            registry=registry,
            model_library=model_library,
            static_root=static_root,
            tts_service=tts_service,
        )
        self._server: BaseWSGIServer | None = None
        self._thread: threading.Thread | None = None
        self._stop_lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._server = make_server(self.host, self.port, self.app, threaded=True)
        self.port = int(self._server.server_port)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="hattip-lab-bridge",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        with self._stop_lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="HatTip Lab HTTP bridge")
    parser.add_argument("port", nargs="?", type=int, default=8766)
    options = parser.parse_args()
    create_app().run(host="127.0.0.1", port=options.port, debug=False, threaded=True)
