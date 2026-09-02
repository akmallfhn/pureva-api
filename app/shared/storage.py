"""Upload attachment ke Supabase Storage; bucket & path sama dengan yang dibaca UI pureva-ai."""

import time

import httpx

from app.shared.http import MEDIA_TIMEOUT


class SupabaseStorage:
    def __init__(
        self, client: httpx.AsyncClient, *, base_url: str, service_role_key: str, bucket: str
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._key = service_role_key
        self._bucket = bucket

    @property
    def enabled(self) -> bool:
        return bool(self._base_url and self._key)

    @staticmethod
    def object_path(tenant_slug: str, media_type: str, media_id: str, mime_type: str) -> str:
        file_ext = mime_type.split("/")[-1].split(";")[0] or "bin"
        return f"{tenant_slug}/{media_type}s/{int(time.time() * 1000)}_{media_id}.{file_ext}"

    async def upload(self, object_path: str, content: bytes, mime_type: str) -> str:
        response = await self._client.post(
            f"{self._base_url}/storage/v1/object/{self._bucket}/{object_path}",
            headers={
                "apikey": self._key,
                "Authorization": f"Bearer {self._key}",
                "Content-Type": mime_type,
                "x-upsert": "false",
            },
            content=content,
            timeout=MEDIA_TIMEOUT,
        )
        response.raise_for_status()
        return f"{self._base_url}/storage/v1/object/public/{self._bucket}/{object_path}"
