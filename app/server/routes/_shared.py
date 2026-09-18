"""Shared infrastructure for all route modules: response cache + FM API streaming."""

import time
import json
import logging
import aiohttp
from contextvars import ContextVar
from typing import Optional

from fastapi import Header

from server.security import resolve_principal, safe_error
from server.user_agent import with_ua

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Authenticated principal
# ---------------------------------------------------------------------------
async def current_principal(
    x_forwarded_email: Optional[str] = Header(default=None),
    x_forwarded_access_token: Optional[str] = Header(default=None),
) -> str:
    """The ownership key for this request's assessment history and saved plans.

    Every per-user route depends on this instead of reading the forwarded email
    header directly, so ownership is bound to an identity the app established —
    from the forwarded token where one exists — rather than to a header value a
    caller could set (CWE-290). This always resolves to a usable key, so no
    request is ever refused for lack of an identity.
    """
    return await resolve_principal(forwarded_email=x_forwarded_email,
                                   forwarded_token=x_forwarded_access_token)

# ---------------------------------------------------------------------------
# Lightweight server-side response cache. The assessment is relatively
# expensive (many system-table queries), so cache results briefly.
# ---------------------------------------------------------------------------
_response_cache: dict[str, tuple[float, object]] = {}
CACHE_TTL = 600  # seconds (10 min — environment metadata changes slowly)
MAX_CACHE_SIZE = 100


def _cache_get(key: str):
    entry = _response_cache.get(key)
    if entry is not None and time.time() - entry[0] < CACHE_TTL:
        return entry[1]
    return None


def _cache_set(key: str, value: object):
    if len(_response_cache) >= MAX_CACHE_SIZE:
        oldest_key = min(_response_cache, key=lambda k: _response_cache[k][0])
        del _response_cache[oldest_key]
    _response_cache[key] = (time.time(), value)


def _cache_clear():
    _response_cache.clear()


# ---------------------------------------------------------------------------
# AI Model toggle: per-request override via X-AI-Model header
# ---------------------------------------------------------------------------
DEFAULT_LLM_MODEL = "databricks-claude-sonnet-4-6"
_ai_model: ContextVar[str] = ContextVar("ai_model", default=DEFAULT_LLM_MODEL)

# AI_MODELS: label/provider lookup for known models. Used to provide pretty labels
# and provider info for models found via the workspace serving endpoints API.
# This is NOT the source of truth for availability — the actual available models
# come from list_available_models() which queries the workspace API.
AI_MODELS = {
    "databricks-claude-sonnet-5": {"label": "Claude Sonnet 5", "provider": "Anthropic"},
    "databricks-claude-opus-5": {"label": "Claude Opus 5", "provider": "Anthropic"},
    "databricks-claude-sonnet-4-6": {"label": "Claude Sonnet 4.6", "provider": "Anthropic"},
    "databricks-claude-opus-4-6": {"label": "Claude Opus 4.6", "provider": "Anthropic"},
    "databricks-gpt-5-6": {"label": "GPT-5.6", "provider": "OpenAI"},
    "databricks-gpt-5-4": {"label": "GPT-5.4", "provider": "OpenAI"},
    "databricks-gpt-5-4-mini": {"label": "GPT-5.4 Mini", "provider": "OpenAI"},
    "databricks-gemini-2-5-pro": {"label": "Gemini 2.5 Pro", "provider": "Google"},
    "databricks-gemini-2-5-flash": {"label": "Gemini 2.5 Flash", "provider": "Google"},
}


# ---------------------------------------------------------------------------
# Foundation Model API streaming (customer's own model serving / AI Gateway)
# ---------------------------------------------------------------------------
_llm_session: Optional[aiohttp.ClientSession] = None

# Newer Anthropic models no longer expose sampling knobs: temperature/top_p/top_k
# are REMOVED and the FM API returns 400 ("Model ... does not support the
# temperature parameter") if you send one. Omit temperature for these proactively
# so the common case never wastes a round-trip; the send loop below also drops it
# and retries when *any* model returns a 400 while temperature is set.
#
# Tokens are version-specific to avoid matching an unrelated custom endpoint whose
# name merely happens to contain a family word. Anything not listed here that turns
# out to reject temperature at runtime is learned into _TEMP_UNSUPPORTED (below), so
# the doubled round-trip is paid at most once per model per process.
# (Opus 5 / 4.8 / 4.7, Sonnet 5, Fable 5 reject it; Opus/Sonnet 4.6 and older accept it.)
_NO_TEMPERATURE_TOKENS = ("opus-5", "opus-4-8", "opus-4-7", "sonnet-5", "fable-5")

