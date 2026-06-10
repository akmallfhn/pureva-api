from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Pureva WhatsApp Agent"
    debug: bool = False

    # Auth webhook
    agent_secret_key: str = ""

    # LLM providers
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Model assignment (sesuai diagram arsitektur; bisa diganti dari .env)
    # GPT-4o untuk node ringan (classifier, booking, send), GPT-4.5 untuk reasoning.
    model_fast: str = "gpt-4o"
    model_reasoning: str = "gpt-4.5-preview"

    # Meta WhatsApp Cloud API. Kalau kosong, agent jalan mode dry-run (log saja).
    whatsapp_phone_number_id: str = ""
    whatsapp_access_token: str = ""
    whatsapp_api_url: str = "https://graph.facebook.com/v21.0"

    # SQLite
    database_path: str = "pureva.sqlite3"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
