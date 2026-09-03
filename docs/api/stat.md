# Stat

Read-only aggregate endpoints untuk dashboard evaluasi 360° WhatsApp brand deals: volume percakapan harian, first response time (median/p90), heatmap jam inbound, funnel lead status beserta nilai project, daftar chat tanpa balasan, dan daftar percakapan yang butuh aksi.

Semua angka dihitung langsung dari `wa_conversations` + `wa_chats` dan di-scope per tenant lewat `tenant_id` — tidak ada laporan manual dan tidak ada tabel agregat terpisah. Semua endpoint memakai `POST`, diautentikasi dengan Bearer token statis dari environment `CLIENT_SECRET`.

Setiap request menerima `start_date`/`end_date` (inklusif, format `YYYY-MM-DD`) dan `timezone` (nama IANA, default `Asia/Jakarta`). Bucket harian dan heatmap dihitung pada zona waktu tersebut, bukan UTC. Jika `start_date`/`end_date` dikosongkan, rentang default adalah 30 hari terakhir sampai hari ini; rentang maksimum 366 hari.

Definisi **turn**: satu pesan masuk yang membuka giliran balas, yaitu inbound pertama setelah outbound terakhir. Beberapa pesan inbound beruntun tanpa balasan dihitung sebagai satu turn. **First response time** = selisih waktu turn tersebut ke pesan outbound pertama sesudahnya; turn tanpa outbound sesudahnya dihitung sebagai *unanswered*.

## Endpoints

### `POST {base_url}/api/v1/stats/summary`

Mengembalikan kartu ringkasan dashboard: volume inbound, first response median/p90, jumlah tanpa balasan, dan persentase pencapaian target.

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "start_date": "2026-08-04",
  "end_date": "2026-09-02",
  "timezone": "Asia/Jakarta",
  "target_seconds": 900
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `start_date` | string (`YYYY-MM-DD`) | no |
| `end_date` | string (`YYYY-MM-DD`) | no |
| `timezone` | string (IANA) | no |
| `target_seconds` | integer (1–86400) | no |

**Response** — `200 OK`

```json
{
  "success": true,
  "code": 200,
  "status": "OK",
  "message": "summary retrieved successfully",
  "data": {
    "start_date": "2026-08-04",
    "end_date": "2026-09-02",
    "timezone": "Asia/Jakarta",
    "target_seconds": 900,
    "active_conversation_count": 11,
    "new_conversation_count": 11,
    "inbound_per_day": 0.37,
    "inbound_turn_count": 20,
    "replied_turn_count": 13,
    "unanswered_turn_count": 7,
    "unanswered_conversation_count": 7,
    "median_response_seconds": 813,
    "p90_response_seconds": 4038,
    "within_target_count": 7,
    "within_target_percent": 53.8,
    "reply_rate_percent": 65.0
  }
}
```

`median_response_seconds` dan `p90_response_seconds` bernilai `null` jika tidak ada satu pun turn yang dibalas pada rentang tersebut.

**Errors**

| Code | Status | Message | When |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | `missing or invalid authorization header` | header authorization hilang/salah |
| 400 | `BAD_REQUEST` | `invalid request: tenant_id` | `tenant_id` tidak dikirim atau bukan string |
| 400 | `BAD_REQUEST` | `start_date must be on or before end_date` | rentang tanggal terbalik |
| 400 | `BAD_REQUEST` | `date range must not exceed 366 days` | rentang lebih dari 366 hari |
| 400 | `BAD_REQUEST` | `timezone must be a valid IANA timezone name` | nama zona waktu tidak dikenal |
| 404 | `NOT_FOUND` | `tenant not found` | `tenant_id` tidak ada di tabel tenants |
| 500 | `INTERNAL_SERVER_ERROR` | `an unexpected error occurred` | kegagalan DB atau `CLIENT_SECRET` belum di-set |

### `POST {base_url}/api/v1/stats/chats-volume`

