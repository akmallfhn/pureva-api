-- PostgreSQL Database for Pureva

------------------
-- Enumerations --
------------------

-- Enumeration for the users and tenants tables

CREATE TYPE status_enum AS ENUM (
  'active',
  'inactive'
);

-- Enumeration for the wa_conversations table (wa_*)

CREATE TYPE wa_lead_status_enum AS ENUM (
  'cold',
  'qualified',
  'rate_card_sent',
  'negotiation',
  'closed'
);

CREATE TYPE wa_mode_enum AS ENUM (
  'ai',
  'human'
);

-- Enumeration for the wa_chats table (wac_*)

CREATE TYPE wac_direction_enum AS ENUM (
  'inbound',
  'outbound'
);

CREATE TYPE wac_sender_type_enum AS ENUM (
  'user',
  'admin'
);

CREATE TYPE wac_type_enum AS ENUM (
  'audio',
  'button',
  'contacts',
  'document',
  'edit',
  'image',
  'interactive',
  'location',
  'order',
  'reaction',
  'revoke',
  'sticker',
  'system',
  'text',
  'unsupported',
  'video',
  'template'
);

CREATE TYPE wac_status_enum AS ENUM (
  'sent',
  'delivered',
  'read',
  'failed'
);

-- Enumeration for the wa_alerts table (wa_alert_*)

CREATE TYPE wa_alert_status_enum AS ENUM (
  'scheduled',
  'sent',
  'delivered',
  'bounced'
);

------------
-- Tables --
------------

-- Lookup tables

CREATE TABLE roles (
  id          SMALLSERIAL  PRIMARY KEY,
  name        VARCHAR      NOT NULL  UNIQUE,
  permission  SMALLINT     NOT NULL,
  created_at  TIMESTAMPTZ  NOT NULL  DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMPTZ  NOT NULL  DEFAULT CURRENT_TIMESTAMP
);

-- User data

CREATE TABLE users (
  id          UUID         PRIMARY KEY  DEFAULT gen_random_uuid(),
  full_name   VARCHAR      NOT NULL,
  email       VARCHAR      NOT NULL     UNIQUE,
  avatar      VARCHAR          NULL,
  role_id     SMALLINT     NOT NULL     DEFAULT 2,
  status      status_enum  NOT NULL     DEFAULT 'active',
  created_at  TIMESTAMPTZ  NOT NULL     DEFAULT CURRENT_TIMESTAMP,
  updated_at  TIMESTAMPTZ  NOT NULL     DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tokens (
  id          SERIAL       PRIMARY KEY,
  user_id     UUID         NOT NULL,
  is_active   BOOLEAN      NOT NULL  DEFAULT FALSE,
  token       TEXT         NOT NULL  UNIQUE,
  created_at  TIMESTAMPTZ  NOT NULL  DEFAULT CURRENT_TIMESTAMP
);

-- Tenants

CREATE TABLE tenants (
  id                  CHAR(21)     PRIMARY KEY  DEFAULT nanoid(),
  name                VARCHAR      NOT NULL,
  slug                VARCHAR      NOT NULL     UNIQUE,
  status              status_enum  NOT NULL     DEFAULT 'active',
  wa_phone_number_id  VARCHAR          NULL     UNIQUE,
  wa_business_id      VARCHAR          NULL,
  wa_access_token     TEXT             NULL,
  created_at          TIMESTAMPTZ  NOT NULL     DEFAULT CURRENT_TIMESTAMP,
  updated_at          TIMESTAMPTZ  NOT NULL     DEFAULT CURRENT_TIMESTAMP
);

-- WhatsApp chat

CREATE TABLE wa_conversations (
  id             CHAR(21)             PRIMARY KEY  DEFAULT nanoid(),
  tenant_id      CHAR(21)             NOT NULL,
  full_name      VARCHAR              NOT NULL,
  phone_number   VARCHAR              NOT NULL,
  brand_name     VARCHAR                  NULL,
  handler_id     UUID                     NULL,
  lead_status    wa_lead_status_enum  NOT NULL     DEFAULT 'cold',
  project_value  BIGINT                   NULL,
  winning_rate   SMALLINT             NOT NULL     DEFAULT 0,
  mode           wa_mode_enum         NOT NULL     DEFAULT 'human',
  note           VARCHAR                  NULL,
  last_read_id   CHAR(21)                 NULL,
  created_at     TIMESTAMPTZ          NOT NULL     DEFAULT CURRENT_TIMESTAMP,
  updated_at     TIMESTAMPTZ          NOT NULL     DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (tenant_id, phone_number)
);

CREATE TABLE wa_chats (
  id            CHAR(21)              PRIMARY KEY  DEFAULT nanoid(),
  conv_id       CHAR(21)              NOT NULL,
  wam_id        VARCHAR               NOT NULL,
  direction     wac_direction_enum    NOT NULL,
  sender_type   wac_sender_type_enum  NOT NULL,
  reply_to_id   CHAR(21)                  NULL,
  type          wac_type_enum         NOT NULL,
  message       VARCHAR               NOT NULL,
  attachment    JSON                      NULL,
  status        wac_status_enum           NULL,
  sent_at       TIMESTAMPTZ               NULL,
  delivered_at  TIMESTAMPTZ               NULL,
  read_at       TIMESTAMPTZ               NULL,
  failed_at     TIMESTAMPTZ               NULL,
  created_at    TIMESTAMPTZ           NOT NULL     DEFAULT CURRENT_TIMESTAMP,
  updated_at    TIMESTAMPTZ           NOT NULL     DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE wa_alerts (
  id                SERIAL                PRIMARY KEY,
  conv_id           CHAR(21)              NOT NULL,
  email_message_id  TEXT                      NULL  UNIQUE,
  scheduled_at      TIMESTAMPTZ           NOT NULL,
  status            wa_alert_status_enum  NOT NULL  DEFAULT 'scheduled',
  created_at        TIMESTAMPTZ           NOT NULL  DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMPTZ           NOT NULL  DEFAULT CURRENT_TIMESTAMP
);

----------------
-- References --
----------------

-- User data

ALTER TABLE users
  ADD FOREIGN KEY (role_id) REFERENCES roles (id);

ALTER TABLE tokens
  ADD FOREIGN KEY (user_id) REFERENCES users (id);

-- WhatsApp chat

ALTER TABLE wa_conversations
  ADD FOREIGN KEY (tenant_id)    REFERENCES tenants (id),
  ADD FOREIGN KEY (handler_id)   REFERENCES users (id),
  ADD FOREIGN KEY (last_read_id) REFERENCES wa_chats (id);

ALTER TABLE wa_chats
  ADD FOREIGN KEY (conv_id)     REFERENCES wa_conversations (id),
  ADD FOREIGN KEY (reply_to_id) REFERENCES wa_chats (id);

ALTER TABLE wa_alerts
  ADD FOREIGN KEY (conv_id) REFERENCES wa_conversations (id);

-------------
-- Indexes --
-------------

-- WhatsApp chat

CREATE INDEX wa_conversations_tenant_id_idx  ON wa_conversations (tenant_id);
CREATE INDEX wa_chats_conv_id_idx            ON wa_chats (conv_id);
CREATE INDEX wa_alerts_conv_id_idx           ON wa_alerts (conv_id);
