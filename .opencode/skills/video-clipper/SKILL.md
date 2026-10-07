---
name: video-clipper
description: Build and maintain the browser-based video clipper using React, FastAPI, FFmpeg, Nginx, and a low-resource Debian VPS.
---

# Video Clipper Project

## Stack

Frontend:
- React
- TypeScript
- Vite

Backend:
- Python
- FastAPI

Video processing:
- FFmpeg

Deployment:
- Debian
- Nginx
- systemd

## Server Constraints

Production server:
- 1 vCPU
- 1 GB RAM
- 15 GB SSD

Keep memory usage low.

Only run one FFmpeg job at a time.

## FFmpeg

Prefer stream copying:

ffmpeg -ss START -i INPUT -t DURATION -c copy OUTPUT

Avoid re-encoding unless required.

## Development Rules

Before changing code:

1. Inspect existing files.
2. Understand the current architecture.
3. Make the smallest necessary change.
4. Test the change.
5. Do not rewrite unrelated code.

## Security

Never use shell=True with user input.

Validate:
- uploaded file type
- file size
- timestamps

Use randomly generated filenames for uploads.

## Architecture

Keep FFmpeg logic separate from API routes.

Do not add unnecessary dependencies.

Do not introduce:
- Redis
- Celery
- Docker orchestration
- Kubernetes
- microservices

unless actually needed.
