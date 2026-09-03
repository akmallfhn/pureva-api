# Lead Evaluation

Agent LangGraph yang menilai ulang satu percakapan WhatsApp setiap ada pesan baru, lalu mengisi empat kolom di `wa_conversations`: `brand_name`, `project_value`, `lead_status`, dan `note`.

Tujuannya menghilangkan input manual: funnel di `POST /api/v1/stats/lead-status` dan daftar brand deal di `POST /api/v1/stats/needs-action/list` terisi sendiri dari isi percakapan.

## Trigger

Dijalankan dari background task webhook, sesudah pesan tersimpan dan di-commit:

| Sumber | Field webhook | Memicu evaluasi |
|---|---|---|
| Pesan masuk dari pelanggan | `messages` | ya |
| Echo pesan keluar dari WhatsApp Business App | `smb_message_echoes` | ya |
| Update status delivery | `statuses` | tidak |

Status delivery tidak mengubah isi percakapan, jadi tidak ada bahan baru untuk dinilai.

Satu batch webhook menghasilkan **satu evaluasi per percakapan**, bukan satu per pesan — beberapa pesan yang datang bersamaan digabung. Evaluasi jalan di session database terpisah supaya panggilan LLM tidak menahan koneksi tulis, dan kegagalannya tidak pernah menjatuhkan penyimpanan pesan.

## Graph

```
fetch_context ──► evaluate ──► persist ──► END
      │               │
      └──► END        └──► END
```

| Node | Fungsi |
|---|---|
| `fetch_context` | Ambil percakapan + riwayat chat dari Postgres, rakit jadi transkrip. Berhenti kalau percakapan tidak ada atau belum punya satu pun pesan teks. |
| `evaluate` | Kirim transkrip + nilai kolom saat ini ke LLM, minta balasan terstruktur `LeadEvaluation`. |
| `persist` | Terapkan kebijakan tulis, lalu `UPDATE wa_conversations`. Tidak menulis apa-apa kalau tidak ada yang berubah. |

Semua node menangkap exception sendiri dan menaruh pesannya di `state["error"]`, jadi kegagalan LLM atau database berakhir sebagai baris log — bukan exception yang naik ke webhook.

## Model

| Node | Provider | Model | Batas output |
|---|---|---|---|
| `evaluate` | openai | `gpt-4.1-mini` | 400 token |

Structured output memakai `method="json_schema"`, yaitu Structured Outputs milik OpenAI, jadi balasannya dijamin cocok dengan skema `LeadEvaluation`.

Batas 400 token bukan tebakan: balasan terpanjang yang realistis — `note` empat kalimat, brand dan nominal terisi — terukur 129 token dengan encoding `o200k_base`. Plafonnya diambil 3x angka itu supaya `note` panjang tidak pernah terpotong di tengah.

## Kebijakan tulis

Ini bagian yang paling menentukan perilaku. LLM selalu menilai keempat kolom, tapi `persist` yang memutuskan mana yang benar-benar ditulis:

| Kolom | Kapan ditulis |
|---|---|
| `brand_name` | Hanya kalau masih `NULL`. Sekali terisi tidak pernah ditimpa. |
| `project_value` | Hanya kalau masih `NULL`. Sekali terisi tidak pernah ditimpa. |
| `lead_status` | Hanya kalau stage usulan **lebih maju** dari stage sekarang. Tidak pernah mundur. |
| `note` | Selalu ditulis ulang kalau isinya berubah. |

`brand_name` dan `project_value` dikunci setelah terisi supaya koreksi manusia tidak ditimpa balik oleh model. `lead_status` tidak bisa dikunci dengan aturan yang sama karena kolomnya `NOT NULL DEFAULT 'cold'` — tidak pernah "kosong" — jadi yang dipakai aturan maju-saja. `note` sengaja selalu disegarkan karena fungsinya memang ringkasan kondisi terakhir.

Kebijakan ini **ditegakkan di SQL, bukan di memori**. `diff()` di [graph.py](../../app/modules/agents/lead_evaluation/graph.py) hanya menyusun usulan supaya `UPDATE` kosong bisa dilewati; yang benar-benar menjaga adalah ekspresi di [repository.py](../../app/modules/agents/lead_evaluation/repository.py):

```sql
brand_name    = COALESCE(brand_name, :brand_name)
project_value = COALESCE(project_value, :project_value)
lead_status   = GREATEST(lead_status, CAST(:lead_status AS wa_lead_status_enum))
note          = :note
```

Alasannya konkurensi. Dua batch webhook untuk percakapan yang sama bisa datang hampir bersamaan, dan kalau guard-nya dievaluasi di Python, keduanya membaca state "sebelum" yang sama — evaluasi yang menang bisa jadi yang stage-nya lebih rendah, dan funnel mundur. Dengan guard di dalam satu `UPDATE`, Postgres mengunci barisnya: transaksi kedua memblok, lalu mengevaluasi ulang `COALESCE`/`GREATEST` terhadap nilai yang sudah di-commit transaksi pertama.

`GREATEST` bisa dipakai karena enum Postgres diurutkan sesuai deklarasi, dan `wa_lead_status_enum` memang dideklarasikan dalam urutan funnel. Menambah stage di tengah funnel berarti harus menata ulang urutan enum-nya, bukan menambahkannya di akhir.

`apply()` mengembalikan kolom yang benar-benar mendarat, bukan yang diusulkan, jadi log `updated ... held ...` menunjukkan mana yang ditahan guard.

## Konfigurasi

Hanya satu environment variable:

| Env | Fungsi |
|---|---|
| `OPENAI_API_KEY` | Kosong berarti semua agent mati; webhook tetap menyimpan pesan seperti biasa. |

Sisanya konstanta di kode, bukan env — nilainya menempel pada perilaku agent, bukan pada environment:

| Konstanta | Nilai | Lokasi |
|---|---|---|
| `MODEL` | `gpt-4.1-mini` | [lead_evaluation/llm.py](../../app/modules/agents/lead_evaluation/llm.py) |
| `MAX_OUTPUT_TOKENS` | `400` | [lead_evaluation/llm.py](../../app/modules/agents/lead_evaluation/llm.py) |
| `MAX_CHATS` | `200` | [lead_evaluation/repository.py](../../app/modules/agents/lead_evaluation/repository.py) |
| `MAX_MESSAGE_CHARS` | `500` | [lead_evaluation/repository.py](../../app/modules/agents/lead_evaluation/repository.py) |
| `DEFAULT_TIMEOUT` | `120.0` detik | [agents/llm.py](../../app/modules/agents/llm.py) |

Pesan tanpa teks (sticker, gambar, dokumen) dirender sebagai `[kiriman <tipe>]`.

## Biaya

Satu panggilan LLM per percakapan per batch webhook. Percakapan yang ramai berarti evaluasi berulang atas transkrip yang makin panjang, dan seluruh transkrip dikirim ulang setiap kali — jadi biayanya naik kira-kira kuadratik terhadap panjang percakapan. Yang paling murah untuk menekannya: turunkan `MAX_CHATS`.
