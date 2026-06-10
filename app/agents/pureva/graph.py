"""LangGraph orchestration untuk WhatsApp Agent Klinik Pureva.

Alur (sesuai diagram arsitektur):

    Context Fetcher -> Intent Classifier -> [ Skin Assessment | Booking |
    Complaint | General Info ] -> Memory (Shared State Update) -> Send Message -> END

Model assignment:
  - GPT-4o (fast)       : Context fetch helper, Intent Classifier, Booking, General Info, Send Message
  - GPT-4.5 (reasoning) : Skin Assessment & Recommendation, Complaint
"""

import asyncio
import json
import logging
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.agents.base.llm import build_llm
from app.agents.pureva.prompts import (
    BOOKING_DRAFT_PROMPT,
    BOOKING_EXTRACT_PROMPT,
    COMPLAINT_PROMPT,
    GENERAL_INFO_PROMPT,
    INTENT_CLASSIFIER_PROMPT,
    SEND_MESSAGE_PROMPT,
    SKIN_ASSESSMENT_PROMPT,
)
from app.agents.pureva.services import (
    create_booking,
    discount_for_day,
    doctors_on_day,
    list_discounts,
    list_doctors,
    list_treatments,
    recent_history,
    save_message,
    search_treatments,
    send_whatsapp_message,
    today_day_name,
)
from app.agents.pureva.utils import (
    derive_keywords,
    format_all_discounts,
    format_discount,
    format_doctors,
    format_history,
    format_treatments,
    route_by_intent,
    split_bubbles,
)
from app.core.config import settings

logger = logging.getLogger(__name__)

VALID_INTENTS = {"skin_consult", "booking", "complaint", "general_info"}


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    conv_id: str
    wam_id: str
    name: str
    phone: str
    # context (Shared State, diisi Context Fetcher)
    treatments: list
    doctors: list
    discounts: list
    history: list
    today: str
    today_discount: dict | None
    # hasil node
    intent: str
    draft: str
    escalate: bool
    booking_result: dict
    bubbles: list[str]


class IntentDecision(BaseModel):
    intent: Literal["skin_consult", "booking", "complaint", "general_info"] = Field(
        description="Kategori intent dari pesan terakhir pasien."
    )


class BookingExtraction(BaseModel):
    treatment: str = Field(default="", description="Nama treatment yang dimaksud pasien, kalau ada.")
    preferred_day: str = Field(default="", description="Hari yang diminta (Senin..Minggu), kalau ada.")
    preferred_time: str = Field(default="", description="Jam yang diminta, kalau ada.")
    doctor: str = Field(default="", description="Nama dokter yang diminta, kalau ada.")
    intent_action: Literal["create", "reschedule", "cancel", "ask_availability"] = Field(
        default="ask_availability", description="Aksi booking yang diinginkan pasien."
    )


def _latest_user_message(state: AgentState) -> str:
    return next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )


# --------------------------------------------------------------------------- #
# 1. Context Fetcher Node  (inject Shared State)
# --------------------------------------------------------------------------- #
async def context_fetcher_node(state: AgentState) -> AgentState:
    try:
        phone = state.get("phone") or ""
        message = _latest_user_message(state)
        today = today_day_name()

        # Ambil semua context yang dibutuhkan downstream sekaligus.
        doctors = list_doctors()
        discounts = list_discounts()
        today_discount = discount_for_day(today)
        history = recent_history(state["conv_id"], limit=10)

        keywords = derive_keywords(message)
        treatments = search_treatments(keywords, limit=15)
        if not treatments:
            treatments = list_treatments(limit=15)

        logger.info(
            f"context_fetcher: conv_id={state['conv_id']} phone={phone} "
            f"treatments={len(treatments)} doctors={len(doctors)} today={today}"
        )
        return {
            "doctors": doctors,
            "discounts": discounts,
            "today": today,
            "today_discount": today_discount,
            "history": history,
            "treatments": treatments,
        }
    except Exception as e:
        logger.error(f"context_fetcher_node failed: {e}")
        return {
            "doctors": [],
            "discounts": [],
            "today": today_day_name(),
            "today_discount": None,
            "history": [],
            "treatments": [],
        }


