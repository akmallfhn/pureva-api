"""Client Graph API untuk attachment WhatsApp: minta metadata dulu, baru unduh URL-nya."""

import httpx

from app.shared.http import MEDIA_TIMEOUT


class MetaMediaClient:
    def __init__(self, client: httpx.AsyncClient, api_version: str) -> None:
        self._client = client
        self._api_version = api_version

    async def fetch(
        self, *, access_token: str, phone_number_id: str, media_id: str
    ) -> tuple[bytes, str]:
        info_response = await self._client.get(
            f"https://graph.facebook.com/{self._api_version}/{media_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"phone_number_id": phone_number_id},
            timeout=MEDIA_TIMEOUT,
        )
        info_response.raise_for_status()
        media_info = info_response.json()

        media_url = media_info.get("url")
        if not media_url:
            raise RuntimeError(f"no media url returned for {media_id}: {media_info}")

        download_response = await self._client.get(
            media_url,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=MEDIA_TIMEOUT,
        )
        download_response.raise_for_status()
        content = download_response.content
        if not content:
            raise RuntimeError(f"empty media download for {media_id}")

        return content, media_info.get("mime_type") or "application/octet-stream"
