import unittest

from websearch.utils.evidence_ranker import (
    build_evidence_items,
    build_evidence_pack,
    rerank_evidence_items,
    should_stop_fetching,
    FetchedCandidate,
)
from websearch.utils.query_planner import plan_query


class EvidenceRankerTests(unittest.TestCase):
    def test_official_docs_evidence_outranks_blog(self) -> None:
        plan = plan_query("ExampleLib official docs")
        docs_candidate = FetchedCandidate(
            title="ExampleLib Documentation",
            url="https://docs.example.com/guide",
            description="official docs",
            source="browser",
            query_variant="ExampleLib official documentation",
            query_reason="official_docs",
            coarse_score=9.0,
            coarse_rank=1,
        )
        blog_candidate = FetchedCandidate(
            title="ExampleLib tutorial blog",
            url="https://blog.example.com/tutorial",
            description="community blog",
            source="browser",
            query_variant="ExampleLib tutorial",
            query_reason="normalized",
            coarse_score=7.5,
            coarse_rank=2,
        )

        evidence_items = build_evidence_items(
            [
                (
                    docs_candidate,
                    {
                        "success": True,
                        "url": docs_candidate.url,
                        "quality_score": 90,
                        "markdown": "Official ExampleLib docs\n\nUse the official configuration guide to enable streaming mode.",
                    },
                ),
                (
                    blog_candidate,
                    {
                        "success": True,
                        "url": blog_candidate.url,
                        "quality_score": 75,
                        "markdown": "Personal blog\n\nI think this setup works for my machine.",
                    },
                ),
            ],
            plan,
        )

        top_items, ranked = rerank_evidence_items(evidence_items, plan, limit=2)

        self.assertEqual(top_items[0].url, docs_candidate.url)
        self.assertGreater(ranked[0].score, ranked[1].score)

    def test_should_stop_fetching_when_strong_fact_evidence_exists(self) -> None:
        plan = plan_query("OpenAI Responses API official docs")
        candidate = FetchedCandidate(
            title="OpenAI Docs",
            url="https://platform.openai.com/docs/api-reference/responses",
            description="official docs",
            source="browser",
            query_variant="Responses API official docs",
            query_reason="official_docs",
            coarse_score=9.2,
            coarse_rank=1,
        )
        evidence_items = build_evidence_items(
            [
                (
                    candidate,
                    {
                        "success": True,
                        "url": candidate.url,
                        "quality_score": 92,
                        "markdown": "Responses API\n\nThe Responses API is the primary API for text, image, and tool use workflows.",
                    },
                )
            ],
            plan,
        )

        self.assertTrue(should_stop_fetching(evidence_items, plan))

    def test_build_evidence_pack_collects_confidence(self) -> None:
        plan = plan_query("latest ExampleLib release")
        candidate = FetchedCandidate(
            title="ExampleLib release notes 2026",
            url="https://docs.example.com/releases/2026",
            description="release notes",
            source="browser",
            query_variant="ExampleLib latest release notes",
            query_reason="release_notes",
            coarse_score=8.7,
            coarse_rank=1,
        )
        evidence_items = build_evidence_items(
            [
                (
                    candidate,
                    {
                        "success": True,
                        "url": candidate.url,
                        "quality_score": 86,
                        "markdown": "Release notes\n\nVersion 3.0 was updated in 2026 with faster search and better retry behavior.",
                    },
                )
            ],
            plan,
        )
        pack = build_evidence_pack(evidence_items, plan, limit=2)

        self.assertEqual(len(pack.items), 1)
        self.assertGreater(pack.confidence, 0)
        self.assertIn(candidate.url, pack.top_sources)


if __name__ == "__main__":
    unittest.main()
