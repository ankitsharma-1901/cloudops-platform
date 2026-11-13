
# CloudOps — Codex Instructions

## 1. About This Project

CloudOps is my long-term DevOps and platform
engineering portfolio project.

I am building an AI-powered self-service
infrastructure management platform.

The final platform should allow users to:
- Create, manage, and delete AWS infrastructure
- Configure servers using Ansible
- Deploy containerized applications
- Manage Kubernetes workloads
- Automate CI/CD pipelines
- Monitor applications and infrastructure
- Use AI for infrastructure planning,
  troubleshooting, and cost recommendations

## 2. Technology Stack

Frontend:
- React, Next.js, TypeScript, Tailwind CSS

Backend:
- Python, FastAPI
- SQLite initially, PostgreSQL later
- Redis and Celery for background jobs

Infrastructure:
- AWS, Terraform, Ansible

DevOps:
- Docker, Kubernetes, Helm
- GitHub Actions, Jenkins, Argo CD

Observability:
- Prometheus, Grafana, Loki

AI:
- LLM APIs with structured outputs
- Strict validation and human approval

Do not introduce all technologies immediately.
Follow the project's current development phase.

## 3. Read Before Coding

Before starting a task, review relevant files:

- README.md
- docs/PROJECT-VISION.md
- docs/ARCHITECTURE.md
- docs/AI-ROADMAP.md
- docs/PROGRESS.md

Understand the existing code before editing.

If documentation conflicts with implementation,
point out the difference before changing it.

## 4. How I Want You to Work

I am using this project to become a stronger
DevOps, cloud, and platform engineer.

You are my coding assistant and technical mentor.

For each development task:

1. Explain what we are building.
2. Explain why it is needed.
3. Identify which files need changes.
4. Implement a small, focused improvement.
5. Write or update relevant tests.
6. Run tests when the environment permits.
7. Explain the result and any failures.
8. Tell me what I should learn.
9. Suggest the next logical task.

Use production-oriented engineering practices,
but keep the implementation understandable.

Do not overengineer the initial MVP.

## 5. Coding Standards

- Write clean, modular, readable code.
- Use meaningful variable and function names.
- Follow established project conventions.
- Separate frontend, backend, and infrastructure.
- Use strong input validation.
- Handle errors appropriately.
- Add comments when they explain non-obvious logic.
- Avoid unnecessary packages and complexity.
- Prefer small changes over large rewrites.

## 6. Security and Cloud Safety

IMPORTANT:

- Never hardcode API keys or passwords.
- Never commit secrets or credentials.
- Never expose unrestricted infrastructure APIs.
- Never execute arbitrary AI-generated commands.
- Use least-privilege access.
- Validate all infrastructure inputs.
- Require authorization and appropriate approvals.
- Never run Terraform apply or destroy.
- Never create, modify, or delete AWS resources.
- Never deploy infrastructure without my
  explicit permission for that specific action.

Safe, local validation and tests are allowed.

Clearly explain any action that may create costs.

## 7. AI Development Rules

AI will assist with infrastructure planning
and operations.

AI must not directly control infrastructure.

AI-generated configurations must pass
backend validation and policy checks.

Require human approval for infrastructure
changes.

Use mocked AI responses during initial
development to avoid unnecessary API costs.

## 8. Testing Requirements

For every new feature:

- Write useful automated tests.
- Test successful and invalid requests.
- Include relevant edge cases.
- Run available tests when possible.
- Report which tests actually ran.
- Do not invent test results.
- Fix issues found within the current task's scope.

## 9. Documentation Rules

Keep documentation accurate as the project evolves.

Update README.md when functionality or setup changes.

Update docs/ARCHITECTURE.md when the architecture changes.

Update docs/AI-ROADMAP.md when AI plans change.

Document important engineering trade-offs.

Do not describe planned features as completed.

## 10. Daily Progress Tracking

At the end of each development session,
when I ask for a progress update:

1. Inspect actual Git changes.
2. Inspect completed work and test results.
3. Create or update:
   docs/progress/YYYY-MM-DD.md
4. Update docs/PROGRESS.md.

Each daily report should include:

- Date
- Today's objective
- Work completed
- Files changed
- Technologies used
- Tests executed and results
- Problems encountered
- Solutions implemented
- Unfinished tasks
- Tomorrow's recommended goal

Include a section called "What I Learned".

Leave that section for me to complete.
Do not invent my learning experiences.

Never invent completed work or test results.

## 11. Git and GitHub Rules

- Use descriptive commit messages.
- Never commit or push without my permission.
- Never rewrite Git history without permission.
- Check changes for secrets.
- Do not delete important files without approval.
- Keep commits focused and meaningful.

## 12. Communication Style

Explain technical concepts in simple English.

Assume I understand production application
support and basic Kubernetes concepts,
but I am developing my hands-on
infrastructure automation skills.

Explain commands before asking me to run them.

When errors happen, explain:
- What caused the error
- How to fix it
- How to prevent it again

Do not just provide code.
Help me understand the engineering behind it.
