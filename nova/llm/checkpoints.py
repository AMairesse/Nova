# nova/llm/checkpoints.py
import asyncio
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from django.conf import settings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

# --- global state --------------------------------------------------------
_bootstrap_done: bool = False
_bootstrap_lock = asyncio.Lock()        # to avoid concurrent bootstraps
# -------------------------------------------------------------------------


def _make_conn_str() -> str:
    db = settings.DATABASES["default"]
    return (
        f"postgresql://{db['USER']}:{db['PASSWORD']}@"
        f"{db['HOST']}:{db['PORT']}/{db['NAME']}"
    )


async def _bootstrap_tables(conn_str: str) -> None:
    global _bootstrap_done
    if _bootstrap_done:
        return

    async with _bootstrap_lock:
        if _bootstrap_done:
            return
        async with AsyncConnectionPool(
            conn_str,
            kwargs={"autocommit": True, "row_factory": dict_row},
        ) as tmp_pool:
            saver = AsyncPostgresSaver(tmp_pool)
            await saver.setup()
        _bootstrap_done = True


async def get_checkpointer() -> AsyncPostgresSaver:
    conn_str = _make_conn_str()
    await _bootstrap_tables(conn_str)

    runtime_pool = AsyncConnectionPool(conninfo=conn_str, timeout=10)
    await runtime_pool.open()
    saver = AsyncPostgresSaver(runtime_pool)
    return saver
