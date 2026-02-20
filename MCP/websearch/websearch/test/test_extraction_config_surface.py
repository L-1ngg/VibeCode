import io
import os
import unittest
from unittest.mock import patch

from websearch.utils.config import _reset_runtime_for_tests, build_config, init_runtime
from websearch.utils.extraction import _extract_best_content


class ExtractionConfigSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        _reset_runtime_for_tests()

    def test_defaults_are_applied(self) -> None:
        cfg = build_config(argv=[], env={})
        self.assertEqual(cfg.extraction.strategy, "quality")
        self.assertEqual(cfg.extraction.markdown_min_chars, 120)
        self.assertEqual(cfg.extraction.text_min_chars, 200)

    def test_old_extraction_variables_are_ignored(self) -> None:
        env = {
            "EXTRACTION_ADAPTER_MIN_QUALITY": "99",
            "EXTRACTION_GENERAL_MIN_QUALITY": "99",
            "EXTRACTION_BONUS_ADAPTER": "99",
            "EXTRACTION_BONUS_PRECISION": "99",
            "EXTRACTION_BONUS_RECALL": "99",
            "EXTRACTION_BONUS_FAST": "99",
            "EXTRACTION_BONUS_BASELINE": "99",
            "EXTRACTION_EARLY_STOP": "0",
            "EXTRACTION_EARLY_STOP_QUALITY": "99",
            "EXTRACTION_EARLY_STOP_CHARS": "9999",
        }
        cfg = build_config(argv=[], env=env)
        self.assertEqual(cfg.extraction.strategy, "quality")
        self.assertEqual(cfg.extraction.markdown_min_chars, 120)
        self.assertEqual(cfg.extraction.text_min_chars, 200)
        self.assertFalse(hasattr(cfg, "extraction_bonus_adapter"))

    def test_invalid_strategy_falls_back_to_quality(self) -> None:
        stderr = io.StringIO()
        with patch("websearch.utils.config.sys.stderr", stderr):
            cfg = build_config(argv=[], env={"EXTRACTION_STRATEGY": "fastest"})
        self.assertEqual(cfg.extraction.strategy, "quality")
        self.assertIn("invalid value for EXTRACTION_STRATEGY", stderr.getvalue())

    def test_extract_best_content_runs_for_all_strategies(self) -> None:
        html = (
            "<html><head><title>Demo</title></head><body>"
            "<article><h1>Heading</h1><p>"
            + ("This is test content. " * 80)
            + "</p></article></body></html>"
        )
        for strategy in ("quality", "balanced", "speed"):
            _reset_runtime_for_tests()
            with patch.dict(os.environ, {"EXTRACTION_STRATEGY": strategy}, clear=False):
                init_runtime(argv=[])
                result = _extract_best_content(html, url="https://example.com", output_format="markdown")
            self.assertTrue(result.get("content"))
            self.assertIn("extractor", result)


if __name__ == "__main__":
    unittest.main()