# Runtime-learned: normalized names of models observed to 400 on `temperature`.
# The send loop adds a model here once it's confident temperature was the cause
# (the error names it, or dropping it made the retry succeed), so subsequent calls
# skip temperature proactively — no recurring wasted round-trip or WARNING spam.
_TEMP_UNSUPPORTED: set[str] = set()


def _normalize_model(model: str) -> str:
    return (model or "").lower().replace(".", "-")


def _supports_temperature(model: str) -> bool:
    """Whether the given serving-endpoint model accepts a `temperature` param.

    Consults the static version denylist AND the runtime-learned set, matching on
    the dot-normalized, lowercased name so both the endpoint name
    (``databricks-claude-opus-5``) and the underlying model id
    (``us.anthropic.claude-opus-5``) are covered.
    """
    m = _normalize_model(model)
    if m in _TEMP_UNSUPPORTED:
        return False
    return not any(tok in m for tok in _NO_TEMPERATURE_TOKENS)


# No total timeout: a streamed completion legitimately runs for minutes. Bound the
# connect and per-read stalls instead, so a hung upstream cannot pin a worker and
# its connection open indefinitely (CWE-400).
_LLM_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=120)


async def _get_llm_session() -> aiohttp.ClientSession:
    global _llm_session
    if _llm_session is None or _llm_session.closed:
        _llm_session = aiohttp.ClientSession(timeout=_LLM_TIMEOUT)
    return _llm_session


async def _stream_from_fmapi(
    messages: list,
    model: Optional[str] = None,
    max_tokens: int = 1500,
    temperature: float = 0.3,
):
    """Core SSE streaming generator for the Databricks Foundation Model API.

    Uses the per-request _ai_model context var if no model is explicitly passed.
    Emits Server-Sent Events: `data: {"content": "..."}` chunks then `data: [DONE]`.
    """
    from server.config import get_workspace_host, get_auth_headers

    if model is None:
        model = _ai_model.get()

    host = get_workspace_host()
    auth_headers = get_auth_headers()
    url = f"{host}/serving-endpoints/{model}/invocations"

    # Shared long-lived session (_get_llm_session): set the product tag per
    # request here, not as a session default, so it reflects the calling tab.
    headers = with_ua({**auth_headers, "Content-Type": "application/json"})
    payload = {
        "messages": messages,
        "max_tokens": max_tokens,
        "stream": True,
    }
    # Only send temperature to models that accept it (newer Anthropic models 400 on it).
    if _supports_temperature(model):
        payload["temperature"] = temperature

    normalized_model = _normalize_model(model)
    retried_without_temp = False

    try:
        session = await _get_llm_session()
        # At most two attempts: if a model returns a 400 while temperature is set,
        # drop temperature and retry once. This self-heals for any model that
        # rejects the param but isn't in the static denylist (finding-driven:
        # rejections can be worded differently, so we don't gate on the error text).
        for attempt in range(2):
            async with session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    if attempt == 0 and response.status == 400 and "temperature" in payload:
                        # If the error explicitly blames temperature we're certain,
                        # so learn it now; otherwise learn only if the retry succeeds
                        # (below), to avoid mislabeling a model over an unrelated 400.
                        if "temperature" in error_text.lower():
                            _TEMP_UNSUPPORTED.add(normalized_model)
                        logger.warning(
                            f"Model {model} returned 400 with temperature set; "
                            f"retrying without it"
                        )
                        payload.pop("temperature", None)
                        retried_without_temp = True
                        continue
                    logger.error(f"LLM API error ({response.status}): {error_text[:200]}")
                    yield f"data: {json.dumps({'error': f'LLM API error: {response.status}'})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                # Dropping temperature fixed a prior 400 → this model rejects it;
                # remember so future calls skip temperature proactively.
                if retried_without_temp:
                    _TEMP_UNSUPPORTED.add(normalized_model)

                async for line in response.content:
                    decoded = line.decode("utf-8").strip()
                    if not decoded or not decoded.startswith("data: "):
                        continue
                    data_str = decoded[6:]
                    if data_str == "[DONE]":
                        yield "data: [DONE]\n\n"
                        return
                    try:
                        chunk = json.loads(data_str)
                        choices = chunk.get("choices", [])
                        if not choices:
                            continue
                        content = choices[0].get("delta", {}).get("content", "")
                        if content:
                            yield f"data: {json.dumps({'content': content})}\n\n"
                    except (json.JSONDecodeError, IndexError, KeyError):
                        continue
                yield "data: [DONE]\n\n"
                return
    except Exception as e:
        # The exception text can carry the request payload or upstream response
        # body; send the client only a reference to the (redacted) server log.
        reference, message = safe_error(e, "LLM streaming", logger)
        yield f"data: {json.dumps({'error': message, 'reference': reference})}\n\n"
        yield "data: [DONE]\n\n"


