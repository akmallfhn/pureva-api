from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tenant.entity import Tenant


class TenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def find_by_wa_phone_number_id(self, phone_number_id: str) -> Tenant | None:
        """Routing multitenant: nomor WhatsApp pengirim event menentukan tenant-nya."""
        stmt = select(Tenant).where(Tenant.wa_phone_number_id == phone_number_id).limit(1)
        return (await self._session.execute(stmt)).scalars().first()

    async def find_by_id(self, tenant_id: str) -> Tenant | None:
        return await self._session.get(Tenant, tenant_id)
