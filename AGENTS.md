# AGENTS.md

Instructions for coding agents (Claude Code, Codex, or others) working in this repo. Read this before making changes.

## What this is

Pureva API is the single Python backend for Pureva, a multitenant WhatsApp brand-deal platform. It does three things: (1) receives Meta WhatsApp Cloud API webhooks — inbound customer messages, echoes of outbound messages sent from the WhatsApp Business App (coexistence), and delivery status updates — and persists them per tenant, uploading media attachments to Supabase Storage; (2) serves read-only aggregate endpoints under `/api/v1/stats` for the 360° brand-deal evaluation dashboard; (3) runs LangGraph agents that read those conversations and write structured fields back. Tenant routing is by `wa_phone_number_id`: the WhatsApp number an event arrives on decides which tenant owns it.

Postgres via Supabase, shared with the `pureva-ai` Next.js app. Deployed on Railway.

## Stack

Python 3.12+, FastAPI, SQLAlchemy 2 async (`asyncpg`), Pydantic v2 + pydantic-settings, httpx. LangGraph + LangChain (`langchain-openai`) for agents. `uv` for dependencies, `ruff` for lint and format.

## Running locally

1. Copy `.env.example` to `.env` and fill in `DATABASE_URL`, the `META_*` and `SUPABASE_*` values, `CLIENT_SECRET`, and `OPENAI_API_KEY`.
2. `uv run dev` — reload server on `:$PORT` (or `$APP_PORT` locally). `uv run start` for the non-reload variant; Railway uses the `Procfile`.
3. No automated test suite exists yet. Verify changes with `uv run ruff check .`, `uv run ruff format --check .`, booting the app (`create_app()` must construct), and manual requests against a running server.

## Project structure

`app/modules/<module>/{entity,repository,service,schema,routes}.py` — each module owns its own layers. Cross-cutting helpers live in `app/shared`. `app/server.py` is the single DI/wiring point — every repository, service, and router is constructed there via a `build_*` factory and nowhere else.

| Module | Owns |
|---|---|
| `whatsapp` | Meta webhook: signature check, inbound messages, outbound echoes, delivery statuses, media upload to Supabase Storage |
| `stat` | Read-only aggregate endpoints for the dashboard — volume, response time, heatmap, lead funnel, unanswered, brand deals |
| `tenant` | Tenant lookup by id and by `wa_phone_number_id` |
| `agents` | LangGraph automation agents — see `docs/agents/README.md` |
| `health` | Liveness and database reachability |
| `shared` | Response envelope, `ApiError`, Bearer auth, pagination, shared httpx client, Meta signature verification, Supabase Storage client |
| `core`, `db` | Settings and the lazy async engine/session factory |

## Conventions — follow these exactly, they're load-bearing

- **Wiring lives only in `app/server.py`.** Modules expose `register_<module>_routes(router, build_service)`; the factory that constructs the service is passed in. Never instantiate a repository or service inside a route handler.
- **Response envelope:** always `success(code, message, data)` / `error(code, message)` from `app.shared.response`. Never return a bare dict or `JSONResponse` from a client-facing endpoint. The webhook routes are the one exception — Meta dictates their response shape (empty `200`, plain-text challenge on verification).
- **Errors:** raise `ApiError(code, message)` from the service layer; `api_error_handler` renders it in the same envelope. Request validation failures are normalized to `400` with `invalid request: <field>` by `validation_error_handler` — don't let the default 422 through.
- **Stats endpoints are `POST` with a JSON body**, authenticated with a static `Bearer $CLIENT_SECRET`. No path params, no query params.
- **Every list endpoint paginates through `app.shared.pagination`** (`normalize`, `offset`, `meta`) — never reimplement the clamping math. Response body is `{"list": [...], "metapaging": {"total_data", "total_page", "current_page", "page_size"}}`. Default page `1`, size `20`, capped at `100`.
- **Stat queries are raw SQL via `text()`, read-only, and always scoped by `tenant_id`.** Aggregation belongs in Postgres, not in Python — the repository returns rows, the service only assembles derived numbers and formats dates.
- **Date ranges are inclusive and timezone-aware.** `end_date` maps to the start of the following day in the requested IANA zone. Endpoints returning a per-day or per-stage series must zero-fill empty buckets so charts don't break.
- **The webhook must answer Meta fast.** Verify the signature, then hand the payload to a background task; Meta retries anything slow. Persistence commits per message so one bad message can't drop the rest of the batch.
- **Enums are owned by Prisma, not by this repo.** Every `ENUM(...)` in an entity is declared `create_type=False`. `updated_at` is maintained by the ORM layer, not by database triggers — this database has no triggers.
- **Comments:** one line, no multi-line comment blocks. If it needs more than one line, it needs a shorter explanation instead. Comments and docs are Indonesian; identifiers, enum values, column names, and API fields are English.
- **Formatting:** `ruff` with line length 100, double quotes, and `E`/`F`/`I` lint rules. Run `uv run ruff format .` before committing.
- **Module API docs:** every module with client-facing endpoints has a `docs/api/<module>.md` — one intro paragraph, then per endpoint: one-sentence description, `**Method:**`/`**Authorization:**` lines, request and response as JSON code blocks (request also gets a Field/Type/Required table; response doesn't), and an errors table. No base_url explanation, no curl examples.

## Database

**This repo does not own the schema.** The source of truth is `prisma/schema.prisma` in the `pureva-ai` repo. A schema change has to land in **three** places or things drift:

1. `prisma/schema.prisma` in `pureva-ai`, then `prisma generate`.
2. The live Supabase project, via the `apply_migration` MCP tool.
3. `docs/db/pureva.sql` in this repo — a hand-maintained reference DDL in the same format as `ordina-ddl.sql`. It is documentation, not a migration runner.

The SQLAlchemy entities here are a read-write mirror of that schema, which is why their enums use `create_type=False`. Note that `id` defaults depend on a `nanoid()` function existing in the database.

Two Postgres details that have already cost time: enums cannot drop labels, so changing one means renaming the old type, creating the new one, migrating the column with a `USING` clause, and dropping the old type. And in SQLAlchemy `text()`, cast with `CAST(:param AS enum_type)`, never `:param::enum_type` — the `::` form is parsed as a bind parameter and fails at runtime.

## Agents

LangGraph automation agents live in `app/modules/agents/<agent_name>/`, sharing `app/modules/agents/llm.py`. Their rules — one env var, measured token ceilings, write guards expressed in SQL rather than Python, and failing without taking down the flow that triggered them — are in `docs/agents/README.md`. Read that before adding one.

## Known gaps

- **RLS is disabled on every table.** Supabase's Data API exposes all tables unrestricted to anyone with the anon/service key. Don't enable RLS without policies — it would lock out this app's own connection too. Needs a deliberate pass.
- **App INFO logs arrive tagged `error` on Railway.** Python logging writes to stderr by default and the platform classifies stderr as error severity, so successful agent runs look like failures in the log stream. Cosmetic, but it buries real errors.
- **`pureva-ai`'s Prisma schema currently drifts from the live database.** The funnel stages in `wa_lead_status_enum`, the `brand_name`/`project_value` columns, and the `human` default on `mode` were applied to Postgres from this repo and have not been mirrored back into Prisma yet.
- No automated tests. No CI config in this repo.
