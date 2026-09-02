"""Satu httpx client dipakai seumur hidup app, biar koneksi ke Meta & Supabase reusable."""

import httpx

DEFAULT_TIMEOUT = 10.0
# Download/upload media WhatsApp jauh lebih lambat daripada call REST biasa.
MEDIA_TIMEOUT = 30.0

_client: httpx.AsyncClient | None = None


def http_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)
    return _client


async def close_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
