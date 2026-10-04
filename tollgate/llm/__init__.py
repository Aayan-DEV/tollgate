"""Provider routing: one place that picks Ollama (local) or Gemini (Vertex) by model name."""

from __future__ import annotations

from tollgate.llm.base import Chat, Turn, provider_of


def make_chat(model: str, system: str, tools: list[dict]) -> Chat:
    if provider_of(model) == "gemini":
        from tollgate.llm.gemini import GeminiChat
        return GeminiChat(model, system, tools)
    from tollgate.llm.ollama import OllamaChat
    return OllamaChat(model, system, tools)


async def complete_json(model: str, system: str, prompt: str, schema: dict, timeout: float) -> tuple[dict, Turn]:
    if provider_of(model) == "gemini":
        from tollgate.llm import gemini
        return await gemini.complete_json(model, system, prompt, schema, timeout)
    from tollgate.llm import ollama
    return await ollama.complete_json(model, system, prompt, schema, timeout)
