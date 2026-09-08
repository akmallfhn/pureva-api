# Knowledge

Chatbot internal untuk menanyakan kondisi brand deal WhatsApp: CRUD thread percakapannya, plus satu endpoint streaming yang menjawab pertanyaan lewat agent LangGraph. Dipakai halaman Knowledge di dashboard TRC dan tidak dimaksudkan untuk diekspos ke luar tim.

Jawaban tidak pernah disusun dari ingatan model. Agent mengambil datanya lebih dulu lewat tool yang membungkus endpoint `stats/*` yang sama dengan dashboard, ditambah pencarian leksikal ke `wa_conversations`/`wa_chats`, lalu menulis jawaban hanya dari hasil pengambilan itu — jadi angka di chat dan angka di dashboard selalu bisa dicek silang. Jejak pengambilannya ikut disimpan di `kb_chats.sources`.

Semua endpoint memakai `POST` dengan body JSON, diautentikasi dengan Bearer token statis dari environment `CLIENT_SECRET`, dan di-scope per tenant lewat `tenant_id` — sama seperti modul `stat`. Percakapan milik tenant lain tidak pernah bisa dibuka: `conv_id` yang tidak cocok dengan `tenant_id` dibalas `404`, bukan `403`.

Endpoint chat butuh `OPENAI_API_KEY`. Tanpa itu CRUD tetap jalan dan `/chat/stream` membalas `503`.

## Endpoints

### `POST {base_url}/api/v1/knowledge/conversations`

Membuat thread kosong. Dipakai kalau UI mau menyiapkan thread lebih dulu; alur normal tidak memerlukannya, karena `/chat/stream` membuat thread sendiri saat `conv_id` dikosongkan.

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "title": "Evaluasi mingguan"
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `title` | string (maks 120) | no |

**Response** — `201 CREATED`

```json
{
  "success": true,
  "code": 201,
  "status": "CREATED",
  "message": "conversation created successfully",
  "data": {
    "conv_id": "V1StGXR8Z5jdHi6BmyT8K",
    "title": "Evaluasi mingguan",
    "created_at": "2026-09-08T10:12:04.183000+00:00",
    "updated_at": "2026-09-08T10:12:04.183000+00:00"
  }
}
```

| Code | Message |
|---|---|
| `400` | `tenant_id is required` |
| `401` | `missing or invalid authorization header` |
| `404` | `tenant not found` |

### `POST {base_url}/api/v1/knowledge/conversations/list`

Daftar thread milik tenant, terbaru di atas, beserta pratinjau pesan terakhirnya.

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "page": 1,
  "page_size": 20
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `page` | integer (≥1) | no |
| `page_size` | integer (1–100) | no |

**Response** — `200 OK`

```json
{
  "success": true,
  "code": 200,
  "status": "OK",
  "message": "conversations retrieved successfully",
  "data": {
    "list": [
      {
        "id": "V1StGXR8Z5jdHi6BmyT8K",
        "title": "First response minggu ini",
        "created_at": "2026-09-08T10:12:04.183000+00:00",
        "updated_at": "2026-09-08T10:14:51.902000+00:00",
        "chat_count": 4,
        "last_message_preview": "Median first response 8 menit 12 detik pada 1-8 September",
        "last_message_at": "2026-09-08T10:14:51.902000+00:00"
      }
    ],
    "metapaging": {
      "total_data": 1,
      "total_page": 1,
      "current_page": 1,
      "page_size": 20
    }
  }
}
```

| Code | Message |
|---|---|
| `400` | `tenant_id is required` |
| `401` | `missing or invalid authorization header` |
| `404` | `tenant not found` |

### `POST {base_url}/api/v1/knowledge/conversations/detail`

Satu thread beserta seluruh pesannya, urut waktu. Baris `assistant` yang `status`-nya `failed` tetap dikembalikan supaya UI bisa menampilkan giliran yang gagal, bukan menghilangkannya.

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "conv_id": "V1StGXR8Z5jdHi6BmyT8K"
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `conv_id` | string | yes |

**Response** — `200 OK`

```json
{
  "success": true,
  "code": 200,
  "status": "OK",
  "message": "conversation retrieved successfully",
  "data": {
    "conv_id": "V1StGXR8Z5jdHi6BmyT8K",
    "title": "First response minggu ini",
    "created_at": "2026-09-08T10:12:04.183000+00:00",
    "updated_at": "2026-09-08T10:14:51.902000+00:00",
    "list": [
      {
        "id": "mJ2kQpLd0RtVxYc7NfWgA",
        "role": "user",
        "message": "Median first response minggu ini berapa?",
        "status": null,
        "error": null,
        "sources": null,
        "created_at": "2026-09-08T10:12:04.512000+00:00"
      },
      {
        "id": "Bq8ZwLmXe4TnPsRvHkJdY",
        "role": "assistant",
        "message": "Median first response 8 menit 12 detik pada 1-8 September 2026...",
        "status": "done",
        "error": null,
        "sources": [
          {
            "tool": "GetSummary",
            "arguments": { "start_date": "2026-09-01", "end_date": "2026-09-08" },
            "summary": "ringkasan 2026-09-01 sampai 2026-09-08"
          }
        ],
        "created_at": "2026-09-08T10:12:05.104000+00:00"
      }
    ]
  }
}
```

