# Agents

Agent automation yang jalan di atas database Pureva. Semuanya LangGraph, dipicu dari alur yang sudah ada (webhook, endpoint, atau scheduler), dan menulis balik ke Postgres yang sama.

| Agent | Pemicu | Yang diubah |
|---|---|---|
| [Lead Evaluation](lead-evaluation.md) | Webhook WhatsApp: pesan masuk dan echo pesan keluar | `wa_conversations.brand_name`, `project_value`, `lead_status`, `note` |

## Tata letak

```
app/modules/agents/
├── llm.py                  # factory LLM bersama + is_configured()
└── <nama_agent>/
    ├── schema.py           # state graph + skema keluaran LLM
    ├── prompts.py
    ├── repository.py       # baca konteks, tulis hasil
    ├── llm.py              # model, batas token, dan pemanggilan LLM agent ini
    ├── graph.py            # node + wiring StateGraph
    └── service.py          # entry point yang dipanggil dari luar
```

## Aturan bersama

- **Satu environment variable**: `OPENAI_API_KEY`. Pilihan model dan batas token adalah konstanta di kode, karena keduanya menempel pada perilaku agent, bukan pada environment tempat ia jalan.
- **Gagal tanpa menjatuhkan apa pun.** Setiap node menangkap exception-nya sendiri dan menaruh pesannya di `state["error"]`. Agent yang gagal berakhir sebagai baris log, tidak pernah membatalkan alur yang memicunya.
- **Mati kalau tidak dikonfigurasi.** Tanpa API key, `is_configured()` mengembalikan `False` dan service berhenti di awal.
- **Session database sendiri.** Agent jalan di session terpisah dari alur pemicunya, supaya panggilan LLM tidak menahan koneksi tulis.
- **Batas token diukur, bukan ditebak.** Ukur keluaran terpanjang yang realistis, lalu ambil 3x lipatnya sebagai plafon.

## Menambah agent baru

Buat folder di `app/modules/agents/`, pakai `build_llm()` dari `llm.py` bersama, rakit graph-nya, lalu sambungkan factory-nya di [app/server.py](../../app/server.py) — satu-satunya tempat wiring di repo ini.
