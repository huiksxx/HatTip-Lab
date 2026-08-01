from __future__ import annotations

import unittest

from agent_providers import (
    AgentProvider,
    HttpProvider,
    OpenAIResponsesProvider,
    ProviderError,
    ProviderInfo,
    ProviderRegistry,
)


class FakeProvider(AgentProvider):
    def __init__(self, provider_id: str, available: bool = True):
        self._info = ProviderInfo(provider_id, provider_id.title(), available)

    def info(self) -> ProviderInfo:
        return self._info

    async def chat(self, text, history):
        yield f"{self._info.id}:{text}"


class ProviderRegistryTests(unittest.TestCase):
    def test_routes_to_selected_provider_and_keeps_history(self) -> None:
        registry = ProviderRegistry([FakeProvider("one"), FakeProvider("two")], default_id="one")
        self.assertEqual(registry.ask("two", "hello").reply, "two:hello")
        self.assertEqual(registry.ask(None, "hello").reply, "one:hello")

    def test_rejects_unknown_or_unavailable_provider(self) -> None:
        registry = ProviderRegistry([FakeProvider("offline", False)])
        with self.assertRaises(ProviderError):
            registry.ask("offline", "hello")
        with self.assertRaises(ProviderError):
            registry.ask("missing", "hello")

    def test_openai_responses_adapter_parses_streaming_delta(self) -> None:
        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def iter_lines(self, decode_unicode=True):
                yield 'data: {"type":"response.output_text.delta","delta":"当然"}'
                yield 'data: {"type":"response.output_text.delta","delta":"可以！"}'
                yield "data: [DONE]"

        def post(*args, **kwargs):
            calls.append((args, kwargs))
            return Response()

        provider = OpenAIResponsesProvider(api_key="test-key", model="test-model", http_post=post)
        registry = ProviderRegistry([provider])
        result = registry.ask("openai", "你好")
        self.assertEqual(result.reply, "当然可以！")
        self.assertEqual(calls[0][1]["json"]["model"], "test-model")
        self.assertTrue(calls[0][1]["json"]["stream"])
        self.assertFalse(calls[0][1]["json"]["store"])
        self.assertEqual(calls[0][1]["headers"]["Authorization"], "Bearer test-key")

    def test_http_provider_parses_openai_compatible_stream(self) -> None:
        calls = []

        class Response:
            def raise_for_status(self):
                return None

            def iter_lines(self, decode_unicode=True):
                yield 'data: {"choices":[{"delta":{"content":"你"}}]}'
                yield 'data: {"choices":[{"delta":{"content":"好"}}]}'
                yield "data: [DONE]"

        def post(*args, **kwargs):
            calls.append((args, kwargs))
            return Response()

        provider = HttpProvider(
            endpoint="http://127.0.0.1:11434",
            api_key="local-key",
            model="local-model",
            http_post=post,
        )
        result = ProviderRegistry([provider]).ask("http", "测试")
        self.assertEqual(result.reply, "你好")
        self.assertEqual(calls[0][0][0], "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(calls[0][1]["json"]["model"], "local-model")
        self.assertEqual(calls[0][1]["headers"]["Authorization"], "Bearer local-key")


if __name__ == "__main__":
    unittest.main()
