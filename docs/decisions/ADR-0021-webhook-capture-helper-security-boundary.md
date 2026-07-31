# ADR-0021: Webhook capture helper security boundary

Status: Accepted
Related ADRs: ADR-0010, ADR-0018, ADR-0041

Partial supersession note: Public trigger credential transport and ingress payload limits are governed by [ADR-0041](ADR-0041-public-webhook-ingress-security-boundary.md). The historical query-token statement below is no longer the current public trigger contract. This ADR continues to govern capture helper authentication, retention, and preview redaction.

## Context

Webhook trigger execution is an intentional public surface. Before ADR-0040, it accepted an app secret through a query token, Bearer header, or `X-Webhook-Secret`. The current public trigger contract accepts exactly one header credential and rejects query-token transport.

The capture helper is different. `GET /api/v1/hooks/{url_slug}/capture/start` and `GET /api/v1/hooks/{url_slug}/capture/status` are interactive debugging tools used by workflow builders to collect a sample webhook payload for test input. If those helper endpoints are unauthenticated or return raw payload, a user who knows a URL slug could start capture and read sensitive webhook data from a legitimate sender.

## Options Considered

1. Keep capture helper unauthenticated.
   - Simple, but exposes payload capture to anyone who knows the slug.
2. Allow app secret authentication for capture helper.
   - Reuses the public trigger credential, but broadens a bearer secret from execution-only use into an administrative read capability.
3. Require user session and target workflow deploy permission.
   - Treats capture as a builder/deployer debugging action and keeps app secret limited to public trigger execution.

## Decision

Webhook capture start/status/cancel requires an authenticated user session and target workflow `deploy` permission. App secret authentication alone cannot start, read, or cancel capture sessions.

Capture sessions use a server-issued `capture_id` nonce and a short TTL. Status polling and cancellation must provide the nonce and must be performed by the same user that started the session. Captured sessions are deleted after the captured status is read once. Cancelled sessions are deleted immediately, and later webhooks follow the normal execution path.

The server does not store raw webhook payload in `CAPTURE_SESSIONS`. It stores only a redacted/capped structured preview suitable for workflow test input. Sensitive keys and known secret-like value patterns are redacted, and nested structures are depth/item/string capped.

## Rationale

- The app secret is intended for public run/webhook execution, not for reading captured payload.
- A session user with workflow `deploy` permission is the actor most closely aligned with publishing and debugging the webhook surface.
- Nonce and TTL reduce stale-session and cross-user polling risk.
- Explicit cancellation prevents a UI-cancelled capture session from continuing to intercept webhooks.
- Redacted/capped preview preserves the workflow testing use case without turning capture into raw payload storage.

## Affected Files

- `apps/gateway/api/v1/endpoints/webhook.py`
- `apps/gateway/tests/api/test_webhook_api.py`
- `apps/client/app/features/workflow/api/webhookApi.ts`
- `apps/client/app/features/workflow/components/nodes/webhook/components/WebhookTriggerNodePanel.tsx`
- `docs/features/deployment/requirements.md`
- `docs/features/deployment/api_spec.md`
- `docs/features/deployment/test_cases.md`
- `docs/architecture.md`

## Follow-up Review Notes

- If future UX requires capture by non-deploy users, add a separate explicit permission instead of reusing app secret authentication.
- If capture samples need durable storage, use a dedicated redacted artifact model with retention and access audit. Do not persist raw webhook payload by default.