Mengembalikan jumlah percakapan yang menerima pesan masuk per hari, dipisah antara percakapan baru dan percakapan lanjutan.

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "start_date": "2026-08-30",
  "end_date": "2026-09-02",
  "timezone": "Asia/Jakarta"
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `start_date` | string (`YYYY-MM-DD`) | no |
| `end_date` | string (`YYYY-MM-DD`) | no |
| `timezone` | string (IANA) | no |

**Response** — `200 OK`

```json
{
  "success": true,
  "code": 200,
  "status": "OK",
  "message": "chats volume retrieved successfully",
  "data": {
    "start_date": "2026-08-30",
    "end_date": "2026-09-02",
    "timezone": "Asia/Jakarta",
    "total_conversation_count": 11,
    "list": [
      {
        "date": "2026-09-01",
        "conversation_count": 0,
        "new_conversation_count": 0,
        "returning_conversation_count": 0
      },
      {
        "date": "2026-09-02",
        "conversation_count": 11,
        "new_conversation_count": 11,
        "returning_conversation_count": 0
      }
    ]
  }
}
```

Setiap hari pada rentang selalu muncul, termasuk hari tanpa pesan masuk.

**Errors**

| Code | Status | Message | When |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | `missing or invalid authorization header` | header authorization hilang/salah |
| 400 | `BAD_REQUEST` | `invalid request: tenant_id` | `tenant_id` tidak dikirim atau bukan string |
| 400 | `BAD_REQUEST` | `start_date must be on or before end_date` | rentang tanggal terbalik |
| 400 | `BAD_REQUEST` | `timezone must be a valid IANA timezone name` | nama zona waktu tidak dikenal |
| 404 | `NOT_FOUND` | `tenant not found` | `tenant_id` tidak ada di tabel tenants |
| 500 | `INTERNAL_SERVER_ERROR` | `an unexpected error occurred` | kegagalan DB atau `CLIENT_SECRET` belum di-set |

### `POST {base_url}/api/v1/stats/response-time`

Mengembalikan first response time median dan p90 per hari, beserta jumlah turn yang dibalas di bawah target.

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "start_date": "2026-08-30",
  "end_date": "2026-09-02",
  "timezone": "Asia/Jakarta",
  "target_seconds": 900
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `start_date` | string (`YYYY-MM-DD`) | no |
| `end_date` | string (`YYYY-MM-DD`) | no |
| `timezone` | string (IANA) | no |
| `target_seconds` | integer (1–86400) | no |

**Response** — `200 OK`

```json
{
  "success": true,
  "code": 200,
  "status": "OK",
  "message": "response time retrieved successfully",
  "data": {
    "start_date": "2026-08-30",
    "end_date": "2026-09-02",
    "timezone": "Asia/Jakarta",
    "target_seconds": 900,
    "list": [
      {
        "date": "2026-09-01",
        "inbound_turn_count": 0,
        "replied_turn_count": 0,
        "unanswered_turn_count": 0,
        "median_response_seconds": null,
        "p90_response_seconds": null,
        "within_target_count": 0,
        "within_target_percent": 0.0
      },
      {
        "date": "2026-09-02",
        "inbound_turn_count": 20,
        "replied_turn_count": 13,
        "unanswered_turn_count": 7,
        "median_response_seconds": 813,
        "p90_response_seconds": 4038,
        "within_target_count": 7,
        "within_target_percent": 53.8
      }
    ]
  }
}
```

Hari tanpa pesan masuk tetap dikembalikan dengan median dan p90 `null`, supaya garis pada chart tidak terputus.

**Errors**

| Code | Status | Message | When |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | `missing or invalid authorization header` | header authorization hilang/salah |
| 400 | `BAD_REQUEST` | `invalid request: target_seconds` | `target_seconds` di luar rentang 1–86400 |
| 400 | `BAD_REQUEST` | `start_date must be on or before end_date` | rentang tanggal terbalik |
| 400 | `BAD_REQUEST` | `timezone must be a valid IANA timezone name` | nama zona waktu tidak dikenal |
| 404 | `NOT_FOUND` | `tenant not found` | `tenant_id` tidak ada di tabel tenants |
| 500 | `INTERNAL_SERVER_ERROR` | `an unexpected error occurred` | kegagalan DB atau `CLIENT_SECRET` belum di-set |

