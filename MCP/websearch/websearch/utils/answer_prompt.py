from __future__ import annotations

from typing import Iterable

from .evidence_ranker import EvidenceItem
from .query_planner import QueryPlan


def build_ai_answer_prompt(plan: QueryPlan, evidence_items: Iterable[EvidenceItem]) -> str:
    items = list(evidence_items)
    preferences = ", ".join(plan.source_preferences) or "general"
    constraints = ", ".join(f"{key}={value}" for key, value in plan.constraints.items()) or "none"
    evidence_lines: list[str] = []
    for index, item in enumerate(items[:4], start=1):
        evidence_lines.append(
            f"[{index}] title={item.title}\n"
            f"source={item.url}\n"
            f"score={item.score}\n"
            f"tags={', '.join(item.reason_tags) or 'none'}"
        )
        for snippet in item.snippets[:3]:
            evidence_lines.append(f"snippet/{snippet.kind}: {snippet.text}")
    evidence_block = "\n".join(evidence_lines) or "No reliable evidence collected."

    answer_shape = {
        "official_docs": "Summarize the official guidance first, then mention practical caveats.",
        "howto": "Answer with concise steps, prerequisites, and pitfalls.",
        "troubleshoot": "Answer with probable root cause, validation hints, and fixes in priority order.",
        "compare": "Answer with a compact comparison, then recommend when to choose each option.",
        "latest": "Answer with the most recent information you can justify and mention dates/versions explicitly.",
        "fact": "Answer directly, then support the answer with brief evidence.",
    }.get(plan.intent, "Answer directly, then support the answer with brief evidence.")

    return (
        "You are a grounded web research assistant. Answer only from the evidence below and do not invent facts.\n"
        f"Original question: {plan.raw_query}\n"
        f"Normalized question: {plan.normalized_query}\n"
        f"Intent: {plan.intent}\n"
        f"Preferred sources: {preferences}\n"
        f"Constraints: {constraints}\n"
        f"Instruction: {answer_shape}\n"
        "Evidence:\n"
        f"{evidence_block}\n"
        "Output requirements:\n"
        "1) Start with a direct answer.\n"
        "2) Then provide a short 'Why' section grounded in the evidence.\n"
        "3) If evidence conflicts or is incomplete, explicitly state the uncertainty.\n"
        "4) End with a final section that starts with `SOURCES:` and then list one URL per line.\n"
        "5) Do not include URLs anywhere except the SOURCES section."
    )


def build_fallback_answer_summary(plan: QueryPlan, evidence_items: Iterable[EvidenceItem]) -> str:
    items = [item for item in evidence_items if item.snippets]
    if not items:
        return ""

    opener = {
        "official_docs": "基于高可信来源，当前更可靠的结论是：",
        "howto": "基于抓取到的高相关页面，可以先按下面的方向处理：",
        "troubleshoot": "结合已抓取证据，优先排查这些方向：",
        "compare": "结合当前证据，主要差异可以概括为：",
        "latest": "结合当前高分页面，较新的信息集中在这些结论上：",
        "fact": "结合当前抓取证据，可以先得到这个结论：",
    }.get(plan.intent, "结合当前抓取证据，可以先得到这个结论：")

    lines = [opener]
    for item in items[:3]:
        ordered_snippets = sorted(
            item.snippets,
            key=lambda snippet: (0 if snippet.kind == "steps" else 1, -snippet.local_score),
        )
        for snippet in ordered_snippets[:2]:
            lines.append(f"- {snippet.text.strip()[:180]}")
    lines.append("来源已按证据强度排序，可结合结果列表继续深入查看。")
    return "\n".join(lines)


__all__ = ["build_ai_answer_prompt", "build_fallback_answer_summary"]

