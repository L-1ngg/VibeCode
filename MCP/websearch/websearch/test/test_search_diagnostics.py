import asyncio
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock, patch

from websearch.tools.search import web_search
from websearch.utils.config import _reset_runtime_for_tests, get_config, init_runtime


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
            patch(
                "websearch.tools.search.fetch",
                new=AsyncMock(
                    return_value={
                        "success": True,
                        "url": "https://example.com",
                        "quality_score": 70,
                        "markdown": "Example result\n\nThis page answers the query directly.",
                    }
                ),
            ),
        ):
            result = asyncio.run(web_search("test query"))

        self.assertTrue(result["success"])
        self.assertEqual(len(result["links"]), 1)
        self.assertIn("diagnostics", result)
        self.assertEqual(result["diagnostics"]["search_backend"], "ddg")
        self.assertTrue(result["diagnostics"]["browser"]["fallback_used"])
        self.assertEqual(result["diagnostics"]["browser"]["brave_error"], "boom")
        self.assertGreaterEqual(len(result["diagnostics"]["browser"]["variants"]), 1)
        self.assertIn("query_plan", result["diagnostics"])
        self.assertIn("fetch_selected", result["diagnostics"])
        self.assertIn("evidence_ranked", result["diagnostics"])

    def test_web_search_reranks_official_docs_above_blog(self) -> None:
        async def fake_ddg(query: str, max_results: int = 20) -> list[dict[str, str]]:
            if "official documentation" in query or query.endswith(" docs"):
                return [{
                    "title": "ExampleLib Documentation",
                    "url": "https://docs.example.com/guide",
                    "description": "official docs",
                }]
            return [{
                "title": "ExampleLib blog tutorial",
                "url": "https://blog.example.com/tutorial",
                "description": "community writeup",
            }]

        async def fake_fetch(url: str, headers: dict[str, str] | None = None) -> dict[str, object]:
            if "docs.example.com" in url:
                return {
                    "success": True,
                    "url": url,
                    "quality_score": 90,
                    "markdown": "Official docs\n\nUse the official configuration guide for ExampleLib.",
                }
            return {
                "success": True,
                "url": url,
                "quality_score": 68,
                "markdown": "Blog post\n\nThis is a personal write-up and may be outdated.",
            }

        with (
            patch("websearch.tools.search._llm_configured", return_value=False),
            patch("websearch.tools.search._search_brave_core", new=AsyncMock(return_value=[])),
            patch("websearch.tools.search._search_duckduckgo_core", new=AsyncMock(side_effect=fake_ddg)),
            patch("websearch.tools.search.fetch", new=AsyncMock(side_effect=fake_fetch)),
        ):
            result = asyncio.run(web_search("ExampleLib official docs"))

        self.assertTrue(result["success"])
        self.assertGreaterEqual(len(result["links"]), 1)
        self.assertEqual(result["links"][0]["url"], "https://docs.example.com/guide")
        self.assertGreaterEqual(len(result["diagnostics"]["browser"]["scheduled_queries"]), 2)
        self.assertEqual(result["diagnostics"]["evidence_ranked"][0]["url"], "https://docs.example.com/guide")

    def test_web_search_uses_evidence_summary_when_llm_disabled(self) -> None:
        ddg_result = [
            {"title": "Streaming docs", "url": "https://docs.example.com/streaming", "description": "official docs"}
        ]
        with (
            patch("websearch.tools.search._llm_configured", return_value=False),
            patch("websearch.tools.search._search_brave_core", new=AsyncMock(return_value=[])),
            patch("websearch.tools.search._search_duckduckgo_core", new=AsyncMock(return_value=ddg_result)),
            patch(
                "websearch.tools.search.fetch",
                new=AsyncMock(
                    return_value={
                        "success": True,
                        "url": "https://docs.example.com/streaming",
                        "quality_score": 88,
                        "markdown": "Streaming guide\n\nStep 1: enable callbacks.\n\nStep 2: configure streaming output.",
                    }
                ),
            ),
        ):
            result = asyncio.run(web_search("How to configure ExampleLib streaming"))

        self.assertEqual(result["diagnostics"]["answer_mode"], "evidence_fallback")
        self.assertIn("Step 1", result["ai_summary"])

    def test_web_search_honors_limited_links_domain_cap(self) -> None:
        ddg_result = [
            {"title": "ExampleLib Install", "url": "https://docs.example.com/install", "description": "official docs"},
            {"title": "ExampleLib Config", "url": "https://docs.example.com/config", "description": "official docs"},
            {"title": "ExampleLib GitHub Guide", "url": "https://github.com/example/project", "description": "repo docs"},
        ]
        constrained_cfg = replace(get_config(), search_result_limit=2, search_max_per_domain=1)

        async def fake_fetch(url: str, headers: dict[str, str] | None = None) -> dict[str, object]:
            return {
                "success": True,
                "url": url,
                "quality_score": 88,
                "markdown": "ExampleLib docs\n\nInstall and configure ExampleLib safely.",
            }

        with (
            patch("websearch.tools.search.get_config", return_value=constrained_cfg),
            patch("websearch.tools.search._llm_configured", return_value=False),
            patch("websearch.tools.search._search_brave_core", new=AsyncMock(return_value=[])),
            patch("websearch.tools.search._search_duckduckgo_core", new=AsyncMock(return_value=ddg_result)),
            patch("websearch.tools.search.fetch", new=AsyncMock(side_effect=fake_fetch)),
        ):
            result = asyncio.run(web_search("ExampleLib official docs"))

        self.assertEqual(len(result["links"]), 2)
        self.assertEqual(result["links"][0]["url"], "https://docs.example.com/install")
        self.assertEqual(result["links"][1]["url"], "https://github.com/example/project")
        self.assertEqual(result["diagnostics"]["coarse_ranked"][1]["url"], "https://github.com/example/project")


if __name__ == "__main__":
    unittest.main()
