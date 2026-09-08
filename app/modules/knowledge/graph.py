"""Graph agent Knowledge: plan -> retrieve (berulang) -> answer; planner dan penjawab dipisah."""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from app.modules.knowledge.llm import answer_llm, planner_llm
from app.modules.knowledge.schema import AgentState, RetrievedSource
from app.modules.knowledge.tools import ToolBox

logger = logging.getLogger(__name__)

# Cukup untuk "ambil ringkasan, lalu dalami satu brand"; lebih dari ini biasanya model nyasar.
MAX_ROUNDS = 3

# Hasil satu tool dipotong sebelum masuk prompt penjawab; transkrip panjang paling sering kena.
MAX_RESULT_CHARS = 12_000

Emit = Callable[[str, str], Awaitable[None]]


def build_knowledge_graph(*, toolbox: ToolBox, system_prompt: str, emit: Emit):
    async def plan_node(state: AgentState) -> AgentState:
        messages = state.get("messages") or _initial_messages(state, system_prompt)
        try:
            reply = await planner_llm().ainvoke(messages, config={"run_name": "knowledge-plan"})
        except Exception as e:
            logger.exception("knowledge: plan failed")
            # Planner gagal bukan alasan diam; penjawab masih bisa menjelaskan keterbatasannya.
            return {"messages": messages, "error": f"plan: {e}"}

        return {"messages": [*messages, reply], "rounds": state.get("rounds", 0) + 1}

    async def retrieve_node(state: AgentState) -> AgentState:
        messages = list(state["messages"])
        calls = getattr(messages[-1], "tool_calls", None) or []
        sources = list(state.get("sources") or [])

        for call in calls:
            name = call.get("name", "")
            args = call.get("args") or {}
            result, summary = await toolbox.run(name, args)

            sources.append(RetrievedSource(tool=name, arguments=args, summary=summary))
            # Satu string siap tampil; bentuk terstrukturnya sudah ikut tersimpan ke kb_chats.
            await emit("source", f"{name} · {summary}" if summary else name)

            messages.append(
                ToolMessage(content=result[:MAX_RESULT_CHARS], tool_call_id=call.get("id", name))
            )

        return {"messages": messages, "sources": sources}

    async def answer_node(state: AgentState) -> AgentState:
        messages = [
            SystemMessage(content=system_prompt),
            *_history_messages(state.get("history") or []),
            HumanMessage(content=_answer_prompt(state)),
        ]

        chunks: list[str] = []
        try:
            async for chunk in answer_llm().astream(
                messages, config={"run_name": "knowledge-answer"}
            ):
                text = _text_of(chunk)
                if not text:
                    continue
                chunks.append(text)
                await emit("delta", text)
        except Exception as e:
            logger.exception("knowledge: answer failed")
            return {"answer": "".join(chunks), "error": f"answer: {e}"}

        return {"answer": "".join(chunks)}

    def after_plan(state: AgentState) -> str:
        messages = state.get("messages") or []
        if state.get("error") or not messages:
            return "answer"
        if state.get("rounds", 0) >= MAX_ROUNDS:
            return "answer"
        return "retrieve" if getattr(messages[-1], "tool_calls", None) else "answer"

    graph = StateGraph(AgentState)
    graph.add_node("plan", plan_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("answer", answer_node)

    graph.set_entry_point("plan")
    graph.add_conditional_edges("plan", after_plan, {"retrieve": "retrieve", "answer": "answer"})
    graph.add_edge("retrieve", "plan")
    graph.add_edge("answer", END)

    return graph.compile()


def _initial_messages(state: AgentState, system_prompt: str) -> list[Any]:
    return [
        SystemMessage(content=system_prompt),
        *_history_messages(state.get("history") or []),
        HumanMessage(content=state["question"]),
    ]


def _history_messages(history: list[dict[str, str]]) -> list[Any]:
    out: list[Any] = []
    for turn in history:
        content = turn.get("message") or ""
        if not content:
            continue
        out.append(
            HumanMessage(content=content)
            if turn.get("role") == "user"
            else AIMessage(content=content)
        )
    return out


def _answer_prompt(state: AgentState) -> str:
    """Rakit pertanyaan + hasil retrieval jadi satu prompt untuk penjawab."""
    blocks: list[str] = []
    messages = state.get("messages") or []
    sources = state.get("sources") or []

    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    for source, message in zip(sources, tool_messages, strict=False):
        blocks.append(f"### {source.tool} {source.arguments}\n{message.content}")

    if not blocks:
        note = state.get("error") or "tidak ada tool yang dipanggil"
        blocks.append(f"(retrieval tidak menghasilkan data: {note})")

    return (
        f"Pertanyaan: {state['question']}\n\n"
        "Data hasil retrieval di bawah ini adalah satu-satunya sumber angka yang boleh kamu "
        "pakai. Kalau data yang dibutuhkan tidak ada di sini, katakan begitu.\n\n"
        + "\n\n".join(blocks)
    )


def _text_of(chunk: Any) -> str:
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    # Model tertentu mengirim content sebagai daftar blok; ambil yang bertipe teks saja.
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))
