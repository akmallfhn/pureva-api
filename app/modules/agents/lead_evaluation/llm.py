"""Pemanggilan LLM untuk agent lead evaluation."""

import logging
from functools import lru_cache

from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable

from app.modules.agents.lead_evaluation.prompts import EVALUATION_PROMPT
from app.modules.agents.lead_evaluation.schema import ConversationContext, LeadEvaluation
from app.modules.agents.llm import build_llm

logger = logging.getLogger(__name__)

MODEL = "gpt-4.1-mini"

# 3x balasan terpanjang yang realistis, yang terukur 129 token dengan o200k_base.
MAX_OUTPUT_TOKENS = 400


@lru_cache(maxsize=1)
def structured_llm() -> Runnable:
    llm = build_llm(model=MODEL, max_tokens=MAX_OUTPUT_TOKENS)
    return llm.with_structured_output(LeadEvaluation, method="json_schema")


def render_prompt(ctx: ConversationContext) -> str:
    return EVALUATION_PROMPT.format(
        full_name=ctx.full_name or "(tanpa nama)",
        phone_number=ctx.phone_number,
        chat_count=ctx.chat_count,
        transcript=ctx.transcript,
        brand_name=ctx.brand_name if ctx.brand_name is not None else "null",
        project_value=ctx.project_value if ctx.project_value is not None else "null",
        lead_status=ctx.lead_status,
        note=ctx.note if ctx.note is not None else "null",
    )


async def evaluate_with_llm(ctx: ConversationContext) -> LeadEvaluation:
    result = await structured_llm().ainvoke(
        [HumanMessage(content=render_prompt(ctx))],
        config={"run_name": "lead-evaluation"},
    )
    if isinstance(result, LeadEvaluation):
        return result
    return LeadEvaluation.model_validate(result)
