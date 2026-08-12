# AI Hackathon Evaluator Backend

FastAPI backend for **HackNIAT** — students submit hackathon demo videos (record in-browser or upload from disk), admins assign **approved evaluators**, evaluators run **Gemini** AI analysis and submit scores for admin approval, and students see the final report/score only after approval.

Stack: **Firebase Auth + Firestore**, **Google Cloud Storage** (videos), **Vertex AI / Gemini** (multimodal video analysis), deployable to **Cloud Run**.

## Quick start

```bash
# 1. Create/activate a virtual environment (Python 3.11+)
python -m venv .venv && source .venv/bin/activate   # macOS/Linux
# Windows Git Bash: source .venv/Scripts/activate

# 2. Install dependencies
pip install -e ".[dev]"

# 3. Configure environment
cp .env.example .env           # then fill in the values

# 4. Run
uvicorn app.main:app --reload  # http://localhost:8000/docs
```

## Roles

| Role | Access |
|------|--------|
| **student** | Register team, submit video + requirement answers, view own submissions; see report/`final_score` only after admin approval |
| **evaluator** | Must be `@nxtwave.co.in` and **approved** by admin; works only on **assigned** submissions; can run AI analysis and submit for review |
| **admin** | Manage hackathons/themes/requirements/scoring, assign evaluators, approve evaluations, publish results |

Seeded users (created on startup, password `12345678`):

| Email | Role | Approval |
|-------|------|----------|
| `admin@nxtwave.co.in` | admin | — |
| `evaluator@nxtwave.co.in` | evaluator | approved |
| `evaluator.pending@nxtwave.co.in` | evaluator | pending |
| `student@nxtwave.co.in` | student | approved |

## Authentication

Login sets an HttpOnly `access_token` cookie (not returned in the JSON body). Use `credentials: "include"` (fetch) or `withCredentials: true` (axios).

```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -c cookies.txt \
  -d '{"email":"student@nxtwave.co.in","password":"12345678"}'
```

- `POST /auth/logout` — clear cookie  
- `GET /auth/me` — current profile (`role`, `approval_status`, …)  
- `POST /auth/change-password` — change password  
- Bearer `Authorization` header still works for Swagger / API clients  

Pending evaluators can call `/auth/me` but other app routes return `403` until approved.

### Registration

**Student** — `POST /auth/register/student`  
**Evaluator** — `POST /auth/register/evaluator` (`@nxtwave.co.in`, starts as `pending`)

## End-to-end flows

### 1. Student submission (record **or** local upload → GCS)

Prefer the **signed-URL** path. Cloud Run rejects multipart bodies over ~**32 MiB** with `413 Content Too Large`.

```text
GET  /submissions/accepted-video-types   → MIME/ext/max size for UI
POST /submissions/upload-url             → signed GCS PUT URL
Browser PUT  → storage.googleapis.com    → video lands in GCS
POST /submissions/from-upload            → create Firestore submission
```

Both **in-browser recording** and **local file upload** use the same APIs. Optional `video_source`: `"recorded"` | `"uploaded"`.

GCS object layout: `submissions/{student_id}/{submission_id}/video.{ext}`

