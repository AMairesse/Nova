# nova/llm/checkpoints.py
import asyncio
from concurrent.futures import Future
import threading
from psycopg_pool import AsyncConnectionPool
from psycopg.rows import dict_row
from django.conf import settings
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

# --- global state --------------------------------------------------------
_bootstrap_done: bool = False
_bootstrap_guard = threading.Lock()
_bootstrap_future: Future | None = None
# -------------------------------------------------------------------------


def _make_conn_str() -> str:
    db = settings.DATABASES["default"]
    return (
        f"postgresql://{db['USER']}:{db['PASSWORD']}@"
        f"{db['HOST']}:{db['PORT']}/{db['NAME']}"
    )


async def _bootstrap_tables(conn_str: str) -> None:
    global _bootstrap_done, _bootstrap_future
    if _bootstrap_done:
        return

    while True:
        with _bootstrap_guard:
            if _bootstrap_done:
                return
            if _bootstrap_future is None:
                bootstrap_future = Future()
                _bootstrap_future = bootstrap_future
                is_leader = True
            else:
                bootstrap_future = _bootstrap_future
                is_leader = False

        if is_leader:
            try:
                async with AsyncConnectionPool(
                    conn_str,
                    kwargs={"autocommit": True, "row_factory": dict_row},
                    open=False,
                ) as tmp_pool:
                    saver = AsyncPostgresSaver(tmp_pool)
                    await saver.setup()
            except Exception as exc:
                bootstrap_future.set_exception(exc)
                with _bootstrap_guard:
                    if _bootstrap_future is bootstrap_future:
                        _bootstrap_future = None
                raise
            else:
                with _bootstrap_guard:
                    _bootstrap_done = True
                    if _bootstrap_future is bootstrap_future:
                        _bootstrap_future = None
                bootstrap_future.set_result(None)
                return

        await asyncio.wrap_future(bootstrap_future)
        return


async def get_checkpointer() -> AsyncPostgresSaver:
    conn_str = _make_conn_str()
    await _bootstrap_tables(conn_str)

    runtime_pool = AsyncConnectionPool(conninfo=conn_str, timeout=10, open=False)
    await runtime_pool.open()
    saver = AsyncPostgresSaver(runtime_pool)
    return saver
