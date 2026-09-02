from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Pureva API"
    debug: bool = False

    # Railway (dan PaaS lain) inject PORT saat runtime; APP_PORT fallback lokal.
    port: int = Field(default=8000, validation_alias=AliasChoices("PORT", "APP_PORT"))

    # Postgres multitenant pureva, dipakai bareng app pureva-ai (Next.js).
    database_url: str = ""
    db_pool_size: int = 10
    db_max_overflow: int = 0

    # Meta WhatsApp Cloud API: verifikasi webhook + download media.
    meta_app_secret: str = ""
    meta_webhook_verify_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "META_WEBHOOK_VERIFY_TOKEN", "WHATSAPP_WEBHOOK_VERIFICATION_TOKEN"
        ),
    )
    graph_api_version: str = "v25.0"

    # Bearer token yang wajib dikirim client dashboard ke endpoint /api/v1/stats.
    client_secret: str = ""

    # Zona waktu default untuk bucket harian & heatmap statistik.
    stat_timezone: str = "Asia/Jakarta"

    # Supabase Storage untuk attachment WhatsApp; bucket sama dengan yang dibaca UI pureva-ai.
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    supabase_bucket: str = "pureva"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
