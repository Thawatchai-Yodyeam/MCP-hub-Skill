---
name: security-audit
category: code-review
description: Application security code review checklist covering OWASP Top 10, sanitization, secrets, and auth.
tags: [security, code-review, owasp, auth, vulnerabilities]
---

# Security Code Review Audit Checklist

Checklist and guidelines for reviewing application code changes against critical security vulnerabilities.

## Checklist
- [ ] Injection defense: All database queries must use parameterized queries/prepared statements (never string formatting).
- [ ] Authentication & Authorization: Verify RBAC/ABAC on every API endpoint before processing mutations.
- [ ] Secret leaks: Check that no API keys, private keys, or passwords exist in source files or commit history.
- [ ] Input sanitization: Sanitize and validate all user-controlled data before rendering (XSS protection) or processing.
- [ ] Dependency safety: Run `pip audit`, `npm audit`, or equivalent security scanners to identify CVEs in dependencies.
- [ ] CORS & Headers: Ensure strict `Content-Security-Policy`, `X-Frame-Options: DENY`, and explicit allowed origins.

## Anti-patterns
- Trusting client-supplied IDs without verifying ownership (IDOR vulnerability).
- Logging sensitive PII, session tokens, or passwords to stdout/log files.