# --------------------------------------------------------------------------- #
# 2. Intent Classifier Node  (GPT-4o)
# --------------------------------------------------------------------------- #
async def intent_classifier_node(state: AgentState) -> AgentState:
    message = _latest_user_message(state)
    try:
        llm = build_llm(provider="openai", model=settings.model_fast, max_tokens=16, temperature=0)
        structured = llm.with_structured_output(IntentDecision)
        result: IntentDecision = await structured.ainvoke(
            INTENT_CLASSIFIER_PROMPT.format(
                history=format_history(state.get("history", [])),
                message=message,
            )
        )
        intent = result.intent if result.intent in VALID_INTENTS else "general_info"
    except Exception as e:
        logger.error(f"intent_classifier_node failed: {e}")
        intent = "general_info"

    logger.info(f"intent_classifier: conv_id={state['conv_id']} intent={intent}")
    return {"intent": intent}


# --------------------------------------------------------------------------- #
# 3a. Skin Assessment & Recommendation Node  (GPT-4.5, deep reasoning)
# --------------------------------------------------------------------------- #
async def skin_assessment_node(state: AgentState) -> AgentState:
    message = _latest_user_message(state)
    try:
        llm = build_llm(
            provider="openai", model=settings.model_reasoning, max_tokens=1200, temperature=0.5
        )
        prompt = SKIN_ASSESSMENT_PROMPT.format(
            treatments=format_treatments(state.get("treatments", []), limit=18),
            today=state.get("today", ""),
            discount=format_discount(state.get("today_discount")),
            history=format_history(state.get("history", [])),
            message=message,
        )
        response = await llm.ainvoke(prompt)
        draft = response.content if isinstance(response.content, str) else str(response.content)
        return {"draft": draft.strip()}
    except Exception as e:
        logger.error(f"skin_assessment_node failed: {e}")
        return {"draft": "Aku bantu rekomendasikan treatment yang cocok ya, boleh cerita lebih detail soal keluhan kulitnya?"}


# --------------------------------------------------------------------------- #
# 3b. Booking Node  (GPT-4o, structured output + cek jadwal)
# --------------------------------------------------------------------------- #
async def booking_node(state: AgentState) -> AgentState:
    message = _latest_user_message(state)
    doctors = state.get("doctors", [])
    try:
        # Step 1: ekstrak detail booking (structured output).
        extract_llm = build_llm(
            provider="openai", model=settings.model_fast, max_tokens=256, temperature=0
        )
        structured = extract_llm.with_structured_output(BookingExtraction)
        extracted: BookingExtraction = await structured.ainvoke(
            BOOKING_EXTRACT_PROMPT.format(
                doctors=format_doctors(doctors),
                treatments=format_treatments(state.get("treatments", []), limit=12),
                history=format_history(state.get("history", [])),
                message=message,
            )
        )

        # Step 2: cek ketersediaan dokter dari jadwal (deterministik).
        day = extracted.preferred_day.strip()
        available = doctors_on_day(day) if day else []
        availability_text = format_doctors(available) if available else (
            f"Tidak ada dokter praktik di hari {day}." if day
            else "Pasien belum menyebut hari spesifik."
        )

        # Step 3: kalau info cukup, buat booking tentatif di DB.
        booking_status = "Belum ada booking dibuat (info belum lengkap)."
        booking_result: dict = {}
        if extracted.intent_action == "create" and available and extracted.treatment:
            doc = available[0]
            today_disc = state.get("today_discount") or {}
            booking_id = create_booking(
                conv_id=state["conv_id"],
                phone=state.get("phone", ""),
                name=state.get("name", ""),
                treatment=extracted.treatment,
                doctor=doc.get("name", ""),
                day=day,
                hours=doc.get("hours", ""),
                price_text="",
                discount_percent=int(today_disc.get("discount_percent", 0) or 0),
            )
            booking_result = {
                "booking_id": booking_id,
                "treatment": extracted.treatment,
                "doctor": doc.get("name", ""),
                "day": day,
                "hours": doc.get("hours", ""),
            }
            booking_status = (
                f"Booking TENTATIF #{booking_id} dibuat: {extracted.treatment} dengan "
                f"{doc.get('name', '')} hari {day} jam {doc.get('hours', '')}. "
                "Perlu konfirmasi akhir dari pasien."
            )

        # Step 4: susun draf balasan.
        draft_llm = build_llm(
            provider="openai", model=settings.model_fast, max_tokens=700, temperature=0.4
        )
        response = await draft_llm.ainvoke(
            BOOKING_DRAFT_PROMPT.format(
                extracted=json.dumps(extracted.model_dump(), ensure_ascii=False, indent=2),
                availability=availability_text,
                booking_status=booking_status,
                today=state.get("today", ""),
                discount=format_discount(state.get("today_discount")),
                message=message,
            )
        )
        draft = response.content if isinstance(response.content, str) else str(response.content)
        logger.info(
            f"booking_node: conv_id={state['conv_id']} action={extracted.intent_action} "
            f"day={day or '-'} created={'yes' if booking_result else 'no'}"
        )
        return {"draft": draft.strip(), "booking_result": booking_result}
    except Exception as e:
        logger.error(f"booking_node failed: {e}")
        return {
            "draft": "Boleh aku bantu booking ya. Mau treatment apa, dan kira-kira hari apa kamu bisa datang?",
            "booking_result": {},
        }


