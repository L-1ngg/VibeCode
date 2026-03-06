import unittest

from websearch.utils.query_planner import normalize_query, plan_query


class QueryPlannerTests(unittest.TestCase):
    def test_normalize_query_collapses_spaces(self) -> None:
        self.assertEqual(normalize_query("  LangChain   FastAPI   streaming  "), "LangChain FastAPI streaming")

    def test_plan_query_detects_howto_and_rewrites(self) -> None:
        plan = plan_query("LangChain FastAPI streaming best practices")

        self.assertEqual(plan.intent, "howto")
        self.assertGreaterEqual(len(plan.rewrites), 3)
        self.assertTrue(any("official documentation" in item.query for item in plan.rewrites))
        self.assertIn("LangChain", plan.entities)

    def test_plan_query_detects_site_and_latest(self) -> None:
        plan = plan_query("site:openai.com latest responses api release")

        self.assertTrue(plan.is_site_query)
        self.assertEqual(plan.intent, "latest")
        self.assertEqual(plan.constraints.get("site"), "openai.com")


if __name__ == "__main__":
    unittest.main()
