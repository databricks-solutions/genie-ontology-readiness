"""Genie Conversation API client."""

import aiohttp
import asyncio
import hashlib
import logging
from server.config import (
    GENIE_ALLOW_SP_FALLBACK,
    GENIE_SPACE_ID,
    get_auth_headers,
    get_user_token,
    get_workspace_host,
)

logger = logging.getLogger(__name__)

# Genie questions are free text typed against the customer's own warehouse. In a
# regulated deployment (this app ships to insurers) a question can name a member,
# a claim or a diagnosis, so the text itself is treated as sensitive: log a stable
# digest for correlation and its length, never the content (CWE-532; HIPAA
# 164.312(b) requires audit records, not a copy of the data).
_QUESTION_DIGEST_LEN = 12


def _question_ref(content: str) -> str:
    """A short, stable, non-reversible reference for one question."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:_QUESTION_DIGEST_LEN]


# Bound every Genie REST call. Without a timeout a stalled upstream holds the
# connection and the worker slot open indefinitely (CWE-400).
_GENIE_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=60)


#: Shown to the caller when the Genie test cannot run as them. Fixed text — it
#: carries no upstream detail, so it is safe to return verbatim.
GENIE_IDENTITY_REQUIRED = (
    "The Genie test runs on-behalf-of you, and no user token was forwarded. Ask a workspace "
    "admin to enable user authorization for this app (and grant it a Genie API scope). "
    "Running the test as the app service principal would answer with the service "
    "principal's data access rather than yours."
)


class GenieIdentityUnavailable(Exception):
    """The viewer's own token cannot call the Genie API and SP fallback is off."""


def _genie_auth_headers() -> tuple[dict, str]:
    """Auth headers for a Genie Conversation API call, plus the identity used.

    A Genie answer carries ROWS from the customer's warehouse back to the caller,
    so the identity this runs as decides what that caller is allowed to see. It
    used to be hard-wired to the app service principal, which holds SELECT on every
    assessed catalog — so any viewer could read any table through it (CWE-269).

    On-behalf-of the viewer is the only identity that makes the answer reflect the
    caller's own grants, so it is preferred. Falling back to the service principal
    re-opens the escalation, so it happens only when a deployment has explicitly
    accepted that (``GENIE_ALLOW_SP_FALLBACK``).
    """
    if get_user_token():
        return get_auth_headers(), "obo"
    if GENIE_ALLOW_SP_FALLBACK:
        logger.warning(
            "Genie call running as the app service principal — the answer reflects the SP's "
            "grants, not the viewer's (GENIE_ALLOW_SP_FALLBACK is on)"
        )
        return get_auth_headers(force_sp=True), "service_principal"
    raise GenieIdentityUnavailable(GENIE_IDENTITY_REQUIRED)


async def start_conversation(content: str) -> dict:
    """Start a new Genie conversation.

    POST /api/2.0/genie/spaces/{space_id}/start-conversation
    Then poll for result.
    """
    host = get_workspace_host()
    auth_headers, identity = _genie_auth_headers()

    if not host:
        raise Exception("DATABRICKS_HOST not configured")
    if not auth_headers:
        raise Exception("No authentication headers available")

    url = f"{host}/api/2.0/genie/spaces/{GENIE_SPACE_ID}/start-conversation"
    headers = {**auth_headers, "Content-Type": "application/json"}

    payload = {"content": content}

    logger.info("Starting Genie conversation: question=%s len=%d",
                _question_ref(content), len(content or ""))

    async with aiohttp.ClientSession(timeout=_GENIE_TIMEOUT) as session:
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"Genie start-conversation error ({response.status}): {error_text[:2000]}")
                raise Exception(f"Genie API error ({response.status})")

            result = await response.json()

        conversation_id = result.get("conversation_id")
        message_id = result.get("message_id")

        if not conversation_id or not message_id:
            raise Exception("Genie API response was missing conversation_id or message_id")

        logger.info(f"Genie conversation started: conv={conversation_id}, msg={message_id}")

        # Poll until the message is completed
        message_result = await _poll_message(
            session, host, auth_headers, conversation_id, message_id
        )

        return {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "ran_as": identity,
            "result": _extract_result(message_result),
        }


async def send_message(conversation_id: str, content: str) -> dict:
    """Send a follow-up message in an existing Genie conversation.

    POST /api/2.0/genie/spaces/{space_id}/conversations/{conv_id}/messages
    Then poll for result.
    """
    host = get_workspace_host()
    auth_headers, identity = _genie_auth_headers()

    if not host:
        raise Exception("DATABRICKS_HOST not configured")
    if not auth_headers:
        raise Exception("No authentication headers available")

    url = (
        f"{host}/api/2.0/genie/spaces/{GENIE_SPACE_ID}"
        f"/conversations/{conversation_id}/messages"
    )
    headers = {**auth_headers, "Content-Type": "application/json"}

    payload = {"content": content}

    logger.info("Sending Genie message in conv=%s: question=%s len=%d",
                conversation_id, _question_ref(content), len(content or ""))

    async with aiohttp.ClientSession(timeout=_GENIE_TIMEOUT) as session:
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"Genie send-message error ({response.status}): {error_text[:2000]}")
                raise Exception(f"Genie API error ({response.status})")

            result = await response.json()

        message_id = result.get("id") or result.get("message_id")

        if not message_id:
            raise Exception("Genie API response was missing message_id")

        logger.info(f"Genie message sent: msg={message_id}")

        # Poll until the message is completed
        message_result = await _poll_message(
            session, host, auth_headers, conversation_id, message_id
        )

        return {
            "message_id": message_id,
            "ran_as": identity,
            "result": _extract_result(message_result),
        }


