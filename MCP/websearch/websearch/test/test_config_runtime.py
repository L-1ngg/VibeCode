import io
import logging
import unittest
from unittest.mock import patch

from websearch.utils.config import (
    _reset_runtime_for_tests,
    build_config,
    get_config,
    init_runtime,
)


class ConfigRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_for_tests()

    def test_get_config_requires_init(self) -> None:
        with self.assertRaises(RuntimeError):
            get_config()

    def test_build_config_cli_overrides_env(self) -> None:
        env = {
            "PROXY": "http://env:7890",
            "OPENAI_BASE_URL": "https://env.example/v1",
        }
        cfg = build_config(
            argv=["--proxy", "http://cli:7890", "--openai-base-url", "https://cli.example/v1"],
            env=env,
        )
        self.assertEqual(cfg.proxy, "http://cli:7890")
        self.assertEqual(cfg.openai_base_url, "https://cli.example/v1")

    def test_build_config_invalid_int_falls_back_default(self) -> None:
        env = {
            "PLAYWRIGHT_TIMEOUT_MS": "invalid",
            "PLAYWRIGHT_CHALLENGE_WAIT": "-1",
        }
        stderr = io.StringIO()
        with patch("websearch.utils.config.sys.stderr", stderr):
            cfg = build_config(argv=[], env=env)
        self.assertEqual(cfg.playwright_timeout_ms, 60000)
        self.assertEqual(cfg.playwright_challenge_wait, 20)
        self.assertIn("invalid integer", stderr.getvalue())

    def test_build_config_extraction_options(self) -> None:
        env = {
            "EXTRACTION_STRATEGY": "speed",
            "EXTRACTION_MARKDOWN_MIN_CHARS": "150",
            "EXTRACTION_TEXT_MIN_CHARS": "260",
        }
        cfg = build_config(argv=[], env=env)
        self.assertEqual(cfg.extraction.strategy, "speed")
        self.assertEqual(cfg.extraction.markdown_min_chars, 150)
        self.assertEqual(cfg.extraction.text_min_chars, 260)

    def test_build_config_invalid_extraction_strategy_falls_back(self) -> None:
        env = {"EXTRACTION_STRATEGY": "fastest"}
        stderr = io.StringIO()
        with patch("websearch.utils.config.sys.stderr", stderr):
            cfg = build_config(argv=[], env=env)
        self.assertEqual(cfg.extraction.strategy, "quality")
        self.assertIn("invalid value for EXTRACTION_STRATEGY", stderr.getvalue())

    def test_init_runtime_is_idempotent_for_logging_handler(self) -> None:
        init_runtime(argv=[])
        root = logging.getLogger()
        first_count = len([h for h in root.handlers if getattr(h, "_websearch_handler", False)])

        init_runtime(argv=[])
        second_count = len([h for h in root.handlers if getattr(h, "_websearch_handler", False)])

        self.assertEqual(first_count, 1)
        self.assertEqual(second_count, 1)
        self.assertIsNotNone(get_config())


if __name__ == "__main__":
    unittest.main()
