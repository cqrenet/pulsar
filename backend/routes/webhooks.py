import hmac
import json

import structlog
from config import settings
from fastapi import APIRouter, Request, Response

router = APIRouter()
logger = structlog.get_logger("pulsar.webhooks")

MAX_WEBHOOK_BODY_BYTES = 256 * 1024
MAX_WEBHOOK_NOTIFICATIONS = 100


async def _read_body_limited(request: Request) -> bytes:
    """Read a webhook body without allowing an unbounded request into memory."""
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > MAX_WEBHOOK_BODY_BYTES:
                raise ValueError("body too large")
        except ValueError as exc:
            if str(exc) == "body too large":
                raise
            raise ValueError("invalid content length") from exc

    chunks = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_WEBHOOK_BODY_BYTES:
            raise ValueError("body too large")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/webhooks/graph")
async def graph_webhook(request: Request):
    """
    Receive Microsoft Graph change notifications.
    Handles the validation handshake by echoing validationToken.
    Validates clientState on notifications to prevent spoofing.
    """
    validation_token = request.query_params.get("validationToken")
    if validation_token:
        # Microsoft sends validationToken as a query param during subscription creation.
        # Echo it back as plain text to prove endpoint ownership.
        # Validate to prevent content injection if endpoint is hit directly.
        if len(validation_token) > 1024 or not validation_token.isascii():
            logger.warning("Invalid validationToken rejected", length=len(validation_token))
            return Response(status_code=400)
        return Response(
            content=validation_token,
            media_type="text/plain",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    try:
        body = json.loads(await _read_body_limited(request))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.warning("Invalid webhook payload", error_type=type(exc).__name__)
        return Response(status_code=413 if str(exc) == "body too large" else 400)

    if not isinstance(body, dict):
        logger.warning("Invalid webhook payload structure")
        return Response(status_code=400)
    notifications = body.get("value", [])
    if not isinstance(notifications, list) or len(notifications) > MAX_WEBHOOK_NOTIFICATIONS:
        logger.warning("Invalid webhook payload structure")
        return Response(status_code=400)

    for notification in notifications:
        if not isinstance(notification, dict):
            logger.warning("Invalid webhook notification structure")
            return Response(status_code=400)
        client_state = str(notification.get("clientState") or "")
        # Fail closed: without a configured secret there is no way to tell Graph from a spoofer.
        if not settings.WEBHOOK_CLIENT_SECRET or not hmac.compare_digest(
            client_state.encode(), settings.WEBHOOK_CLIENT_SECRET.encode()
        ):
            logger.warning(
                "Graph webhook rejected: invalid clientState",
                change_type=notification.get("changeType"),
                resource=notification.get("resource"),
            )
            return Response(status_code=401)

        logger.info(
            "Received Graph notification",
            change_type=notification.get("changeType"),
            resource=notification.get("resource"),
        )

    return {"status": "accepted"}
