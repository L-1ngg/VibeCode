import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from websearch.tools.search import web_search
from websearch.utils.config import _reset_runtime_for_tests, init_runtime


class SearchDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _reset_runtime_for_tests()
        init_runtime(argv=[])

    def test_web_search_reports_ddg_fallback_diagnostics(self) -> None:
        ddg_result = [{"title": "Example", "url": "https://example.com", "description": "desc"}]
        with (
            patch("websearch.tools.search._llm_configured", return_value=False),
            patch("websearch.tools.search._search_brave_core", new=AsyncMock(side_effect=RuntimeError("boom"))),
            patch("websearch.tools.search._search_duckduckgo_core", new=AsyncMock(return_value=ddg_result)),
        ):
            result = asyncio.run(web_search("test query"))

        self.assertTrue(result["success"])
        self.assertEqual(len(result["links"]), 1)
        self.assertIn("diagnostics", result)
        self.assertEqual(result["diagnostics"]["search_backend"], "ddg")
        self.assertTrue(result["diagnostics"]["browser"]["fallback_used"])
        self.assertEqual(result["diagnostics"]["browser"]["brave_error"], "boom")


if __name__ == "__main__":
    unittest.main()
