"""Factory LLM bersama untuk semua agent automation di modul ini."""

from langchain_openai import ChatOpenAI

from app.core.config import settings

DEFAULT_TIMEOUT = 120.0


def is_configured() -> bool:
    """Tanpa API key semua agent mati; jalur utama aplikasi tetap jalan."""
    return bool(settings.openai_api_key)


def build_llm(*, model: str, max_tokens: int, timeout: float = DEFAULT_TIMEOUT) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        api_key=settings.openai_api_key,
        max_tokens=max_tokens,
        timeout=timeout,
    )
