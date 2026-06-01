import structlog
from config import WEBHOOK_CLIENT_SECRET
from fastapi import APIRouter, Request, Response

router = APIRouter()
logger = structlog.get_logger("pulsar.webhooks")


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
        body = await request.json()
    except Exception as exc:
        logger.warning("Invalid webhook payload", error=str(exc))
        return Response(status_code=400)

    notifications = body.get("value", [])
    if not isinstance(notifications, list):
        logger.warning("Invalid webhook payload structure")
        return Response(status_code=400)

    for notification in notifications:
        client_state = notification.get("clientState")
        if WEBHOOK_CLIENT_SECRET and client_state != WEBHOOK_CLIENT_SECRET:
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