async def stream_llm_chat(
    messages: list,
    model: Optional[str] = None,
    max_tokens: int = 1500,
    temperature: float = 0.3,
):
    """Stream a response from the FM API given a full message list (multi-turn)."""
    async for chunk in _stream_from_fmapi(messages, model, max_tokens, temperature):
        yield chunk


async def stream_llm_response(
    system_prompt: str,
    user_prompt: str,
    model: Optional[str] = None,
    max_tokens: int = 1500,
    temperature: float = 0.3,
):
    """Stream a response from the FM API given a system + single user prompt."""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    async for chunk in _stream_from_fmapi(messages, model, max_tokens, temperature):
        yield chunk


def _derive_label_and_provider(model_id: str) -> tuple[str, str]:
    """Derive a reasonable label and provider from a model endpoint name.

    If the model_id is in AI_MODELS, use the curated values.
    Otherwise, infer from the model name:
      - Label: strip leading 'databricks-', replace '-' with spaces, title-case
      - Provider: infer from model name (claude→Anthropic, gpt→OpenAI, etc.)
    """
    if model_id in AI_MODELS:
        info = AI_MODELS[model_id]
        return info["label"], info["provider"]

    # Infer provider from model name substring
    provider = "Databricks"
    if "claude" in model_id:
        provider = "Anthropic"
    elif "gpt" in model_id:
        provider = "OpenAI"
    elif "gemini" in model_id or "gemma" in model_id:
        provider = "Google"
    elif "llama" in model_id:
        provider = "Meta"
    elif "qwen" in model_id:
        provider = "Alibaba"

    # Derive label: strip leading 'databricks-', replace '-' with spaces, title-case
    label = model_id
    if label.startswith("databricks-"):
        label = label[11:]
    label = label.replace("-", " ").title()

    return label, provider


# Model-family classification for the grouped picker: (family, open_source),
# matched by keyword on the (lowercased) endpoint id. Proprietary vs open-source
# is a property of the family, not the provider (Google ships both Gemini
# [proprietary] and Gemma [open], and OpenAI ships both GPT [proprietary] and
# gpt-oss [open]). Order matters — the more specific open-weight tokens must
# come before their proprietary parent: gpt-oss before gpt, gemma before gemini.
_FAMILY_RULES = (
    ("claude", ("Claude", False)),
    ("gpt-oss", ("GPT", True)),   # OpenAI open-weight — must precede the "gpt" rule
    ("gpt", ("GPT", False)),
    ("gemma", ("Gemma", True)),
    ("gemini", ("Gemini", False)),
    ("llama", ("Llama", True)),
    ("qwen", ("Qwen", True)),
    ("mixtral", ("Mistral", True)),
    ("mistral", ("Mistral", True)),
    ("deepseek", ("DeepSeek", True)),
    ("phi", ("Phi", True)),
    ("dbrx", ("DBRX", True)),
)


def _classify_family(model_id: str, provider: str) -> tuple[str, bool]:
    """Return (family, open_source) for a model endpoint id. Unknown families
    fall back to the provider name and are treated as proprietary (conservative)."""
    m = (model_id or "").lower()
    for token, (family, open_source) in _FAMILY_RULES:
        if token in m:
            return family, open_source
    return (provider or "Other"), False


