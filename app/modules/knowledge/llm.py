"""Model dan batas token agent Knowledge."""

import logging
from functools import lru_cache

from langchain_core.messages import HumanMessage
from langchain_core.runnables import Runnable

from app.modules.agents.llm import build_llm
from app.modules.knowledge.prompts import TITLE_PROMPT
from app.modules.knowledge.tools import ToolBox

logger = logging.getLogger(__name__)

# Node planner cuma memilih tool dan mengisi argumennya, jadi model kecil sudah cukup.
PLANNER_MODEL = "gpt-4.1-mini"
PLANNER_MAX_TOKENS = 500

# Node jawaban dilihat orang, jadi model penuh; 1400 = 3x jawaban terpanjang yang terukur.
ANSWER_MODEL = "gpt-4.1"
ANSWER_MAX_TOKENS = 1400

TITLE_MODEL = "gpt-4.1-mini"
TITLE_MAX_TOKENS = 32

# Jawaban boleh lebih lama dari agent webhook: orangnya sedang menunggu di depan layar.
ANSWER_TIMEOUT = 180.0


@lru_cache(maxsize=1)
def planner_llm() -> Runnable:
    llm = build_llm(model=PLANNER_MODEL, max_tokens=PLANNER_MAX_TOKENS)
    return llm.bind_tools(ToolBox.definitions())


@lru_cache(maxsize=1)
def answer_llm() -> Runnable:
    return build_llm(model=ANSWER_MODEL, max_tokens=ANSWER_MAX_TOKENS, timeout=ANSWER_TIMEOUT)


@lru_cache(maxsize=1)
def title_llm() -> Runnable:
    return build_llm(model=TITLE_MODEL, max_tokens=TITLE_MAX_TOKENS)


async def generate_title(question: str) -> str:
    """Judul thread dari pertanyaan pertama; dipanggil sesudah jawaban selesai."""
    result = await title_llm().ainvoke(
        [HumanMessage(content=TITLE_PROMPT.format(question=question[:500]))],
        config={"run_name": "knowledge-title"},
    )
    return str(getattr(result, "content", "")).strip().strip('"').strip()