async def _poll_message(
    session: aiohttp.ClientSession,
    host: str,
    auth_headers: dict,
    conversation_id: str,
    message_id: str,
    timeout_seconds: int = 90,
) -> dict:
    """Poll a Genie message until status is COMPLETED or FAILED."""
    url = (
        f"{host}/api/2.0/genie/spaces/{GENIE_SPACE_ID}"
        f"/conversations/{conversation_id}/messages/{message_id}"
    )

    poll_interval = 2
    elapsed = 0

    while elapsed < timeout_seconds:
        await asyncio.sleep(poll_interval)
        elapsed += poll_interval

        async with session.get(url, headers=auth_headers) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.warning(f"Genie poll error ({resp.status}): {error_text[:2000]}")
                # Keep polling on transient errors
                continue

            result = await resp.json()
            status = result.get("status", "")

            logger.info(f"Genie poll: msg={message_id}, status={status}, elapsed={elapsed}s")

            if status == "COMPLETED":
                # Check if there's a query attachment that needs result fetching
                result = await _fetch_query_results_if_needed(
                    session, host, auth_headers, conversation_id, message_id, result
                )
                return result
            elif status in ("FAILED", "CANCELLED"):
                logger.error("Genie query failed: %s", result.get("error", "unknown error"))
                raise Exception("The Genie query failed.")
            # Otherwise keep polling (SUBMITTED, IN_PROGRESS, EXECUTING_QUERY, etc.)

    raise Exception(f"Genie message timed out after {timeout_seconds} seconds")


async def _fetch_query_results_if_needed(
    session: aiohttp.ClientSession,
    host: str,
    auth_headers: dict,
    conversation_id: str,
    message_id: str,
    message: dict,
) -> dict:
    """If the message has a query attachment, fetch results by attachment_id."""
    attachments = message.get("attachments", [])

    for attachment in attachments:
        if "query" in attachment:
            attachment_id = attachment.get("id") or attachment.get("attachment_id")
            if not attachment_id:
                logger.warning("Query attachment has no attachment_id, cannot fetch results")
                continue

            logger.info(f"Fetching query result for attachment_id={attachment_id}")

            # Use the get_message_query_result_by_attachment endpoint
            result_url = (
                f"{host}/api/2.0/genie/spaces/{GENIE_SPACE_ID}"
                f"/conversations/{conversation_id}"
                f"/messages/{message_id}"
                f"/query-result/{attachment_id}"
            )
            try:
                async with session.get(result_url, headers=auth_headers) as resp:
                    if resp.status == 200:
                        result_data = await resp.json()
                        # The response has a statement_response with manifest and result
                        statement_response = result_data.get("statement_response", {})
                        manifest = statement_response.get("manifest", {})
                        schema = manifest.get("schema", {})
                        columns = schema.get("columns", [])
                        result_obj = statement_response.get("result", {})
                        data_array = result_obj.get("data_array", [])

                        logger.info(
                            f"Fetched query result by attachment: "
                            f"{len(columns)} columns, {len(data_array)} rows"
                        )

                        # Build result in the format _extract_result expects
                        attachment["query"]["result"] = {
                            "columns": [{"name": c.get("name", f"col_{i}")} for i, c in enumerate(columns)],
                            "data_array": data_array,
                        }
                    else:
                        error_text = await resp.text()
                        logger.warning(
                            f"Failed to fetch query result by attachment ({resp.status}): {error_text[:2000]}"
                        )
            except Exception as e:
                logger.warning(f"Error fetching query result by attachment: {e}")

    return message


def _extract_result(message: dict) -> dict:
    """Extract structured result from a Genie message response."""
    attachments = message.get("attachments", [])
    status = message.get("status", "UNKNOWN")

    result: dict = {
        "status": status,
        "text": None,
        "sql": None,
        "description": None,
        "columns": [],
        "rows": [],
    }

    for attachment in attachments:
        # Text attachment
        if "text" in attachment:
            text_content = attachment["text"]
            if isinstance(text_content, dict):
                result["text"] = text_content.get("content", "")
            else:
                result["text"] = str(text_content)

        # Query attachment
        if "query" in attachment:
            query_obj = attachment["query"]
            if isinstance(query_obj, dict):
                result["sql"] = query_obj.get("query", "")
                result["description"] = query_obj.get("description", "")
                # The query result may have columns and data
                query_result = query_obj.get("result")
                if query_result:
                    columns = query_result.get("columns", [])
                    result["columns"] = [
                        c.get("name", f"col_{i}") for i, c in enumerate(columns)
                    ]
                    data_array = query_result.get("data_array", [])
                    # Convert to list of dicts
                    rows = []
                    for row_data in data_array:
                        row = {}
                        for i, col in enumerate(result["columns"]):
                            row[col] = row_data[i] if i < len(row_data) else None
                        rows.append(row)
                    result["rows"] = rows[:100]  # Cap at 100 rows for the UI

    # If we got nothing useful, create a generic response
    if not result["text"] and not result["sql"] and not result["description"]:
        result["text"] = "I processed your request but have no specific output to display."

    return result