| Code | Message |
|---|---|
| `400` | `tenant_id is required` |
| `401` | `missing or invalid authorization header` |
| `404` | `conversation not found` |

### `POST {base_url}/api/v1/knowledge/conversations/update`

Mengganti judul thread. Judul pertama dibuat otomatis dari pertanyaan pembuka; endpoint ini untuk menimpanya secara manual.

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "conv_id": "V1StGXR8Z5jdHi6BmyT8K",
  "title": "Evaluasi September"
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `conv_id` | string | yes |
| `title` | string (1–120) | yes |

**Response** — `200 OK`

```json
{
  "success": true,
  "code": 200,
  "status": "OK",
  "message": "conversation updated successfully",
  "data": {
    "conv_id": "V1StGXR8Z5jdHi6BmyT8K",
    "title": "Evaluasi September"
  }
}
```

| Code | Message |
|---|---|
| `400` | `invalid request: title` |
| `401` | `missing or invalid authorization header` |
| `404` | `conversation not found` |

### `POST {base_url}/api/v1/knowledge/conversations/delete`

Menghapus thread beserta seluruh pesannya (`kb_chats` ikut terhapus lewat `ON DELETE CASCADE`). Tidak bisa dibatalkan.

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "conv_id": "V1StGXR8Z5jdHi6BmyT8K"
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `conv_id` | string | yes |

**Response** — `200 OK`

```json
{
  "success": true,
  "code": 200,
  "status": "OK",
  "message": "conversation deleted successfully",
  "data": { "conv_id": "V1StGXR8Z5jdHi6BmyT8K" }
}
```

| Code | Message |
|---|---|
| `401` | `missing or invalid authorization header` |
| `404` | `conversation not found` |

### `POST {base_url}/api/v1/knowledge/chat/stream`

Mengirim satu pertanyaan dan mengalirkan jawabannya sebagai Server-Sent Events. `conv_id` kosong berarti thread baru dibuat lebih dulu, dan id-nya dikirim balik sebagai event `conversation` pertama.

Ini satu-satunya endpoint di repo ini yang tidak membalas dengan envelope — bodinya `text/event-stream`. Penolakan yang terjadi **sebelum** stream dibuka (`400`, `401`, `404`, `429`, `503`) tetap dibalas sebagai envelope JSON biasa, jadi klien cukup memeriksa `Content-Type` respons.

Pertanyaannya dititipkan ke antrean sebelum dijawab. Jumlah jawaban yang diproses bersamaan dibatasi karena panggilan LLM punya rate limit; kalau antrean penuh, request ditolak `429` alih-alih diantre tanpa batas. Menutup koneksi tidak membatalkan job: jawabannya tetap diselesaikan dan disimpan, jadi thread-nya utuh waktu dibuka lagi lewat `/conversations/detail`.

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "conv_id": null,
  "message": "Brand mana yang paling lama belum dibalas?"
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `conv_id` | string / null | no |
| `message` | string (1–4000) | yes |

**Response** — `200 OK`, `Content-Type: text/event-stream`

Setiap event membawa satu string ber-encoding JSON pada baris `data:`. Baris yang diawali `:` adalah komentar keepalive dan harus diabaikan.

```
event: conversation
data: "V1StGXR8Z5jdHi6BmyT8K"

event: source
data: "3 brand deal berjalan"

event: delta
data: "Yang paling lama diam "

event: delta
data: "adalah Tokopedia, 6 hari."

event: title
data: "Brand paling lama belum dibalas"

event: done
data: "Bq8ZwLmXe4TnPsRvHkJdY"

```

| Event | Kapan dikirim | Isi `data` |
|---|---|---|
| `conversation` | selalu, paling awal | `conv_id` thread ini |
| `source` | tiap satu langkah retrieval selesai | ringkasan satu baris langkah itu |
| `delta` | tiap potongan jawaban | potongan teks, harus disambung berurutan |
| `title` | hanya pada thread baru, sesudah jawaban selesai | judul hasil rangkuman |
| `done` | pada akhir jawaban yang berhasil | `id` baris `kb_chats` jawabannya |
| `error` | pada kegagalan di tengah jalan | pesan yang bisa ditampilkan ke pengguna |

`error` menutup stream tanpa `done`. Baris jawabannya ditandai `failed` di database, dan giliran itu bisa dikirim ulang oleh pengguna.

| Code | Message |
|---|---|
| `400` | `invalid request: message` |
| `401` | `missing or invalid authorization header` |
| `404` | `conversation not found` |
| `429` | `too many questions in flight, try again shortly` |
| `503` | `knowledge chat is not configured: OPENAI_API_KEY is missing` |