### `POST {base_url}/api/v1/stats/inbound-heatmap`

Mengembalikan sebaran pesan masuk per kombinasi hari dalam minggu dan jam, untuk heatmap kapan inbound datang.

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "start_date": "2026-08-04",
  "end_date": "2026-09-02",
  "timezone": "Asia/Jakarta"
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `start_date` | string (`YYYY-MM-DD`) | no |
| `end_date` | string (`YYYY-MM-DD`) | no |
| `timezone` | string (IANA) | no |

**Response** — `200 OK`

```json
{
  "success": true,
  "code": 200,
  "status": "OK",
  "message": "inbound heatmap retrieved successfully",
  "data": {
    "start_date": "2026-08-04",
    "end_date": "2026-09-02",
    "timezone": "Asia/Jakarta",
    "total_message_count": 39,
    "list": [
      { "day_of_week": 3, "hour": 17, "message_count": 10, "conversation_count": 4 },
      { "day_of_week": 3, "hour": 18, "message_count": 11, "conversation_count": 4 }
    ]
  }
}
```

`day_of_week` memakai ISO: `1` = Senin sampai `7` = Minggu. `hour` bernilai `0`–`23` pada zona waktu yang diminta. Kombinasi tanpa pesan masuk tidak dikembalikan — klien mengisi sisanya dengan nol.

**Errors**

| Code | Status | Message | When |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | `missing or invalid authorization header` | header authorization hilang/salah |
| 400 | `BAD_REQUEST` | `invalid request: tenant_id` | `tenant_id` tidak dikirim atau bukan string |
| 400 | `BAD_REQUEST` | `timezone must be a valid IANA timezone name` | nama zona waktu tidak dikenal |
| 404 | `NOT_FOUND` | `tenant not found` | `tenant_id` tidak ada di tabel tenants |
| 500 | `INTERNAL_SERVER_ERROR` | `an unexpected error occurred` | kegagalan DB atau `CLIENT_SECRET` belum di-set |

### `POST {base_url}/api/v1/stats/lead-status`

Mengembalikan funnel percakapan per `lead_status`, beserta pembagian mode AI/human, rata-rata winning rate, dan nilai project yang tertahan di tiap stage.

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "start_date": "2026-08-04",
  "end_date": "2026-09-02"
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `start_date` | string (`YYYY-MM-DD`) | no |
| `end_date` | string (`YYYY-MM-DD`) | no |
| `timezone` | string (IANA) | no |

**Response** — `200 OK`

```json
{
  "success": true,
  "code": 200,
  "status": "OK",
  "message": "lead status retrieved successfully",
  "data": {
    "start_date": "2026-08-04",
    "end_date": "2026-09-02",
    "timezone": "Asia/Jakarta",
    "total_conversation_count": 11,
    "total_project_value": 185000000,
    "list": [
      {
        "lead_status": "cold",
        "conversation_count": 5,
        "mode_ai_count": 5,
        "mode_human_count": 0,
        "avg_winning_rate": 8,
        "valued_conversation_count": 0,
        "total_project_value": 0
      },
      {
        "lead_status": "qualified",
        "conversation_count": 3,
        "mode_ai_count": 2,
        "mode_human_count": 1,
        "avg_winning_rate": 35,
        "valued_conversation_count": 2,
        "total_project_value": 45000000
      },
      {
        "lead_status": "rate_card_sent",
        "conversation_count": 2,
        "mode_ai_count": 0,
        "mode_human_count": 2,
        "avg_winning_rate": 60,
        "valued_conversation_count": 2,
        "total_project_value": 65000000
      },
      {
        "lead_status": "negotiation",
        "conversation_count": 1,
        "mode_ai_count": 0,
        "mode_human_count": 1,
        "avg_winning_rate": 80,
        "valued_conversation_count": 1,
        "total_project_value": 75000000
      },
      {
        "lead_status": "closed",
        "conversation_count": 0,
        "mode_ai_count": 0,
        "mode_human_count": 0,
        "avg_winning_rate": 0,
        "valued_conversation_count": 0,
        "total_project_value": 0
      }
    ]
  }
}
```