**GCS bucket CORS** must allow the frontend origin (required for browser PUT). Deploy via Cloud Build applies CORS; for local `http://localhost:5173` you may need to set it once on the bucket (see [GCS CORS](#gcs-cors-for-direct-uploads)).

Legacy multipart (small files only): `POST /submissions` with form fields + `video` file.

Students **do not** start AI analysis. After submit they wait until an evaluator finishes and an admin approves.

### 2. Admin: assign evaluators

```text
GET  /admin/evaluators?approval_status=approved
POST /submissions/{id}/assign                         { "evaluator_id": "..." }
POST /submissions/admin/hackathons/{id}/assign-equally
     { "submission_ids": ["..."], "evaluator_ids": ["..."]? }   # random equal split
```

### 3. Evaluator: analyze → submit for review

```text
GET  /submissions/evaluator/hackathons
GET  /submissions/evaluator/hackathons/{hackathon_id}   # assigned only
POST /submissions/{id}/evaluate                         # AI analysis (202)
POST /submissions/{id}/submit-for-review
     { "final_score": 0-100, "evaluator_notes": "..."? }
```

`review_status`: `none` → `pending_review` → `approved` | `changes_requested`

### 4. Admin: approve → student sees results

```text
POST /submissions/{id}/approve-evaluation
     { "final_score": ...?, "review_notes": ...? }   # publishes report + score
POST /submissions/{id}/request-changes               # send back to evaluator
```

After approval: `report_published=true`, student can read analysis/report/`final_score`.

---

## API overview

Interactive docs (local / non-production): `http://localhost:8000/docs`

Paths below are **public absolute URLs** (same as OpenAPI when docs are enabled). Do not rename
these without an API version bump. In production, `/docs`, `/redoc`, and `/openapi.json` are
disabled by default (`ENVIRONMENT=production`); set `ENABLE_API_DOCS=true` only if needed.

### Health / root

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health` | — | Liveness |
| GET | `/` | — | Service welcome |

### Auth — `/auth`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/auth/register/student` | — | Student registration |
| POST | `/auth/register/evaluator` | — | Evaluator registration (pending) |
| POST | `/auth/login` | — | HttpOnly cookie session |
| POST | `/auth/logout` | — | Clear cookie |
| POST | `/auth/change-password` | user | Change password |
| GET | `/auth/me` | user | Current profile |

### Admin users — `/admin`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/admin/users` | Non-admin users |
| GET | `/admin/evaluators/pending` | Pending evaluators |
| GET | `/admin/evaluators` | Evaluators (`?approval_status=pending\|approved`) |
| POST | `/admin/evaluators/{id}/approve` | Approve evaluator |
| GET/PATCH | `/admin/user/{id}` | Get / update user |

### Hackathons — `/hackathons`

CRUD for hackathons (banner, themes, timeline, `hackathon_url`, linked evaluation requirements). Public list/detail for authenticated users; create/update/delete are admin.

### Themes — `/themes`

CRUD for reusable themes (admin write; list/read for app).

### Evaluation requirements — `/evaluation-requirements`

Reusable requirement field definitions used by hackathons / student forms.

### AI metric scoring — `/ai-evaluation-metric-scoring`

Per-field scoring prompts (`max_score`, natural-language scoring instructions) for Gemini.

### Submissions — `/submissions`

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/submissions/accepted-video-types` | student | Allowed MIME/ext + max size |
| POST | `/submissions/upload-url` | student | Signed PUT URL (record or file) |
| POST | `/submissions/from-upload` | student | Finalize after GCS PUT |
| POST | `/submissions` | student | Multipart create (≤ ~32 MiB on Cloud Run) |
| GET | `/submissions` | student | Own submissions |
| GET | `/submissions/admin/hackathons` | admin | Hackathons + submission counts |
| GET | `/submissions/admin/hackathons/{id}` | admin | Submissions for a hackathon |
| POST | `/submissions/admin/hackathons/{id}/assign-equally` | admin | Divide selected among evaluators |
| GET | `/submissions/admin/all` | admin | All submissions |
| GET | `/submissions/evaluator/hackathons` | evaluator | Hackathons with assigned work |
| GET | `/submissions/evaluator/hackathons/{id}` | evaluator | Assigned submissions |
| GET | `/submissions/assigned-to-me` | evaluator | Flat assigned list |
| GET | `/submissions/{id}` | owner / assignee / admin | Submission detail |
| GET | `/submissions/{id}/video` | same | Stream video (Range supported) |
| GET | `/submissions/{id}/analysis` | same* | Analysis doc (*students after publish) |
| GET | `/submissions/{id}/report` | same* | Markdown report |
| POST | `/submissions/{id}/evaluate` | admin or assignee | Start Gemini analysis |
| POST | `/submissions/{id}/submit-for-review` | assignee | Send score to admin |
| POST | `/submissions/{id}/approve-evaluation` | admin | Approve → publish to student |
| POST | `/submissions/{id}/request-changes` | admin | Send back to evaluator |
| POST | `/submissions/{id}/publish` | admin | Manual publish toggle |
| POST | `/submissions/{id}/assign` | admin | Assign / unassign evaluator |

### Internal jobs (Cloud Tasks — not for the SPA)

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/internal/jobs/evaluate-submission` | `X-Internal-Job-Secret` | Run Gemini analysis for a queued submission |

### Example: signed upload (recommended)

```bash
# Login as student first (-c / -b cookies.txt)

# 1) Prepare upload
curl -X POST http://localhost:8000/submissions/upload-url \
  -b cookies.txt -H "Content-Type: application/json" \
  -d '{"filename":"demo.webm","content_type":"video/webm","video_source":"uploaded"}'
# -> { "upload_url", "video_path", "content_type", ... }

# 2) PUT file directly to GCS (same Content-Type)
curl -X PUT "<upload_url>" \
  -H "Content-Type: video/webm" \
  --data-binary @demo.webm

# 3) Finalize
curl -X POST http://localhost:8000/submissions/from-upload \
  -b cookies.txt -H "Content-Type: application/json" \
  -d '{
        "video_path": "gs://…/video.webm",
        "content_type": "video/webm",
        "source_filename": "demo.webm",
        "video_source": "uploaded",
        "hackathon_id": "<id>",
        "theme_id": "<id>",
        "problem_statement": "…",
        "solution_description": "…"
      }'
```

---

## Production (cross-origin frontend)

As wired in `app/main.py` + `cloudbuild.yaml`: CORS with `allow_credentials=True` and explicit origins from `get_allowed_origins()` / `ALLOWED_ORIGINS`. Production deploy sets:

| Variable | Typical value |
|----------|----------------|
| `ENVIRONMENT` | `production` (disables `/docs`, `/redoc`, `/openapi.json` unless `ENABLE_API_DOCS=true`) |
| `ALLOWED_ORIGINS` | `https://hackniat.vercel.app` |
| `COOKIE_SAMESITE` | `none` (with Secure cookies) |

CORS **methods/headers** default to the SPA allow-list (`GET/POST/PATCH/DELETE/…`, `Authorization`, `Content-Type`, `X-CSRF-Token`, `Range`, …). Set `CORS_ALLOW_METHODS=*` or `CORS_ALLOW_HEADERS=*` only if you need the old wildcards.

Frontend must send `credentials: "include"`.

### GCS CORS for direct uploads

Applied in `cloudbuild.yaml` step 4 on `gs://$PROJECT_ID-hackathon-evaluations`:

```json
[
  {
    "origin": [
      "https://hackniat.vercel.app",
      "http://localhost:3000",
      "http://localhost:5173"
    ],
    "method": ["GET", "PUT", "HEAD", "OPTIONS"],
    "responseHeader": ["Content-Type", "Content-Length", "x-goog-resumable"],
    "maxAgeSeconds": 3600
  }
]
```

Manual apply (same bucket naming as Cloud Build):

```bash
gcloud storage buckets update gs://$PROJECT_ID-hackathon-evaluations --cors-file=cors.json
```

### Durable AI evaluation (Phase 2)

`POST /submissions/{id}/evaluate` still returns **202**; clients still poll `GET /submissions/{id}`.  
Production schedules work with **Cloud Tasks** → `POST /internal/jobs/evaluate-submission` (header `X-Internal-Job-Secret`).  
Locally, if Cloud Tasks env is unset, the app uses FastAPI `BackgroundTasks` (same feature, not durable across restarts).

**One-time Google Cloud setup (do this before the next Cloud Build that enables Cloud Tasks):**

1. Enable APIs:
   ```bash
   gcloud services enable cloudtasks.googleapis.com run.googleapis.com secretmanager.googleapis.com
   ```
2. Create a long random secret and store it:
   ```bash
   openssl rand -hex 32   # copy the output
   echo -n 'PASTE_SECRET_HERE' | gcloud secrets create INTERNAL_JOB_SECRET --data-file=-
   # If the secret already exists:
   # echo -n 'PASTE_SECRET_HERE' | gcloud secrets versions add INTERNAL_JOB_SECRET --data-file=-
   ```
3. Allow the Cloud Build / Cloud Run deploy SA to access that secret (same pattern as your other Firebase secrets).
4. Deploy via Cloud Build (`cloudbuild.yaml` creates queue `evaluation-jobs`, deploys with `EVALUATION_JOB_MODE=cloud_tasks`, then sets `CLOUD_TASKS_TARGET_URL` to your service URL).
5. Smoke test: call evaluate as admin/evaluator → Cloud Console → Cloud Tasks → queue `evaluation-jobs` should show a task → submission status becomes `completed` or `failed`.

---

## Architecture & deployment (source of truth)

These files define production architecture and deploy. Do not assume a different stack or pipeline.

| File | Role |
|------|------|
| [`app/main.py`](app/main.py) | FastAPI app: CORS (`allow_credentials=True` + `get_allowed_origins()`), lifespan `DatabaseSeeder`, routers, `/health`, `/`, `ValueError`→400 / `Exception`→500 |
| [`pyproject.toml`](pyproject.toml) | Package `ai-hackathon-evaluator-backend` 1.0.0, Python ≥3.11, runtime + optional `dev` deps, Black/mypy |
| [`requirements.txt`](requirements.txt) | Same runtime deps (FastAPI, uvicorn, pydantic, firebase-admin, google-cloud-storage, google-genai, dotenv, multipart, email-validator, requests) |
| [`Dockerfile`](Dockerfile) | `python:3.11-slim` → `pip install -e .` from `pyproject.toml` → copy `app/` → **uvicorn on `0.0.0.0:8080`** (no `.env` in image) |
| [`cloudbuild.yaml`](cloudbuild.yaml) | Artifact Registry → Docker build/push → GCS bucket + CORS → Cloud Run deploy |

### App surface (`app/main.py`)

Routers mounted:

- `auth`, `admin`, `submissions`, `hackathon`, `theme`, `evaluation_requirement`, `metric_scoring`, `internal_jobs`

Startup: `DatabaseSeeder.seed_all()` (failures logged; startup continues).

Local entry (`python -m` / `__main__`): uvicorn `app.main:app` on **8000** with reload.  
Container/Cloud Run: uvicorn on **8080** per Dockerfile.

### Dependencies (`pyproject.toml` / `requirements.txt`)

Runtime: FastAPI, uvicorn[standard], pydantic v2, firebase-admin, google-cloud-storage, google-cloud-tasks, google-genai, python-dotenv, python-multipart, email-validator, requests.

Dev (optional `[dev]`): pytest, pytest-asyncio, black (line-length 100), flake8, mypy.

### Production deploy (`cloudbuild.yaml` + `Dockerfile`)

1. Ensure Artifact Registry repo `ai-hackathon-evaluator-backend` in **asia-south1**  
2. Build image → `asia-south1-docker.pkg.dev/$PROJECT_ID/ai-hackathon-evaluator-backend/ai-hackathon-evaluator-backend:$SHORT_SHA` (+ `:latest`)  
3. Push `$SHORT_SHA` tag  
4. Ensure bucket `gs://$PROJECT_ID-hackathon-evaluations` (create in **us-central1** if missing) and apply CORS for `https://hackniat.vercel.app`, `http://localhost:3000`, `http://localhost:5173`  
5. Ensure Cloud Tasks queue `evaluation-jobs` in **asia-south1**  
6. Deploy Cloud Run service **`ai-hackathon-evaluator-backend`**:
   - region: **asia-south1**
   - `--allow-unauthenticated`
   - secrets: Firebase set + `INTERNAL_JOB_SECRET`
   - env: production cookie/CORS/Gemini/GCS vars + `EVALUATION_JOB_MODE=cloud_tasks`, `CLOUD_TASKS_QUEUE=evaluation-jobs`, `CLOUD_TASKS_LOCATION=asia-south1`, then `CLOUD_TASKS_TARGET_URL=<service>/internal/jobs/evaluate-submission`
   - **1Gi** memory, **1** CPU, **3600s** timeout

Build options: `E2_HIGHCPU_8`, `CLOUD_LOGGING_ONLY`, build timeout `1800s`.

Shell vars in step 4 must use `$$BUCKET` so Cloud Build does not treat them as substitutions.

```bash
gcloud builds submit --config=cloudbuild.yaml
```

## Configuration

See [.env.example](.env.example) for local development. Production secrets/env come from Cloud Run as set in `cloudbuild.yaml` (Secret Manager + `--set-env-vars`).

| Area | Variables |
|------|-----------|
| Auth / Firestore (secrets in prod) | `FIREBASE_PROJECT_ID`, `FIREBASE_PRIVATE_KEY`, `FIREBASE_CLIENT_EMAIL`, `FIREBASE_WEB_API_KEY`, `FIREBASE_DATABASE_URL` |
| Gemini + GCS (set on Cloud Run) | `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `EVALUATION_BUCKET_NAME`, `GEMINI_MODEL`, `GEMINI_ENTERPRISE` |
| App / cookies | `ENVIRONMENT`, `DEBUG`, `ALLOWED_ORIGINS`, `COOKIE_SAMESITE` |
| Optional local | `VIDEO_SIGNED_URL_EXPIRY_SECONDS`, `VIDEO_UPLOAD_URL_EXPIRY_SECONDS` |

## Project layout

```
app/
├── main.py                      # app factory, CORS, handlers, lifespan + DI container
├── dependencies.py              # FastAPI Depends providers / AppContainer
├── exceptions.py                # AppError hierarchy + ValueError status mapping
├── middleware/
│   └── auth_middleware.py       # current / active / admin / student / evaluator deps
├── models/                      # Pydantic schemas (users, submissions, hackathons, …)
├── routes/
│   ├── auth.py                  # /auth
│   ├── admin.py                 # /admin
│   ├── submissions.py           # /submissions
│   ├── hackathon.py             # /hackathons
│   ├── theme.py                 # /themes
│   ├── evaluation_requirement.py
│   ├── metric_scoring.py        # /ai-evaluation-metric-scoring
│   └── internal_jobs.py         # /internal/jobs (Cloud Tasks worker)
├── services/
│   ├── firebase.py              # Firebase Admin singleton
│   ├── user_service.py
│   ├── registration_service.py
│   ├── submission_service.py    # thin re-export of submission package
│   ├── submission/              # create / analysis / assignment / review / query mixins
│   ├── evaluation_job_service.py
│   ├── hackathon_service.py
│   ├── theme_service.py
│   ├── evaluation_requirement_service.py
│   └── metric_scoring_service.py
└── utils/
    ├── seeder.py
    ├── auth_cookies.py
    ├── cors_config.py
    ├── async_io.py              # run_sync offload helper
    ├── gcs_video.py             # signed GET/PUT + streaming
    ├── video_upload.py          # MIME allow-list (record + upload)
    └── image_upload.py          # hackathon banners
```

## Commands

```bash
# Local (matches app/main.py __main__ / typical dev)
uvicorn app.main:app --reload          # :8000

# Install (matches Dockerfile / pyproject)
pip install -e ".[dev]"

# Quality (pyproject optional-deps + tool config)
pytest           # Phase 0 characterization tests under tests/
black .          # line-length 100
flake8 app
mypy app
```

### Async note (Phase 1)

Route handlers remain `async def`, but sync Firestore / GCS / Identity Toolkit / service work is offloaded with `app.utils.async_io.run_sync` (`asyncio.to_thread`) so the event loop is not blocked. External API behaviour is unchanged.