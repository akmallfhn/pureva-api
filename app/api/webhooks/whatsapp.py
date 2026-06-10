from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from app.agents.pureva import pureva_graph
from app.core.auth import verify_token

router = APIRouter(prefix="/webhook/whatsapp", tags=["webhook:whatsapp"])


class IncomingMessage(BaseModel):
    conv_id: str
    wam_id: str = ""
    phone: str = ""
    name: str = ""
    message: str
    direction: str = "inbound"
    sender_type: str = "user"


class MessageResponse(BaseModel):
    received: bool
    conv_id: str
    timestamp: datetime


async def _run_agent(payload: IncomingMessage) -> None:
    await pureva_graph.ainvoke(
        {
            "messages": [HumanMessage(content=payload.message, id=payload.wam_id or None)],
            "conv_id": payload.conv_id,
            "wam_id": payload.wam_id,
            "name": payload.name,
            "phone": payload.phone,
            "treatments": [],
            "doctors": [],
            "discounts": [],
            "history": [],
            "today": "",
            "today_discount": None,
            "intent": "general_info",
            "draft": "",
            "escalate": False,
            "booking_result": {},
            "bubbles": [],
        }
    )


@router.post("/message", response_model=MessageResponse, dependencies=[Depends(verify_token)])
async def receive_message(
    payload: IncomingMessage, background_tasks: BackgroundTasks
) -> MessageResponse:
    if payload.direction == "inbound" and payload.sender_type == "user" and payload.message.strip():
        background_tasks.add_task(_run_agent, payload)

    return MessageResponse(received=True, conv_id=payload.conv_id, timestamp=datetime.now())
