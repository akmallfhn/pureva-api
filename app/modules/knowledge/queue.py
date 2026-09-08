"""Antrean job chat Knowledge: worker tetap, terbatas jumlahnya, hidup di memori satu proses."""

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Jumlah jawaban yang boleh diproses bersamaan.
DEFAULT_WORKERS = 2

# Job menunggu di antrean; lebih dari ini permintaan ditolak, bukan diantre tanpa batas.
DEFAULT_MAX_PENDING = 32

# Buffer event per job; klien yang lambat kehilangan delta lama, teks utuhnya tetap ke DB.
CHANNEL_BUFFER = 512

# Plafon satu job, supaya worker tidak ditahan selamanya oleh panggilan yang menggantung.
JOB_TIMEOUT_SECONDS = 300.0


@dataclass(frozen=True)
class ChatJob:
    tenant_id: str
    conv_id: str
    # Baris pertanyaannya, dan baris kosong yang akan diisi jawabannya.
    question_id: str
    chat_id: str
    question: str
    is_new_conversation: bool
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


class JobChannel:
    """Aliran event satu job. Ditutup dengan sentinel supaya pembacanya berhenti sendiri."""

    def __init__(self) -> None:
        self._events: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue(CHANNEL_BUFFER)
        self._closed = False

    async def publish(self, event: str, data: str) -> None:
        if self._closed:
            return
        try:
            self._events.put_nowait((event, data))
        except asyncio.QueueFull:
            # Buang delta terlama, bukan yang terbaru: yang dibaca orang adalah ujung tulisan.
            try:
                self._events.get_nowait()
                self._events.put_nowait((event, data))
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                logger.warning("knowledge: dropped event, channel congested")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._events.put(None)

    async def __aiter__(self) -> AsyncIterator[tuple[str, str]]:
        while True:
            item = await self._events.get()
            if item is None:
                return
            yield item


Runner = Callable[[ChatJob, JobChannel], Awaitable[None]]


class QueueFullError(RuntimeError):
    """Antrean penuh; pemanggil yang memutuskan mau balas apa ke klien."""


class ChatQueue:
    def __init__(
        self,
        *,
        run: Runner,
        workers: int = DEFAULT_WORKERS,
        max_pending: int = DEFAULT_MAX_PENDING,
    ) -> None:
        self._run = run
        self._worker_count = workers
        self._queue: asyncio.Queue[tuple[ChatJob, JobChannel]] = asyncio.Queue(max_pending)
        self._workers: list[asyncio.Task] = []

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(self._worker(i), name=f"knowledge-worker-{i}")
            for i in range(self._worker_count)
        ]
        logger.info(f"knowledge: {self._worker_count} chat workers started")

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []

    def submit(self, job: ChatJob) -> JobChannel:
        channel = JobChannel()
        try:
            self._queue.put_nowait((job, channel))
        except asyncio.QueueFull:
            raise QueueFullError("chat queue is full") from None
        return channel

    async def _worker(self, index: int) -> None:
        while True:
            job, channel = await self._queue.get()
            try:
                await asyncio.wait_for(self._run(job, channel), timeout=JOB_TIMEOUT_SECONDS)
            except asyncio.CancelledError:
                await channel.publish("error", "server shutting down")
                await channel.close()
                raise
            except TimeoutError:
                logger.warning(f"knowledge: job {job.id} timed out")
                await channel.publish("error", "jawaban tidak selesai dalam batas waktu")
                await channel.close()
            except Exception:
                # Job yang gagal tidak boleh menjatuhkan worker-nya; job berikutnya harus jalan.
                logger.exception(f"knowledge: job {job.id} failed on worker {index}")
                await channel.publish("error", "terjadi kesalahan saat menyusun jawaban")
                await channel.close()
            finally:
                self._queue.task_done()
