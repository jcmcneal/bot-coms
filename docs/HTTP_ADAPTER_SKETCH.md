# HTTP adapter sketch (post-MVP)

**Do not implement a server in MVP.** Gate: start HTTP only after filesystem smoke S1–S12 is green.

## Intent

The same library `Transport` protocol (`send` / `claim` / `ack` / `nack` / `status`) should grow an HTTP mapping. The filesystem spool remains the source of truth for local/dev; HTTP is a transport swap behind the same API.

## Suggested mapping (non-normative)

| Method | Route | Semantics |
|---|---|---|
| `POST` | `/v1/peers/{to}/inbox` | enqueue (body = envelope) |
| `POST` | `/v1/peers/{peer}/claim` | exclusive claim |
| `POST` | `/v1/peers/{peer}/messages/{id}/ack` | ack + optional result |
| `POST` | `/v1/peers/{peer}/messages/{id}/nack` | nack / poison |
| `GET` | `/v1/peers/{peer}/messages/{id}` | status |
| `GET` | `/v1/peers/{peer}/results/{correlation_id}` | poll result |

The HTTP server should persist using the FS spool primitives (atomic write, claim rename, leases). It must not invent a second source of truth.

## Deferred

Auth, TLS, mTLS, multi-host routing, and encryption are later decisions. Do not ship an open bind-all listener as a “demo”.
