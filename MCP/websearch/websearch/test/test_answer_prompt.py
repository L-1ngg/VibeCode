import unittest

from websearch.utils.answer_prompt import build_ai_answer_prompt, build_fallback_answer_summary
from websearch.utils.evidence_ranker import build_evidence_items, build_evidence_pack, FetchedCandidate
from websearch.utils.query_planner import plan_query


class AnswerPromptTests(unittest.TestCase):
    def test_build_ai_answer_prompt_contains_evidence_and_constraints(self) -> None:
        plan = plan_query("site:openai.com latest responses api release")
        candidate = FetchedCandidate(
            title="Responses API release notes",
            url="https://openai.com/index/responses-api-release",
            description="release notes",
            source="browser",
            query_variant="responses api latest release",
            query_reason="release_notes",
            coarse_score=8.5,
            coarse_rank=1,
        )
        evidence_items = build_evidence_items(
            [
                (
                    candidate,
                    {
                        "success": True,
                        "url": candidate.url,
                        "quality_score": 82,
                        "markdown": "Responses API release notes\n\nUpdated in 2026 with new tool calling details and migration guidance.",
                    },
                )
            ],
            plan,
        )

        prompt = build_ai_answer_prompt(plan, evidence_items)

        self.assertIn("Original question", prompt)
        self.assertIn("Intent: latest", prompt)
        self.assertIn("Constraints:", prompt)
        self.assertIn(candidate.url, prompt)
        self.assertIn("Updated in 2026", prompt)

    def test_build_fallback_answer_summary_uses_top_evidence(self) -> None:
        plan = plan_query("How to configure ExampleLib streaming")
        candidate = FetchedCandidate(
            title="ExampleLib docs",
            url="https://docs.example.com/streaming",
            description="official docs",
            source="browser",
            query_variant="ExampleLib docs",
            query_reason="official_docs",
            coarse_score=8.8,
            coarse_rank=1,
        )
        evidence_items = build_evidence_items(
            [
                (
                    candidate,
                    {
                        "success": True,
                        "url": candidate.url,
                        "quality_score": 88,
                        "markdown": "Streaming guide\n\nStep 1: enable the transport.\n\nStep 2: configure the callback handler.",
                    },
                )
            ],
            plan,
        )
        summary = build_fallback_answer_summary(plan, evidence_items)

        self.assertIn("可以先按下面的方向处理", summary)
        self.assertIn("Step 1", summary)


if __name__ == "__main__":
    unittest.main()