# --------------------------------------------------------------------------- #
# 3c. Complaint Node  (GPT-4.5, empati + analisis)
# --------------------------------------------------------------------------- #
async def complaint_node(state: AgentState) -> AgentState:
    message = _latest_user_message(state)
    try:
        llm = build_llm(
            provider="openai", model=settings.model_reasoning, max_tokens=1000, temperature=0.6
        )
        response = await llm.ainvoke(
            COMPLAINT_PROMPT.format(
                treatments=format_treatments(state.get("treatments", []), limit=10),
                history=format_history(state.get("history", [])),
                message=message,
            )
        )
        raw = response.content if isinstance(response.content, str) else str(response.content)
        raw = raw.strip()

        escalate = False
        first_line, _, rest = raw.partition("\n")
        if "URGENSI" in first_line.upper():
            escalate = "TINGGI" in first_line.upper()
            draft = rest.strip() or raw
        else:
            draft = raw

        logger.info(f"complaint_node: conv_id={state['conv_id']} escalate={escalate}")
        return {"draft": draft, "escalate": escalate}
    except Exception as e:
        logger.error(f"complaint_node failed: {e}")
        return {
            "draft": "Aku turut prihatin dengan keluhanmu. Biar aman, sebaiknya kita jadwalkan kontrol dengan dokter ya supaya bisa diperiksa langsung.",
            "escalate": True,
        }


# --------------------------------------------------------------------------- #
# 3d. General Info Node  (GPT-4o)
# --------------------------------------------------------------------------- #
async def general_info_node(state: AgentState) -> AgentState:
    message = _latest_user_message(state)
    try:
        llm = build_llm(
            provider="openai", model=settings.model_fast, max_tokens=700, temperature=0.5
        )
        response = await llm.ainvoke(
            GENERAL_INFO_PROMPT.format(
                today=state.get("today", ""),
                discount=format_discount(state.get("today_discount")),
                all_discounts=format_all_discounts(state.get("discounts", [])),
                treatments=format_treatments(state.get("treatments", []), limit=12),
                doctors=format_doctors(state.get("doctors", [])),
                history=format_history(state.get("history", [])),
                message=message,
            )
        )
        draft = response.content if isinstance(response.content, str) else str(response.content)
        return {"draft": draft.strip()}
    except Exception as e:
        logger.error(f"general_info_node failed: {e}")
        return {"draft": "Halo, terima kasih sudah menghubungi Klinik Pureva. Ada yang bisa Vera bantu soal perawatan kulit, booking, atau info promo?"}


