---
name: docker-deployment
category: devops
description: Production-grade Docker containerization, multi-stage builds, rootless execution, and layer caching.
tags: [docker, devops, container, deployment, security]
---

# Docker Production Deployment & Containerization

Guide to crafting lightweight, secure, and reproducible Docker images for production.

## Checklist
- [ ] Use multi-stage builds to exclude build tools, compilers, and dev dependencies from final images.
- [ ] Pin specific base image versions (e.g., `python:3.14-slim` instead of `python:latest`).
- [ ] Order Dockerfile instructions by frequency of change (put `package.json` or `requirements.txt` before application source code to maximize cache hits).
- [ ] Run as a non-root user (`USER appuser`) to minimize container breakout risks.
- [ ] Use `.dockerignore` to exclude `.git`, `node_modules`, `venv`, test artifacts, and secrets.
- [ ] Include explicit `HEALTHCHECK` instructions where applicable.

## Anti-patterns
- Storing secrets, passwords, or API keys directly in the Dockerfile or image layers.
- Using huge OS images (like full Ubuntu) when slim/alpine images suffice.
