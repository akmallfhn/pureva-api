-- Migrasi: tabel chat Knowledge (kb_*)
--
-- Jalankan sekali di Supabase pureva. Aditif — tidak menyentuh tabel yang sudah ada.

------------------
-- Enumerations --
------------------

-- Enumeration for the kb_chats table (kbc_*)

CREATE TYPE kbc_role_enum AS ENUM (
  'user',
  'assistant'
);

CREATE TYPE kbc_status_enum AS ENUM (
  'queued',
  'streaming',
  'done',
  'failed'
);

------------
-- Tables --
------------

CREATE TABLE kb_conversations (
  id          CHAR(21)     PRIMARY KEY  DEFAULT nanoid(),
  tenant_id   CHAR(21)     NOT NULL,
  title       VARCHAR      NOT NULL     DEFAULT '',
  created_at  TIMESTAMPTZ  NOT NULL     DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMPTZ  NOT NULL     DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE kb_chats (
  id          CHAR(21)          PRIMARY KEY  DEFAULT nanoid(),
  conv_id     CHAR(21)          NOT NULL,
  role        kbc_role_enum     NOT NULL,
  message     VARCHAR           NOT NULL     DEFAULT '',
  status      kbc_status_enum       NULL,
  error       VARCHAR               NULL,
  sources     JSON                  NULL,
  created_at  TIMESTAMPTZ       NOT NULL     DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMPTZ       NOT NULL     DEFAULT CURRENT_TIMESTAMP
);

----------------
-- References --
----------------

-- Knowledge chat

ALTER TABLE kb_conversations
  ADD FOREIGN KEY (tenant_id) REFERENCES tenants (id);

ALTER TABLE kb_chats
  ADD FOREIGN KEY (conv_id) REFERENCES kb_conversations (id) ON DELETE CASCADE;

-------------
-- Indexes --
-------------

-- Knowledge chat

CREATE INDEX kb_conversations_tenant_id_idx  ON kb_conversations (tenant_id, updated_at DESC);
CREATE INDEX kb_chats_conv_id_idx            ON kb_chats (conv_id, created_at);

--------------------------
-- Retrieval (RAG) index --
--------------------------

-- Retriever leksikal jalan di atas wa_chats/wa_conversations yang sudah ada, tanpa tabel
-- turunan: korpusnya kecil dan selalu berubah, jadi indeks turunan cuma menambah jalur basi.
-- pg_trgm dipakai supaya ILIKE nama brand dan isi pesan tidak jadi seq scan penuh.
--
-- Bagian ini OPSIONAL: retriever tetap benar tanpanya, cuma lebih lambat. Jalankan
-- terpisah dari blok di atas. CONCURRENTLY wajib di sini karena wa_chats adalah tabel
-- yang ditulis webhook WhatsApp — CREATE INDEX biasa mengunci penulisan sampai selesai.
-- CONCURRENTLY tidak boleh jalan di dalam transaksi, jadi kirim satu per satu.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE INDEX CONCURRENTLY wa_conversations_brand_name_trgm_idx
  ON wa_conversations USING GIN (brand_name gin_trgm_ops);
CREATE INDEX CONCURRENTLY wa_conversations_full_name_trgm_idx
  ON wa_conversations USING GIN (full_name gin_trgm_ops);
CREATE INDEX CONCURRENTLY wa_chats_message_trgm_idx
  ON wa_chats USING GIN (message gin_trgm_ops);
