from __future__ import annotations

import unittest

from agent_providers import (
    HermesError,
    HermesProvider,
    ProviderError,
    ProviderRegistry,
    REPLY_MARKER,
    detect_emotion,
)


class HermesProviderTests(unittest.TestCase):
    def test_builds_cli_command_and_keeps_registry_history(self) -> None:
        commands = []

        def runner(command, timeout):
            commands.append((command, timeout))
            return f"{REPLY_MARKER}当然可以，很开心帮你！"

        registry = ProviderRegistry(
            [HermesProvider(executable="hermes-test", timeout=7, runner=runner)]
        )
        first = registry.ask("hermes", "你好")
        second = registry.ask("hermes", "还记得我吗")

        self.assertEqual(first.emotion, "happy")
        self.assertIn("hermes-test", commands[0][0])
        self.assertIn("--quiet", commands[0][0])
        self.assertEqual(commands[0][1], 7)
        self.assertIn("用户：你好", commands[1][0][3])
        self.assertEqual(second.reply, "当然可以，很开心帮你！")

    def test_rejects_empty_and_oversized_messages(self) -> None:
        registry = ProviderRegistry([HermesProvider(runner=lambda *_: "unused")])
        with self.assertRaises(ProviderError):
            registry.ask("hermes", "   ")
        with self.assertRaises(ProviderError):
            registry.ask("hermes", "x" * 2001)

    def test_detect_emotion(self) -> None:
        self.assertEqual(detect_emotion("太好了，没问题"), "happy")
        self.assertEqual(detect_emotion("很抱歉听到这个消息"), "sad")
        self.assertEqual(detect_emotion("普通的信息"), "neutral")

    def test_strips_hermes_reasoning_and_warning_output(self) -> None:
        raw = (
            "Warning: Unknown toolsets: messaging, moa\r\n\r\n"
            "┌─ Reasoning ─────┐\r\nI should answer briefly.\r\n"
            f"{REPLY_MARKER}连接正常"
        )
        registry = ProviderRegistry([HermesProvider(runner=lambda *_: raw)])
        self.assertEqual(registry.ask("hermes", "测试").reply, "连接正常")


if __name__ == "__main__":
    unittest.main()
