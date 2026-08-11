"""Genie Conversation API client."""

import aiohttp
import asyncio
import logging
from server.config import get_workspace_host, get_auth_headers, GENIE_SPACE_ID

logger = logging.getLogger(__name__)


async def start_conversation(content: str) -> dict:
    """Start a new Genie conversation.

    POST /api/2.0/genie/spaces/{space_id}/start-conversation
    Then poll for result.
    """
    host = get_workspace_host()
    auth_headers = get_auth_headers(force_sp=True)  # Genie REST API via SP (OBO token lacks genie scope)

    if not host:
        raise Exception("DATABRICKS_HOST not configured")
    if not auth_headers:
        raise Exception("No authentication headers available")

    url = f"{host}/api/2.0/genie/spaces/{GENIE_SPACE_ID}/start-conversation"
    headers = {**auth_headers, "Content-Type": "application/json"}

    payload = {"content": content}

    logger.info(f"Starting Genie conversation: {content[:80]}...")

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"Genie start-conversation error ({response.status}): {error_text}")
                raise Exception(f"Genie API error ({response.status}): {error_text}")

            result = await response.json()

        conversation_id = result.get("conversation_id")
        message_id = result.get("message_id")

        if not conversation_id or not message_id:
            raise Exception(f"Missing conversation_id or message_id in response: {result}")

        logger.info(f"Genie conversation started: conv={conversation_id}, msg={message_id}")

        # Poll until the message is completed
        message_result = await _poll_message(
            session, host, auth_headers, conversation_id, message_id
        )

        return {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "result": _extract_result(message_result),
        }


async def send_message(conversation_id: str, content: str) -> dict:
    """Send a follow-up message in an existing Genie conversation.

    POST /api/2.0/genie/spaces/{space_id}/conversations/{conv_id}/messages
    Then poll for result.
    """
    host = get_workspace_host()
    auth_headers = get_auth_headers(force_sp=True)  # Genie REST API via SP (OBO token lacks genie scope)

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

    logger.info(f"Sending Genie message in conv={conversation_id}: {content[:80]}...")

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, headers=headers) as response:
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"Genie send-message error ({response.status}): {error_text}")
                raise Exception(f"Genie API error ({response.status}): {error_text}")

            result = await response.json()

        message_id = result.get("id") or result.get("message_id")

        if not message_id:
            raise Exception(f"Missing message_id in response: {result}")

        logger.info(f"Genie message sent: msg={message_id}")

        # Poll until the message is completed
        message_result = await _poll_message(
            session, host, auth_headers, conversation_id, message_id
        )

        return {
            "message_id": message_id,
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
                logger.warning(f"Genie poll error ({resp.status}): {error_text}")
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
                error_msg = result.get("error", "Unknown error")
                raise Exception(f"Genie query failed: {error_msg}")
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
                            f"Failed to fetch query result by attachment ({resp.status}): {error_text}"
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