Rentang tanggal difilter pada `wa_conversations.created_at`, bukan pada aktivitas chat.

`lead_status` adalah stage funnel dan selalu dikembalikan lengkap dalam urutan `cold` → `qualified` → `rate_card_sent` → `negotiation` → `closed`. Stage tanpa percakapan tetap muncul dengan hitungan `0` supaya funnel tidak bolong.

`project_value` adalah nilai project per percakapan dalam Rupiah penuh (tanpa desimal) dan boleh `null` selama belum ditentukan. `valued_conversation_count` menghitung percakapan yang `project_value`-nya sudah terisi, jadi `total_project_value` bisa dibaca sebagai nilai yang tertahan di stage tersebut — bukan estimasi seluruh percakapan di stage itu.

**Errors**

| Code | Status | Message | When |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | `missing or invalid authorization header` | header authorization hilang/salah |
| 400 | `BAD_REQUEST` | `invalid request: tenant_id` | `tenant_id` tidak dikirim atau bukan string |
| 404 | `NOT_FOUND` | `tenant not found` | `tenant_id` tidak ada di tabel tenants |
| 500 | `INTERNAL_SERVER_ERROR` | `an unexpected error occurred` | kegagalan DB atau `CLIENT_SECRET` belum di-set |

### `POST {base_url}/api/v1/stats/unanswered/list`

Mengembalikan daftar percakapan yang punya pesan masuk tanpa balasan sama sekali, diurutkan dari yang paling lama menunggu.

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "start_date": "2026-08-04",
  "end_date": "2026-09-02",
  "page": 1,
  "page_size": 20
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `start_date` | string (`YYYY-MM-DD`) | no |
| `end_date` | string (`YYYY-MM-DD`) | no |
| `timezone` | string (IANA) | no |
| `page` | integer | no |
| `page_size` | integer | no |

**Response** — `200 OK`

```json
{
  "success": true,
  "code": 200,
  "status": "OK",
  "message": "unanswered conversations retrieved successfully",
  "data": {
    "start_date": "2026-08-04",
    "end_date": "2026-09-02",
    "timezone": "Asia/Jakarta",
    "list": [
      {
        "conv_id": "CSzBhHYZH2h61r5eXoe6n",
        "full_name": "Vicky - Sands Bosum Indonesia",
        "phone_number": "6282213950021",
        "brand_name": "Sands Bosum Indonesia",
        "lead_status": "rate_card_sent",
        "project_value": 35000000,
        "note": "Rate card sudah dikirim, brand minta waktu untuk review internal.",
        "unanswered_turn_count": 1,
        "first_unanswered_at": "2026-09-02T10:00:08+00:00",
        "last_unanswered_at": "2026-09-02T10:00:08+00:00",
        "waiting_hours": 5
      }
    ],
    "metapaging": {
      "total_data": 7,
      "total_page": 1,
      "current_page": 1,
      "page_size": 20
    }
  }
}
```

`page` default `1`, `page_size` default `20` dan dibatasi maksimum `100`. `waiting_hours` dihitung dari `first_unanswered_at` sampai sekarang. `brand_name`, `project_value`, dan `note` boleh `null` kalau belum diisi — dipakai untuk memperkirakan nilai yang berisiko hilang dari chat yang menggantung, dan `note` memberi konteks terakhir tanpa perlu membuka thread-nya.

**Errors**

| Code | Status | Message | When |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | `missing or invalid authorization header` | header authorization hilang/salah |
| 400 | `BAD_REQUEST` | `invalid request: page` | `page` kurang dari 1 |
| 400 | `BAD_REQUEST` | `start_date must be on or before end_date` | rentang tanggal terbalik |
| 404 | `NOT_FOUND` | `tenant not found` | `tenant_id` tidak ada di tabel tenants |
| 500 | `INTERNAL_SERVER_ERROR` | `an unexpected error occurred` | kegagalan DB atau `CLIENT_SECRET` belum di-set |