def _model_record(model_id: str) -> dict:
    """One model's full record for the picker: {id, label, provider, family, open_source}."""
    label, provider = _derive_label_and_provider(model_id)
    family, open_source = _classify_family(model_id, provider)
    return {"id": model_id, "label": label, "provider": provider,
            "family": family, "open_source": open_source}


async def list_available_models() -> list[dict]:
    """Fetch the list of available LLM chat models from the workspace serving endpoints.

    Queries the serving-endpoints API, filters to task == "llm/v1/chat", and
    returns a list of {id, label, provider, family, open_source} dicts (see
    _model_record) so the picker can group by licensing and family.

    Results are cached for CACHE_TTL (10 min). On ANY error, falls back to
    static AI_MODELS entries so the dropdown is never empty.
    """
    from server.config import get_workspace_host, get_auth_headers

    # Check cache first
    cached = _cache_get("serving_models")
    if cached is not None:
        return cached

    try:
        host = get_workspace_host()
        auth_headers = get_auth_headers()

        if not host or not auth_headers:
            logger.warning("Missing host or auth headers; falling back to static AI_MODELS")
            result = [_model_record(k) for k in AI_MODELS]
            _cache_set("serving_models", result)
            return result

        url = f"{host}/api/2.0/serving-endpoints"

        session = await _get_llm_session()
        # Shared long-lived session: tag per request (see _stream_from_fmapi).
        async with session.get(url, headers=with_ua(auth_headers), timeout=aiohttp.ClientTimeout(total=10)) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.warning(f"serving-endpoints API error ({response.status}): {error_text[:200]}")
                result = [_model_record(k) for k in AI_MODELS]
                _cache_set("serving_models", result)
                return result

            data = await response.json()
            endpoints = data.get("endpoints", [])

            # Filter to chat endpoints, enrich with labels and providers
            models = []
            for ep in endpoints:
                if ep.get("task") == "llm/v1/chat":
                    model_id = ep.get("name")
                    if model_id:
                        models.append(_model_record(model_id))

            if not models:
                logger.warning("No chat endpoints found in serving-endpoints API")
                result = [_model_record(k) for k in AI_MODELS]
                _cache_set("serving_models", result)
                return result

            # Sort: known/curated families first (Anthropic, OpenAI, Google), then alpha
            provider_order = {"Anthropic": 0, "OpenAI": 1, "Google": 2}
            models.sort(key=lambda m: (provider_order.get(m["provider"], 999), m["id"]))

            _cache_set("serving_models", models)
            return models

    except Exception as e:
        logger.error(f"Error fetching serving endpoints: {e}")
        result = [_model_record(k) for k in AI_MODELS]
        _cache_set("serving_models", result)
        return result


async def resolve_default_model(models: list[dict]) -> str:
    """Resolve the default model from the available list.

    Preference order:
      1. DEFAULT_LLM_MODEL if it's in the available list
      2. First available model whose id contains "claude-sonnet"
      3. First available model id
      4. DEFAULT_LLM_MODEL (ultimate fallback)
    """
    if not models:
        return DEFAULT_LLM_MODEL

    # Check if DEFAULT_LLM_MODEL is available
    available_ids = {m["id"] for m in models}
    if DEFAULT_LLM_MODEL in available_ids:
        return DEFAULT_LLM_MODEL

    # Try to find a claude-sonnet model
    for model in models:
        if "claude-sonnet" in model["id"]:
            return model["id"]

    # Fall back to first available model
    if models:
        return models[0]["id"]

    # Ultimate fallback
    return DEFAULT_LLM_MODEL


async def is_available_model(model_id: str) -> bool:
    """Whether a model id is one the workspace actually serves.

    Validates against the DYNAMIC serving-endpoints list (cached), NOT the
    static AI_MODELS label map — the picker lists every live chat endpoint, so
    the static map is not the source of truth for what's selectable. Degrades
    open: if the list can't be resolved, accept the id rather than silently
    forcing the default (the FM API call itself will surface a real error if
    the endpoint truly doesn't exist).
    """
    if not model_id:
        return False
    try:
        models = await list_available_models()
        ids = {m["id"] for m in models}
        return model_id in ids if ids else True
    except Exception:
        return True
