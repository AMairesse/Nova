# nova/llm/checkpoints.py
import asyncio
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from django.conf import settings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

_checkpointer = None


async def _bootstrap_tables(conn_str):
    async with AsyncConnectionPool(conn_str,
                                   max_size=10,
                                   kwargs={"autocommit": True,
                                           "row_factory": dict_row}
                                   ) as tmp_pool:
        checkpointer = AsyncPostgresSaver(tmp_pool)
        await checkpointer.setup()


async def _create_checkpointer():
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    db = settings.DATABASES["default"]
    conn_str = f"postgresql://{db['USER']}:{db['PASSWORD']}@{db['HOST']}:{db['PORT']}/{db['NAME']}"

    # 1. bootstrap
    await _bootstrap_tables(conn_str)

    # 2. pool transactionnel pour l’exécution courante
    runtime_pool = AsyncConnectionPool(conninfo=conn_str, timeout=10)
    saver = AsyncPostgresSaver(runtime_pool)        # <-- premier arg = conn
    _checkpointer = saver
    return saver


async def get_checkpointer():
    return await _create_checkpointer()


def get_checkpointer_sync():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(get_checkpointer())
    finally:
        loop.close()