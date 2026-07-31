import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from apps.gateway.auth.dependencies import get_current_user
from apps.gateway.services.notification_service import (
    NOTIFICATION_EVENT_CHANGED,
    NotificationService,
    notification_channel,
)
from apps.shared.db.models.user import User
from apps.shared.db.session import get_db
from apps.shared.pubsub import get_async_redis_client
from apps.shared.schemas.notification import NotificationListResponse

router = APIRouter()

SSE_NO_BUFFER_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@router.get("", response_model=NotificationListResponse)
def list_notifications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return NotificationListResponse(
        items=NotificationService.list_notifications(db, current_user.id)
    )


@router.get("/stream")
async def stream_notifications(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    async def event_generator():
        client = get_async_redis_client()
        pubsub = client.pubsub()
        channel = notification_channel(current_user.id)
        try:
            await pubsub.subscribe(channel)
            while True:
                if await request.is_disconnected():
                    break

                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=15.0,
                )
                if message is None:
                    yield ": heartbeat\n\n"
                    continue

                event = json.loads(message["data"])
                event_type = event.get("type") or NOTIFICATION_EVENT_CHANGED
                yield f"event: {event_type}\ndata: {{}}\n\n"
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers=SSE_NO_BUFFER_HEADERS,
    )
