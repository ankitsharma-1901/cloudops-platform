
# CloudOps — AI-Powered DevOps Platform

A self-service cloud infrastructure management
platform built with DevOps, cloud automation,
Kubernetes, CI/CD, and AI technologies.

## Project Objective

Build a platform where authorized users can:

- Request, create, and manage cloud servers
- Automate infrastructure using Terraform
- Configure servers using Ansible
- Deploy applications using Docker and Kubernetes
- Automate builds and deployments using CI/CD
- Monitor infrastructure health and performance
- Use AI to assist with infrastructure planning
  and troubleshooting

## Planned Technology Stack

**Cloud:** AWS  
**Infrastructure:** Terraform, Ansible  
**Backend:** Python, FastAPI  
**Frontend:** React, Next.js, TypeScript  
**Containers:** Docker, Kubernetes  
**CI/CD:** GitHub Actions, Jenkins, Argo CD  
**Monitoring:** Prometheus, Grafana, Loki  
**AI:** LLM APIs with structured outputs

## Current Status

Local MVP development: validated server requests, SQLite persistence, and a
Create Server frontend are implemented.

Day 4: A local Next.js frontend creates server requests through FastAPI.
The backend validates configurations and saves queued requests using SQLite
and SQLModel. Provisioning, authentication, and AI features remain planned.

No cloud resources have been deployed.

## Local Backend

The backend lives in `cloudops-platform/backend/`.
From `D:\Devops\PythonLearn\cloudops-platform`, run these PowerShell commands:

```powershell
# Install dependencies into the existing virtual environment.
# This setting avoids the optional PyYAML C build on this Python setup.
$env:PYYAML_FORCE_LIBYAML = "0"
& ".\backend\.venv\Scripts\python.exe" -m pip install -r .\backend\requirements.txt

# Run tests.
Push-Location .\backend
& ".\.venv\Scripts\python.exe" -m pytest tests
Pop-Location

# Start the local development server.
& ".\backend\.venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir .\backend --reload
```

Open http://127.0.0.1:8000/docs to try the API:

- `GET /health` returns `{"status":"healthy"}`.
- `POST /api/v1/resources/validate` returns HTTP 200 with
  `{"status":"validated","configuration":{...}}` for valid server settings.
- Invalid requests return HTTP 422 with field-level validation errors.
- `POST /api/v1/resources` saves the same configuration and returns HTTP 202
  with the six submitted fields, a generated UUID `id`, `status: "queued"`,
  and a UTC `created_at` timestamp.
- `GET /api/v1/resources` returns an array of saved requests (or `[]`).
- `GET /api/v1/resources/{id}` returns a saved request, or HTTP 404 if missing.
- Duplicate server names within the same environment return HTTP 409.
  The same name can be used in a different environment.

Example request:

```json
{
  "name": "web-dev-01",
  "environment": "development",
  "region": "ca-central-1",
  "instance_type": "t3.micro",
  "install_nginx": true,
  "install_docker": true
}
```

Names must contain 3-32 lowercase letters, digits, or hyphens, start with a
letter, and end with a letter or digit. Allowed environments are `development`
and `staging`; regions are `ca-central-1` and `us-east-1`; instance types are
`t3.micro` and `t3.small`. Both install flags are required JSON booleans.
Extra fields are rejected. Validation does not create or save resources.

### Local database

The save endpoint commits a request to `backend/cloudops.db`. The path is
independent of the terminal's current folder. Tables are created on application
startup. The database and SQLite journal files are excluded from Git.

To use another database file, set `DATABASE_URL` before starting the server:

```powershell
$env:DATABASE_URL = "sqlite:///D:/Devops/PythonLearn/cloudops-platform/backend/another.db"
```

Tests pass temporary database URLs to `create_app()` and never use the local
development database. Each API request receives a separate database session.
`queued` means the request was saved; no background worker or provisioning runs.
This is a local development API; authentication is not implemented yet.

## Local Frontend (Day 4)

`frontend/` is a Next.js App Router application using TypeScript and Tailwind CSS.
Use a current Node.js LTS installation with npm. In a separate PowerShell terminal:

```powershell
Set-Location "D:\Devops\PythonLearn\cloudops-platform\frontend"
npm install
npm run dev
```

For this workspace, a portable Node.js runtime is also available. If `npm`
is not recognized, run this in the same terminal before the commands above:

```powershell
$env:PATH = "D:\Devops\PythonLearn\cloudops-platform\.tools\node-v26.13.1-win-x64;" + $env:PATH
```

Keep the FastAPI server running on port 8000, then open http://localhost:3000.
The form sends the six configuration fields to `POST /api/v1/resources` and
shows the saved UUID and queued status. It displays field validation errors,
duplicate-name conflicts, connection failures, and a disabled saving state.
The summary updates as you change the form. No infrastructure is provisioned.

The API URL defaults to `http://127.0.0.1:8000`. To change it, copy
`frontend/.env.example` to `frontend/.env.local`, edit `NEXT_PUBLIC_API_URL`,
and restart Next.js. This variable is public; do not put secrets in it.

FastAPI CORS permits exactly `http://localhost:3000` and
`http://127.0.0.1:3000`, GET/POST, and the Content-Type header. Credentials
are not enabled. Other origins and ports need an explicit backend change.
CORS controls browser access; it is not authentication.

Run frontend checks from `frontend/`:

```powershell
npm test
npm run typecheck
npm run build
```

Component tests mock HTTP responses; backend tests use temporary databases.

## Documentation links

- [Project Vision](docs/PROJECT-VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [AI Roadmap](docs/AI-ROADMAP.md)
- [Development Progress](docs/PROGRESS.md)

## Development Approach

This project is being developed incrementally,
with documented engineering decisions, tests,
daily progress notes, and regular GitHub commits.

The current target is a **three-month core MVP**. The core scope and milestones
will be refined separately from the longer-term platform vision. I will
personally write more application code, with Codex acting as a tutor and code
reviewer to support understanding, debugging, and maintainable changes.

For the implementation review and recorded verification limits, see the
[November 12, 2026 progress entry](docs/progress/2026-11-12.md).

## Security

No secrets or cloud credentials are stored
in this repository.
