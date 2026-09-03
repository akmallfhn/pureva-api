"""Graph evaluator lead: fetch_context -> evaluate -> persist."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph import END, StateGraph

from app.modules.agents.lead_evaluation.repository import LeadEvalRepository
from app.modules.agents.lead_evaluation.schema import (
    STAGE_ORDER,
    ConversationContext,
    EvalState,
    LeadEvaluation,
)

logger = logging.getLogger(__name__)

Evaluator = Callable[[ConversationContext], Awaitable[LeadEvaluation]]


def diff(ctx: ConversationContext, verdict: LeadEvaluation) -> dict[str, Any]:
    """Kebijakan tulis: brand & nilai project sekali isi, stage hanya maju, note selalu segar."""
    changes: dict[str, Any] = {}

    if ctx.brand_name is None and verdict.brand_name:
        changes["brand_name"] = verdict.brand_name.strip()

    if ctx.project_value is None and verdict.project_value is not None:
        changes["project_value"] = verdict.project_value

    current = STAGE_ORDER.get(ctx.lead_status, 0)
    proposed = STAGE_ORDER.get(verdict.lead_status, 0)
    if proposed > current:
        changes["lead_status"] = verdict.lead_status

    note = (verdict.note or "").strip()
    if note and note != (ctx.note or ""):
        changes["note"] = note

    return changes


def build_lead_eval_graph(*, repo: LeadEvalRepository, evaluate: Evaluator):
    async def fetch_context_node(state: EvalState) -> EvalState:
        conv_id = state["conv_id"]
        try:
            ctx = await repo.fetch_context(conv_id)
        except Exception as e:
            logger.exception(f"lead-eval: fetch_context failed for {conv_id}")
            return {"context": None, "error": f"fetch_context: {e}"}

        if ctx is None:
            return {"context": None, "error": "conversation not found"}
        if ctx.text_count == 0:
            # Percakapan yang isinya cuma sticker/media tidak punya bahan untuk dinilai.
            return {"context": None, "error": "no text message to evaluate"}
        return {"context": ctx}

    async def evaluate_node(state: EvalState) -> EvalState:
        ctx = state["context"]
        assert ctx is not None
        try:
            verdict = await evaluate(ctx)
        except Exception as e:
            logger.exception(f"lead-eval: evaluate failed for {ctx.conv_id}")
            return {"verdict": None, "error": f"evaluate: {e}"}
        return {"verdict": verdict}

    async def persist_node(state: EvalState) -> EvalState:
        ctx = state["context"]
        verdict = state["verdict"]
        assert ctx is not None and verdict is not None

        changes = diff(ctx, verdict)
        if not changes:
            return {"changes": {}}

        try:
            await repo.apply(ctx.conv_id, changes)
        except Exception as e:
            logger.exception(f"lead-eval: persist failed for {ctx.conv_id}")
            return {"changes": {}, "error": f"persist: {e}"}

        logger.info(f"lead-eval: {ctx.conv_id} updated {sorted(changes)}")
        return {"changes": changes}

    def has_context(state: EvalState) -> str:
        return "yes" if state.get("context") is not None else "no"

    def has_verdict(state: EvalState) -> str:
        return "yes" if state.get("verdict") is not None else "no"

    graph = StateGraph(EvalState)
    graph.add_node("fetch_context", fetch_context_node)
    graph.add_node("evaluate", evaluate_node)
    graph.add_node("persist", persist_node)

    graph.set_entry_point("fetch_context")
    graph.add_conditional_edges("fetch_context", has_context, {"yes": "evaluate", "no": END})
    graph.add_conditional_edges("evaluate", has_verdict, {"yes": "persist", "no": END})
    graph.add_edge("persist", END)

    return graph.compile()
