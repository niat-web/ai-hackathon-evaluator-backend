# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

AI Hackathon Evaluator Backend is a FastAPI service whose core feature is **AI-assisted video evaluation**: a participant uploads their hackathon submission video, and the backend uses Vertex AI **Gemini** (multimodal) to watch the video and produce a structured evaluation (per-criterion scores, strengths, improvements, and an overall verdict). Videos are stored in Google Cloud Storage and session metadata in Firestore. Firebase provides authentication and the Firestore database.

The **login / authentication system is reused verbatim** from the NxtCreate backend: Firebase Auth via the Identity Toolkit REST API for login, `id_token` verification for protected routes, Firestore-backed roles, and a startup seeder.

## Commands

```bash
# Install (editable, with dev tools)
pip install -e ".[dev]"

# Run locally with hot reload (serves on :8000; also `python -m app.main`)
uvicorn app.main:app --reload

# Run all tests / a single test
pytest
pytest path/to/test_file.py::test_name

# Format, lint, type-check
black .            # line-length 100
flake8 app
mypy app

# Docker
docker-compose up                 # production-style service on :8000
docker-compose --profile dev up   # hot-reload dev service
```

Note: `pytest`/`pytest-asyncio` are configured as dev dependencies but there is no `tests/` directory yet.

## Architecture

**Current system design:** [docs/architecture.md](docs/architecture.md) (layers, Firestore collections, evaluation jobs, rounds/teams/leaderboard). Do not follow the old `/upload-video` / `evaluation_sessions` prototype.

Layered, with a strict dependency direction: **routes → services → `FirebaseService`**. Models are Pydantic schemas; middleware supplies auth dependencies.

- **`app/main.py`** — app factory, CORS allow-list, global `AppError` / `ValueError`→400 / `Exception`→500 handlers, and a `lifespan` that builds `AppContainer` and optionally runs `DatabaseSeeder`.
- **`app/routes/`** — `auth`, `admin`, `hackathon`, `teams`, `submissions`, `theme`, `evaluation_requirement`, `evaluation_prompt`, `metric_scoring`, `settings`, `internal_jobs`.
- **`app/services/`** — business logic. `FirebaseService` is the only thing that touches Firestore/Firebase Auth directly; all other services depend on it.
- **`app/models/`** — Pydantic request/response schemas.
- **`app/middleware/auth_middleware.py`** — FastAPI dependencies, not ASGI middleware.
- **`app/dependencies.py`** — process-scoped `AppContainer` injected via `Depends`.

### Firebase access pattern

`FirebaseService` (`app/services/firebase.py`) is a **singleton** (`__new__` + `_initialized` guard) that initializes the Firebase Admin SDK from env vars (credentials assembled from `FIREBASE_*` env vars; `FIREBASE_PRIVATE_KEY` has its `\n` escapes un-escaped at runtime). It exposes generic Firestore helpers (`set/get/update/delete_document`, `get_collection`, `query_collection`, `batch_write`) and Auth helpers. **Never import `firebase_admin` directly in routes/services — go through `FirebaseService`.** Firestore is schemaless; collection names (`users`, `hackathons`, `submissions`, …) are conventions. Full list: [docs/architecture.md](docs/architecture.md).

### Authentication & authorization

- Login (`POST /auth/login`) does **not** use the Admin SDK to mint tokens. It calls the Firebase Identity Toolkit REST API (`signInWithPassword`) with `FIREBASE_WEB_API_KEY`, then sets an HttpOnly `access_token` cookie (Bearer still works for Swagger).
- Protected routes depend on `get_current_user`, which verifies the cookie/Bearer JWT (shape + project `aud`, then Admin SDK) and loads `users/{uid}` to attach `role`. A token valid in Firebase but with **no Firestore user doc → 404**.
- Admin-only routes depend on `get_admin_user` (`role == "admin"`). Roles live on the Firestore user document, not Firebase custom claims. Evaluators also need `approval_status == approved`.

### Video evaluation flow (async, polling-based)

Live path is **submissions**, not `evaluation_sessions`:

1. Student `POST /submissions/upload-url` then `POST /submissions/from-upload` (or legacy multipart `POST /submissions`). One submission per student/team per round.
2. Admin assigns an evaluator. `POST /submissions/{id}/evaluate` returns **202** and schedules Gemini via **Cloud Tasks** in production (`POST /internal/jobs/evaluate-submission`) or FastAPI `BackgroundTasks` locally.
3. The SPA **polls** `GET /submissions/{id}` (`status`: uploaded → processing → completed/failed). Report/score are visible to students only after admin approval.

Gemini receives the GCS `gs://` URI (no re-download). GCS layout: `submissions/{student_id}/{submission_id}/video.<ext>`.

### Startup seeding

`DatabaseSeeder` (`app/utils/seeder.py`) runs in lifespan when `SEED_ON_STARTUP` is true and idempotently ensures default admin / evaluator / student accounts. Failures are logged and **do not** abort startup. Extend this seeder; do not add standalone seed scripts.

## Configuration

All config is env-driven (`.env` locally, Cloud Run secrets/env in prod — see `.env.example`). Key vars:
- **Auth/Firestore:** `FIREBASE_PROJECT_ID`, `FIREBASE_PRIVATE_KEY`, `FIREBASE_CLIENT_EMAIL`, `FIREBASE_WEB_API_KEY` (required for login).
- **Evaluation:** `GOOGLE_CLOUD_PROJECT` (falls back to `FIREBASE_PROJECT_ID`), `GOOGLE_CLOUD_LOCATION`, `EVALUATION_BUCKET_NAME` (falls back to `VIDEO_BUCKET_NAME`), `GEMINI_MODEL` (default `gemini-3.5-flash`).

## Deployment

`cloudbuild.yaml` builds the image to Artifact Registry (`asia-south1`), ensures the `gs://$PROJECT_ID-hackathon-evaluations` bucket exists, and deploys to **Cloud Run** (`asia-south1`, `--allow-unauthenticated`, 1Gi/1cpu, 3600s timeout). Firebase secrets come from Secret Manager; non-secret config via `--set-env-vars`. The `Dockerfile` listens on `8080` (Cloud Run convention); local/compose use `8000`.
