from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from app.core.config import settings


def build_llm(
    provider: str = "openai",
    model: str = "gpt-4o",
    max_tokens: int = 1024,
    temperature: float = 0.4,
    timeout: float = 120.0,
):
    """Factory LLM tipis supaya node tinggal panggil build_llm(...).

    provider: "openai" (default) atau "anthropic" kalau mau swap node reasoning.
    """
    if provider == "openai":
        return ChatOpenAI(
            model=model,
            api_key=settings.openai_api_key,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
    if provider == "anthropic":
        return ChatAnthropic(
            model=model,
            api_key=settings.anthropic_api_key,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
        )
    raise ValueError(f"Unsupported LLM provider: {provider!r}")
