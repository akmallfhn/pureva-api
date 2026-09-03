"""Entry point evaluator lead, dipanggil sesudah webhook menyimpan pesan."""

import logging
from typing import Any

from app.modules.agents.lead_evaluation.graph import Evaluator, build_lead_eval_graph
from app.modules.agents.lead_evaluation.repository import LeadEvalRepository
from app.modules.agents.lead_evaluation.schema import EvalState

logger = logging.getLogger(__name__)


class LeadEvaluationService:
    def __init__(self, *, repo: LeadEvalRepository, evaluate: Evaluator, enabled: bool) -> None:
        self._enabled = enabled
        self._graph = build_lead_eval_graph(repo=repo, evaluate=evaluate)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def evaluate(self, conv_id: str) -> dict[str, Any]:
        """Nilai satu percakapan. Kegagalan dicatat, tidak dilempar ke pemanggil webhook."""
        if not self._enabled:
            return {}

        state: EvalState = {"conv_id": conv_id}
        result = await self._graph.ainvoke(state)

        error = result.get("error")
        if error:
            logger.warning(f"lead-eval: {conv_id} skipped ({error})")
        return result.get("changes") or {}
