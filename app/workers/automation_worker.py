"""Worker assíncrono contínuo para processamento da fila de disparos omnichannel.

Execução standalone no container:
  docker compose exec -T api python -m app.workers.automation_worker
"""

from __future__ import annotations

import asyncio
import logging
import os

from app.db.session import async_engine, AsyncSessionLocal
from app.services import automation_service

logger = logging.getLogger("automation_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


async def run_forever(poll_interval_seconds: int = 10) -> None:
    logger.info("Worker de Automação Omnichannel iniciado (intervalo=%ss).", poll_interval_seconds)
    while True:
        try:
            async with AsyncSessionLocal() as session:
                res = await automation_service.process_dispatch_queue(session, tenant_id=None, batch_size=100)
                await session.commit()
                if res.processed_count > 0:
                    logger.info(
                        "Ciclo concluído: processados=%d enviados=%d falhas=%d reenfileirados=%d",
                        res.processed_count,
                        res.sent_count,
                        res.failed_count,
                        res.requeued_count,
                    )
        except Exception as exc:
            logger.exception("Erro no ciclo do worker de automação: %s", exc)
        await asyncio.sleep(poll_interval_seconds)


if __name__ == "__main__":
    interval = int(os.getenv("AUTOMATION_WORKER_INTERVAL_SECONDS", "10"))
    asyncio.run(run_forever(poll_interval_seconds=interval))
