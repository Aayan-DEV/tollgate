"""Gemini on Vertex AI (the commercial API), using the service account in .env."""

from __future__ import annotations

import asyncio
import base64
import json
import random
import time
from pathlib import Path

from google import genai
from google.genai import errors, types
from google.oauth2 import service_account

from tollgate.llm.base import ToolCall, Turn, load_env, note

ROOT = Path(__file__).resolve().parents[2]
_client: genai.Client | None = None


def client() -> genai.Client:
    global _client
    if _client is None:
        env = load_env(ROOT / ".env")
        raw = env["SERVICE_ACCOUNT_JSON"]
        try:
            info = json.loads(raw)
        except json.JSONDecodeError:
            info = json.loads(base64.b64decode(raw))
        creds = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        _client = genai.Client(vertexai=True, project=env["GCP_PROJECT_ID"], location=env.get("GCP_LOCATION", "europe-west4"),
                               credentials=creds, http_options=types.HttpOptions(timeout=120_000))
    return _client


RETRYABLE = {429, 500, 503}


EMPTY_RETRIES = 2


async def _generate(**kwargs):
    """generate_content with exponential backoff: Vertex throttles bursts (429) on shared quota."""
    for attempt in range(6):
        try:
            return await client().aio.models.generate_content(**kwargs)
        except errors.APIError as err:
            if err.code not in RETRYABLE or attempt == 5:
                raise
            delay = min(30, 2 ** attempt) + random.random()
            note(f"Gemini is busy (error {err.code}); trying again in {delay:.0f} s, attempt {attempt + 2} of 6")
            await asyncio.sleep(delay)


def _usage(resp) -> tuple[int, int]:
    u = resp.usage_metadata
    if not u:
        return 0, 0
    return u.prompt_token_count or 0, (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)


class GeminiChat:
    provider = "gemini"

    def __init__(self, model: str, system: str, tools: list[dict]):
        self.model = model
        decls = []
        for t in tools:
            has_params = bool(t.get("parameters", {}).get("properties"))
            decls.append(types.FunctionDeclaration(name=t["name"], description=t["description"],
                                                   parameters_json_schema=t["parameters"] if has_params else None))
        self.config = types.GenerateContentConfig(
            system_instruction=system, tools=[types.Tool(function_declarations=decls)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        self.contents: list[types.Content] = []
        self.pending: list[types.Part] = []

    def _flush(self) -> None:
        if self.pending:
            self.contents.append(types.Content(role="user", parts=self.pending))
            self.pending = []

    def add_user(self, text: str) -> None:
        self._flush()
        self.contents.append(types.Content(role="user", parts=[types.Part(text=text)]))

    def add_assistant(self, text: str) -> None:
        self._flush()
        self.contents.append(types.Content(role="model", parts=[types.Part(text=text)]))

    def add_tool_result(self, call: ToolCall, result: object) -> None:
        self.pending.append(types.Part.from_function_response(name=call.name, response={"result": result}))

    async def step(self) -> Turn:
        self._flush()
        t0 = time.perf_counter()
        tin = tout = 0
        for _ in range(EMPTY_RETRIES + 1):
            resp = await _generate(model=self.model, contents=self.contents, config=self.config)
            content = resp.candidates[0].content if resp.candidates else None
            calls = [ToolCall(fc.name, dict(fc.args or {})) for fc in (resp.function_calls or [])]
            text = "".join(p.text for p in (content.parts if content and content.parts else []) if p.text and not p.thought)
            a, b = _usage(resp)
            tin, tout = tin + a, tout + b
            if calls or text.strip():
                self.contents.append(content)  # keeps thought signatures intact
                break
            # An empty reply (no text, no tool call; e.g. a malformed function call) is not an answer: ask again.
            note("the model sent an empty reply; asking again")
        return Turn(text, calls, tin, tout, (time.perf_counter() - t0) * 1000)


async def complete_json(model: str, system: str, prompt: str, schema: dict, timeout: float) -> tuple[dict, Turn]:
    t0 = time.perf_counter()
    resp = await _generate(
        model=model, contents=prompt,
        config=types.GenerateContentConfig(system_instruction=system, temperature=0, response_mime_type="application/json",
                                           response_json_schema=schema,
                                           automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                                           http_options=types.HttpOptions(timeout=int(timeout * 1000))))
    tin, tout = _usage(resp)
    turn = Turn(resp.text or "", [], tin, tout, (time.perf_counter() - t0) * 1000)
    return json.loads(turn.text), turn