# --------------------------------------------------------------------------- #
# 4. Memory Node (Shared State Update)  - NO LLM
# --------------------------------------------------------------------------- #
async def memory_node(state: AgentState) -> AgentState:
    try:
        message = _latest_user_message(state)
        intent = state.get("intent", "")
        # Simpan pesan pasien ke histori percakapan (refresh konteks sesi).
        save_message(state["conv_id"], "user", message, intent)

        logger.info(f"memory_node: conv_id={state['conv_id']} saved user msg intent={intent}")
    except Exception as e:
        logger.error(f"memory_node failed: {e}")
    return {}


# --------------------------------------------------------------------------- #
# 7. Send Message Node  (GPT-4o, tone-adjusted final + kirim WA)
# --------------------------------------------------------------------------- #
async def send_message_node(state: AgentState) -> AgentState:
    draft = state.get("draft", "").strip()
    try:
        llm = build_llm(
            provider="openai", model=settings.model_fast, max_tokens=900, temperature=0.6
        )
        response = await llm.ainvoke(
            SEND_MESSAGE_PROMPT.format(
                name=state.get("name", "") or "(tidak diketahui)",
                intent=state.get("intent", ""),
                draft=draft or "(kosong)",
            )
        )
        final = response.content if isinstance(response.content, str) else str(response.content)
    except Exception as e:
        logger.error(f"send_message_node format failed: {e}")
        final = draft or "Maaf, ada gangguan sebentar. Boleh diulang ya 🙏"

    bubbles = split_bubbles(final)

    # Kalau complaint urgensi tinggi, tambahkan penegasan eskalasi ke CS/dokter.
    if state.get("escalate"):
        bubbles.append(
            "Karena ini perlu penanganan cepat, aku teruskan ke tim klinik & dokter ya. "
            "Tim kami akan segera menghubungimu. 🙏"
        )

    phone = state.get("phone", "")
    try:
        for bubble in bubbles:
            await send_whatsapp_message(phone, bubble)
            await asyncio.sleep(0.5)
    except Exception as e:
        logger.error(f"send_message_node send failed: {e}")

    # Simpan balasan asisten ke memory.
    try:
        save_message(state["conv_id"], "assistant", " ".join(bubbles), state.get("intent", ""))
    except Exception as e:
        logger.error(f"send_message_node save reply failed: {e}")

    return {"messages": [AIMessage(content=" ".join(bubbles))], "bubbles": bubbles}


# --------------------------------------------------------------------------- #
# Build graph
# --------------------------------------------------------------------------- #
def build_pureva_graph():
    graph = StateGraph(AgentState)

    graph.add_node("context_fetcher", context_fetcher_node)
    graph.add_node("intent_classifier", intent_classifier_node)
    graph.add_node("skin_assessment", skin_assessment_node)
    graph.add_node("booking", booking_node)
    graph.add_node("complaint", complaint_node)
    graph.add_node("general_info", general_info_node)
    graph.add_node("memory", memory_node)
    graph.add_node("send_message", send_message_node)

    graph.set_entry_point("context_fetcher")
    graph.add_edge("context_fetcher", "intent_classifier")
    graph.add_conditional_edges(
        "intent_classifier",
        route_by_intent,
        {
            "skin_assessment": "skin_assessment",
            "booking": "booking",
            "complaint": "complaint",
            "general_info": "general_info",
        },
    )
    for branch in ("skin_assessment", "booking", "complaint", "general_info"):
        graph.add_edge(branch, "memory")
    graph.add_edge("memory", "send_message")
    graph.add_edge("send_message", END)

    return graph.compile()


pureva_graph = build_pureva_graph()