### `POST {base_url}/api/v1/stats/needs-action/list`

Mengembalikan daftar percakapan yang pesan terakhirnya dari pelanggan dan sudah diam lebih lama dari `idle_hours` — isi tabel "butuh aksi sekarang".

**Method:** `POST`

**Authorization:** `Bearer <client_secret>`

**Request**

```json
{
  "tenant_id": "8yuPA4qUjqC3OfizucQoH",
  "start_date": "2026-08-04",
  "end_date": "2026-09-02",
  "idle_hours": 48,
  "page": 1,
  "page_size": 20
}
```

| Field | Type | Required |
|---|---|---|
| `tenant_id` | string | yes |
| `start_date` | string (`YYYY-MM-DD`) | no |
| `end_date` | string (`YYYY-MM-DD`) | no |
| `timezone` | string (IANA) | no |
| `idle_hours` | integer (1–8760) | no |
| `page` | integer | no |
| `page_size` | integer | no |

**Response** — `200 OK`

```json
{
  "success": true,
  "code": 200,
  "status": "OK",
  "message": "needs action conversations retrieved successfully",
  "data": {
    "start_date": "2026-08-04",
    "end_date": "2026-09-02",
    "timezone": "Asia/Jakarta",
    "idle_hours": 48,
    "list": [
      {
        "conv_id": "CSzBhHYZH2h61r5eXoe6n",
        "full_name": "Vicky - Sands Bosum Indonesia",
        "phone_number": "6282213950021",
        "brand_name": "Sands Bosum Indonesia",
        "lead_status": "negotiation",
        "project_value": 75000000,
        "winning_rate": 0,
        "mode": "ai",
        "note": "Nego turun dari rate card, menunggu keputusan internal brand.",
        "last_message_at": "2026-09-02T10:00:08+00:00",
        "last_message_type": "text",
        "last_message_preview": "Belum kaak, aku habis flight nih",
        "idle_hours": 5
      }
    ],
    "metapaging": {
      "total_data": 3,
      "total_page": 1,
      "current_page": 1,
      "page_size": 20
    }
  }
}
```

`idle_hours` default `48`, mengikuti ambang "lead idle" pada dokumen evaluasi. `last_message_preview` dipotong 120 karakter pertama. `brand_name`, `project_value`, dan `note` boleh `null` kalau belum diisi; `note` dikembalikan utuh tanpa dipotong.

**Errors**

| Code | Status | Message | When |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | `missing or invalid authorization header` | header authorization hilang/salah |
| 400 | `BAD_REQUEST` | `invalid request: idle_hours` | `idle_hours` di luar rentang 1–8760 |
| 400 | `BAD_REQUEST` | `start_date must be on or before end_date` | rentang tanggal terbalik |
| 404 | `NOT_FOUND` | `tenant not found` | `tenant_id` tidak ada di tabel tenants |
| 500 | `INTERNAL_SERVER_ERROR` | `an unexpected error occurred` | kegagalan DB atau `CLIENT_SECRET` belum di-set |

## Metrik yang belum bisa dilayani

Dua elemen dashboard pada dokumen evaluasi masih belum punya sumber data di schema dan belum dibuatkan endpoint. Keduanya butuh penambahan kolom di schema Prisma milik `pureva-ai` lebih dulu.

| Elemen dashboard | Yang dibutuhkan |
|---|---|
| Lost reason | Kolom alasan saat percakapan ditutup |
| Cycle time inbound → closed | Timestamp saat stage closed tercapai |

Funnel `Inbound → Qualified → Rate card → Nego → Closed` sudah dilayani `POST /stats/lead-status` sejak `wa_lead_status_enum` memakai stage `cold` → `qualified` → `rate_card_sent` → `negotiation` → `closed`. Estimasi leakage (Rp) bisa dirakit dari `total_project_value` per stage pada endpoint yang sama, digabung dengan `project_value` pada `POST /stats/unanswered/list` dan `POST /stats/needs-action/list`.
