---
name: api-design
category: web-development
description: RESTful API design best practices, idempotency, HTTP status codes, and pagination.
tags: [api, rest, backend, json, http]
---

# API Design Guidelines & Best Practices

This skill outlines guidelines for designing robust, predictable RESTful APIs.

## Checklist
- [ ] Use nouns instead of verbs in URI paths (e.g., `/users` instead of `/getUsers`).
- [ ] Use standard HTTP methods (`GET`, `POST`, `PUT`, `PATCH`, `DELETE`).
- [ ] Return appropriate status codes: `200` (OK), `201` (Created), `400` (Bad Request), `401` (Unauthorized), `404` (Not Found), `500` (Internal Server Error).
- [ ] Implement pagination for list endpoints (`limit`, `offset` or cursor-based `next_cursor`).
- [ ] Include clear error bodies with `code`, `message`, and optional `details`.
- [ ] Ensure `PUT` and `DELETE` operations are idempotent.

## Common Anti-patterns
- Returning `200 OK` with `{ "error": true, "message": "Failed" }`. Always use 4xx/5xx HTTP codes.
- Deeply nested URLs (e.g., `/users/12/orders/45/items/6/history`). Keep depth to at most 2 levels.
- Missing rate-limiting headers on public APIs.
