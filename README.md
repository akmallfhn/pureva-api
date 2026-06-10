# Pureva WhatsApp Agent

WhatsApp AI agent untuk **Klinik Pureva Skin & Beauty**, dibangun dengan **LangGraph + FastAPI (Python)**.
Mengikuti pola arsitektur agent `wa-sales` di `sevenpreneur-agents`, tapi domainnya klinik kecantikan.

Knowledge base (treatment, jadwal dokter, promo) berasal dari CSV yang
**di-seed ke SQLite** (di-"hardcode" sesuai permintaan), lalu di-query oleh node-node graph.

## Arsitektur Graph

```
WhatsApp User
     │
     ▼
[1] Context Fetcher Node ──────── inject Shared State (treatment, jadwal dokter, promo)
     │
     ▼
[2] Intent Classifier Node (GPT-4o) ── skin_consult | booking | complaint | general_info
     │
     ├── skin_consult ─▶ [3a] Skin Assessment & Recommendation (GPT-4.5, deep reasoning)
     ├── booking ──────▶ [3b] Booking Node (GPT-4o, structured output + cek jadwal + buat booking)
     ├── complaint ────▶ [3c] Complaint Node (GPT-4.5, empati + analisis + eskalasi)
     └── general_info ─▶ [3d] General Info Node (GPT-4o)
     │
     ▼
[4] Memory Node (Shared State Update, NO LLM) ── simpan histori percakapan + refresh konteks sesi
     │
     ▼
[7] Send Message Node (GPT-4o, tone-adjusted) ── format WhatsApp (maks 1600 char) + kirim
     │
     ▼
Respons ke User (via WhatsApp)
```

Mapping model bisa diganti lewat `.env` (`MODEL_FAST`, `MODEL_REASONING`).

## Struktur Proyek

```
app/
  main.py                     # FastAPI app (auto-seed SQLite saat startup)
  scripts.py                  # entrypoint dev/start
  core/
    config.py                 # Settings (.env)
    auth.py                   # verifikasi Bearer token webhook
  db/
    database.py               # koneksi SQLite + schema + parse harga
    seed.py                   # loader CSV -> SQLite (idempotent)
    data/                     # sumber CSV (knowledge base)
      product_aesthetic.csv
      product_skin_health.csv
      practice_schedule.csv
      practice_discount.csv
  agents/
    base/llm.py               # factory LLM (OpenAI / Anthropic)
    pureva/
      graph.py                # 7 node LangGraph
      prompts.py              # prompt tiap node
      services.py             # query SQLite + kirim WhatsApp
      utils.py                # format context & routing
  api/webhooks/whatsapp.py    # endpoint webhook WhatsApp
```

## Setup

```bash
# 1. Install deps (pakai uv)
uv sync

# 2. Konfigurasi environment
cp .env.example .env
# isi OPENAI_API_KEY dan AGENT_SECRET_KEY

# 3. Seed database SQLite dari CSV
uv run seed

# 4. Jalankan server
uv run dev          # http://localhost:8000  (reload)
# atau: uv run start
```

> Tanpa `uv`: `pip install -e .` lalu `python -m app.db.seed` dan `python -m app.scripts` (atau `uvicorn app.main:app --reload`).

## Mengetes Agent

Kirim pesan masuk ke webhook (header `Authorization: Bearer <AGENT_SECRET_KEY>`):

```bash
curl -X POST http://localhost:8000/api/v1/webhook/whatsapp/message \
  -H "Authorization: Bearer <AGENT_SECRET_KEY>" \
  -H "Content-Type: application/json" \
  -d '{
    "conv_id": "demo-1",
    "phone": "6281200000001",
    "name": "Sarah",
    "message": "kak jerawatku lagi parah banget, ada treatment yang cocok?"
  }'
```

`phone` dipakai untuk tujuan pengiriman WhatsApp dan pencatatan booking. `conv_id`
adalah kunci sesi percakapan (histori disimpan per `conv_id`).

Kalau `WHATSAPP_ACCESS_TOKEN` kosong, balasan **tidak benar-benar dikirim** ke WhatsApp —
agent jalan mode **dry-run** dan menulis pesan ke log (`[DRY-RUN WA -> ...]`). Cocok untuk demo skripsi.

Contoh pesan untuk tiap intent:
- `skin_consult`: "ada flek hitam di pipi, mau treatment buat mencerahkan"
- `booking`: "mau booking dong, dokter yang praktik hari sabtu siapa?"
- `complaint`: "habis laser kemarin kulitku merah dan perih, gimana ya?"
- `general_info`: "promo hari ini apa aja?"

## Data Knowledge Base

Data CSV adalah **single source of truth**. Untuk memperbarui katalog/jadwal/promo,
edit file di `app/db/data/`, hapus `pureva.sqlite3`, lalu `uv run seed` lagi.
Kalau punya CSV katalog asli yang lebih lengkap, timpa `product_aesthetic.csv`
(format kolom: `product,description,category,price_start_from`) dan seed ulang.

## Catatan Desain

- **Context Fetcher** sengaja deterministik (query SQLite, bukan LLM) supaya cepat & hemat.
- **Booking Node** memakai structured output untuk ekstraksi, lalu validasi jadwal dilakukan
  secara deterministik terhadap data dokter, baru LLM menyusun kalimat balasan.
- **Complaint Node** menandai urgensi (`[URGENSI: TINGGI/NORMAL]`) untuk memicu eskalasi.
- **Memory Node** tidak memakai LLM: menyimpan histori percakapan per sesi
  (mirip peran Redis/vector store di diagram, di sini pakai tabel SQLite `conversations`).
