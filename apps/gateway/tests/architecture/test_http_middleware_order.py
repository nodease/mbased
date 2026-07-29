from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from apps.gateway.main import app
from apps.gateway.middleware.csrf import CsrfProtectionMiddleware
from apps.gateway.middleware.public_conversation_cors import (
    PublicConversationCorsBoundaryMiddleware,
)
from apps.gateway.middleware.webhook_query_redaction import (
    WebhookQueryRedactionMiddleware,
)


def test_security_middleware_order_keeps_cors_outside_csrf_denials():
    middleware_classes = [entry.cls for entry in app.user_middleware]

    assert middleware_classes.index(WebhookQueryRedactionMiddleware) < (
        middleware_classes.index(PublicConversationCorsBoundaryMiddleware)
    )
    assert middleware_classes.index(PublicConversationCorsBoundaryMiddleware) < (
        middleware_classes.index(CORSMiddleware)
    )
    assert middleware_classes.index(CORSMiddleware) < middleware_classes.index(
        CsrfProtectionMiddleware
    )
    assert middleware_classes.index(CsrfProtectionMiddleware) < (
        middleware_classes.index(SessionMiddleware)
    )
