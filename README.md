# Pureva API

Single backend untuk **Pureva**: menerima webhook WhatsApp Cloud API langsung dari Meta dan
mencatatnya ke **Postgres multitenant** yang sama dengan app `pureva-ai` (Next.js).

Dibangun dengan **FastAPI + SQLAlchemy (async) + asyncpg**.

Schema Postgres-nya dimiliki Prisma di repo `pureva-ai` — repo ini **tidak pernah** mengeluarkan
DDL. Tabel yang dipakai: `tenants`, `wa_conversations`, `wa_chats`.

## Alur

```
Meta WhatsApp Cloud API
     │  POST /api/v1/webhook/whatsapp/callback   (signature X-Hub-Signature-256)
     ▼
[routes]   balas 200 secepatnya, proses di background task
     ▼
[service]  tenant di-resolve dari metadata.phone_number_id  ─▶  multitenant
     ├── messages            ─▶ wa_conversations (upsert) + wa_chats (inbound/user)
     │     └── media         ─▶ download dari Graph API ─▶ upload Supabase Storage ─▶ storage_url
     ├── smb_message_echoes  ─▶ wa_chats (outbound/admin)   pesan staff dari WA Business App
     └── statuses            ─▶ update sent/delivered/read/failed + timestamp-nya
```

Meta menjanjikan *at-least-once delivery* dan mengulang kirim kalau webhook tidak balas 200
dengan cepat, jadi seluruh persistensi jalan di background task. Tiap pesan di-commit
sendiri-sendiri: satu pesan gagal tidak menjatuhkan pesan lain di batch yang sama.

## Struktur Proyek

Pola `modules/<module>/{entity,repository,service,routes}` — tiap module punya layer sendiri,
dan `app/server.py` adalah **satu-satunya** tempat wiring (semua repo/service dirakit di sana).

```
app/
  main.py                       # objek FastAPI (dari server.create_app)
  server.py                     # DI/wiring + lifespan; module baru didaftarkan di sini
  scripts.py                    # entrypoint dev/start (pakai PORT / APP_PORT)
  core/config.py                # Settings (.env)
  db/
    base.py                     # DeclarativeBase entity Postgres
    session.py                  # engine async + session (normalisasi URL Prisma -> asyncpg)
  shared/
    security.py                 # verifikasi signature Meta
    http.py                     # httpx client seumur hidup app
    storage.py                  # upload attachment ke Supabase Storage
  modules/
    health/routes.py            # /health, /health/db
    tenant/                     # entity + repository `tenants`
    whatsapp/                   # entity, repository, service, routes, meta_client
```

Menambah module baru: bikin folder di `app/modules/`, lalu daftarkan di `create_app()`.

## Endpoint

| Method | Path | Dipanggil oleh | Auth |
|---|---|---|---|
| `GET` | `/health` | siapa saja | - |
| `GET` | `/health/db` | monitoring | - |
| `GET` | `/api/v1/webhook/whatsapp/callback` | Meta (verifikasi webhook) | `hub.verify_token` |
| `POST` | `/api/v1/webhook/whatsapp/callback` | Meta (event pesan/status) | `X-Hub-Signature-256` |

## Setup

```bash
# 1. Install deps (pakai uv)
uv sync

# 2. Konfigurasi environment
cp .env.example .env
# wajib: DATABASE_URL, META_APP_SECRET, META_WEBHOOK_VERIFY_TOKEN
# untuk attachment: SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY

# 3. Jalankan server
uv run dev          # http://localhost:$APP_PORT  (reload)
# atau: uv run start
```

> Tanpa `uv`: `pip install -e .` lalu `uvicorn app.main:app --reload`.

Port mengikuti pola yang sama dengan `ordina`: `PORT` (di-inject Railway/PaaS saat runtime)
dengan fallback `APP_PORT` untuk lokal.

`DATABASE_URL` boleh langsung disalin dari `pureva-ai/.env` — format Prisma
(`?schema=public`, `?pgbouncer=true`, `?sslmode=require`) dinormalisasi otomatis ke asyncpg.
Kalau pakai connection pooler Supabase (port 6543), prepared statement cache dimatikan sendiri.

Cek koneksi database: `curl http://localhost:8000/health/db`.

## Menghubungkan Webhook Meta

Di Meta App Dashboard → WhatsApp → Configuration, set:

- **Callback URL**: `https://<host>/api/v1/webhook/whatsapp/callback`
- **Verify token**: sama dengan `META_WEBHOOK_VERIFY_TOKEN`
- **Webhook fields**: `messages` (dan `smb_message_echoes` kalau pakai coexistence)

Tenant di-routing lewat `tenants.wa_phone_number_id`, jadi tiap klinik cukup didaftarkan
nomornya di tabel `tenants` — tidak ada konfigurasi per-tenant di repo ini. Download media
memakai `tenants.wa_access_token` milik tenant yang bersangkutan.

Kalau `META_APP_SECRET` kosong, verifikasi signature **dilewati** (hanya untuk dev lokal).

## Catatan Desain

- **Tenant di-resolve dari `metadata.phone_number_id`**, bukan dari config — satu deployment
  melayani semua klinik.
- **Conversation di-upsert**, bersandar pada unique constraint `(tenant_id, phone_number)`.
  Meta bisa mengirim beberapa event untuk kontak baru yang sama secara bersamaan, jadi
  cek-lalu-insert tidak aman. Nama profil di-refresh, kecuali event-nya memang tidak membawa
  nama (echo & status) — supaya nama yang sudah ada tidak tertimpa string kosong.
- **Attachment gagal disimpan tidak membatalkan pesannya**: `storage_url` sekadar tidak ikut
  disisipkan, teks/metadata pesannya tetap masuk.
- **`created_at` diambil dari timestamp Meta**, bukan waktu server, supaya urutan chat di UI
  mengikuti waktu kirim sebenarnya.
- Bucket dan layout path Storage (`<slug>/<type>s/<ts>_<media_id>.<ext>`) sengaja sama dengan
  yang dibaca UI `pureva-ai`.

## Known Gaps

- **RLS mati di semua tabel** Postgres-nya (isu lintas repo, bukan dari sini). Siapa pun dengan
  anon key bisa baca/tulis `tenants` — termasuk kolom `wa_access_token`. Perlu pass tersendiri;
  mengaktifkan RLS tanpa policy akan mengunci app sendiri.
- Belum ada test suite otomatis. Verifikasi perubahan dengan `uv run ruff check app` plus
  request manual ke server yang jalan.
