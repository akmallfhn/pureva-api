# Pureva API

Single backend untuk **Pureva**: menerima webhook WhatsApp Cloud API langsung dari Meta dan
mencatatnya ke **Postgres multitenant** di Supabase.

Dibangun dengan **FastAPI + SQLAlchemy (async) + asyncpg**.

DDL referensinya ada di `docs/db/pureva.sql`; SQLAlchemy tidak pernah menggenerate schema.
Tabel yang dipakai: `tenants`, `wa_conversations`, `wa_chats`.

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
    session.py                  # engine async + session (normalisasi URL libpq -> asyncpg)
  shared/
    security.py                 # verifikasi signature Meta
    http.py                     # httpx client seumur hidup app
    storage.py                  # upload attachment ke Supabase Storage
  modules/
    health/routes.py            # /health, /health/db
    tenant/                     # entity + repository `tenants`
    whatsapp/                   # entity, repository, service, routes, meta_client
    stat/                       # endpoint agregat read-only untuk dashboard
    knowledge/                  # chatbot internal: CRUD thread + SSE + antrean job
```

Menambah module baru: bikin folder di `app/modules/`, lalu daftarkan di `create_app()`.

## Endpoint

| Method | Path | Dipanggil oleh | Auth |
|---|---|---|---|
| `GET` | `/health` | siapa saja | - |
| `GET` | `/health/db` | monitoring | - |
| `GET` | `/api/v1/webhook/whatsapp/callback` | Meta (verifikasi webhook) | `hub.verify_token` |
| `POST` | `/api/v1/webhook/whatsapp/callback` | Meta (event pesan/status) | `X-Hub-Signature-256` |
| `POST` | `/api/v1/stats/*` | dashboard TRC | `Bearer CLIENT_SECRET` |
| `POST` | `/api/v1/knowledge/conversations*` | dashboard TRC (halaman Knowledge) | `Bearer CLIENT_SECRET` |
| `POST` | `/api/v1/knowledge/chat/stream` | dashboard TRC (halaman Knowledge) | `Bearer CLIENT_SECRET` |

## Knowledge (chatbot internal)

Tanya-jawab tentang kondisi brand deal di atas data yang sama dengan dashboard.
Detail endpoint-nya di [docs/api/knowledge.md](docs/api/knowledge.md).

```
Dashboard TRC
     │  POST /api/v1/knowledge/chat/stream
     ▼
[routes]     simpan pertanyaan, titipkan job ke antrean, buka SSE
     ▼
[queue]      worker terbatas — panggilan LLM punya rate limit, antrean penuh ditolak 429
     ▼
[graph]      plan ──▶ retrieve ──┐   (maksimal 3 ronde)
             ▲                   │
             └───────────────────┘
                       ▼
                    answer  ──▶ token di-stream ke SSE, teks utuh disimpan ke kb_chats
```

**Retrieval-nya bukan vector search.** Pertanyaan internal hampir selalu berbentuk
agregat ("median first response bulan ini berapa"), dan potongan teks hasil similarity
tidak bisa dipakai menghitung median — angkanya harus datang dari Postgres. Jadi jalur
utamanya tool yang membungkus `StatService`, yaitu endpoint `stats/*` yang sama persis
dengan yang dibaca dashboard, ditambah pencarian leksikal ke `wa_conversations`/`wa_chats`
untuk pertanyaan kualitatif yang menyebut nama brand atau kata di dalam pesan. Konsekuensi
yang disengaja: angka di chat dan angka di dashboard tidak akan pernah berbeda.

Planner dan penjawab dipisah. Planner (`gpt-4.1-mini`) terikat ke tool dan boleh berputar;
penjawab (`gpt-4.1`) tidak terikat tool sama sekali dan hanya membaca hasil retrieval,
supaya token jawaban tidak pernah terpakai memanggil tool dan jawabannya bisa di-stream
utuh dari token pertama.

Empat metrik sengaja ditolak, bukan diestimasi: leakage (Rp), lost reason, cycle time
inbound → closed, dan konversi antar stage sebagai deret waktu. Semuanya belum punya
sumber data di schema percakapan, dan menebaknya lebih berbahaya daripada bilang tidak
tahu.

### Batasan antrean

Antrean hidup di memori satu proses. Job yang sedang jalan hilang kalau server restart —
barisnya ditandai `failed` saat startup berikutnya, jadi tidak ada jawaban yang menggantung
selamanya di UI — dan tidak menyebar ke replika kedua. Untuk itu perlu broker di luar
proses (Redis atau sejenisnya). Untuk satu instance Railway dengan lalu lintas internal,
ini cukup.


## Setup

> Modul Knowledge butuh dua tabel baru (`kb_conversations`, `kb_chats`).
> Jalankan [docs/db/knowledge.sql](docs/db/knowledge.sql) sekali di Supabase sebelum
> memakai endpoint `/api/v1/knowledge/*`; endpoint lain tidak terpengaruh.

```bash
# 1. Install deps (pakai uv)
uv sync

# 2. Konfigurasi environment
cp .env.example .env
# wajib: DATABASE_URL, META_APP_SECRET, META_WEBHOOK_VERIFY_TOKEN
# untuk attachment: SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY
# untuk stats + knowledge: CLIENT_SECRET; untuk knowledge: OPENAI_API_KEY

# 3. Jalankan server
uv run dev          # http://localhost:$APP_PORT  (reload)
# atau: uv run start
```

> Tanpa `uv`: `pip install -e .` lalu `uvicorn app.main:app --reload`.

Port mengikuti pola yang sama dengan `ordina`: `PORT` (di-inject Railway/PaaS saat runtime)
dengan fallback `APP_PORT` untuk lokal.

`DATABASE_URL` memakai connection string Postgres biasa — param libpq
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
  yang dibaca UI dashboard.

## Known Gaps

- **RLS mati di semua tabel** Postgres-nya. Siapa pun dengan
  anon key bisa baca/tulis `tenants` — termasuk kolom `wa_access_token`. Perlu pass tersendiri;
  mengaktifkan RLS tanpa policy akan mengunci app sendiri.
- **Antrean chat Knowledge ada di memori satu proses**, jadi tidak selamat dari restart
  dan tidak menyebar ke replika kedua. Lihat bagian Knowledge di atas.
- Belum ada test suite otomatis. Verifikasi perubahan dengan `uv run ruff check app` plus
  request manual ke server yang jalan.
