"""OpenNEM Database Module


Provides database engine connections and sessions across the entire
project


"""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import deprecation
from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from opennem import settings
from opennem.utils.version import get_version

DeclarativeBase = declarative_base()

logger = logging.getLogger("opennem.db")

_engine = None
_engine_sync = None


def db_connect(db_conn_str: str | None = None, debug: bool = False, timeout: int = 10) -> AsyncEngine:
    """
    Performs database connection using database settings from settings.py.

    Returns sqlalchemy engine instance

    :param db_conn_str: Database connection string
    :param debug: Debug mode will render queries and info to terminal
    :param timeout: Database connection timeout
    """
    global _engine

    if _engine:
        return _engine

    if not db_conn_str:
        db_conn_str = str(settings.db_url)

    try:
        _engine = create_async_engine(
            db_conn_str,
            query_cache_size=1200,
            echo=settings.db_debug,
            future=True,
            pool_size=30,
            max_overflow=20,
            pool_recycle=100,
            pool_timeout=timeout,
            pool_pre_ping=True,
            pool_use_lifo=True,
        )

        return _engine
    except Exception as exc:
        logger.error("Could not connect to database: %s", exc)
        raise exc


def db_connect_sync() -> Engine:
    global _engine_sync

    if _engine_sync:
        return _engine_sync

    db_connect_uri = str(settings.db_url).replace("+asyncpg", "+psycopg")

    _engine_sync = create_engine(db_connect_uri, echo=settings.db_debug)

    return _engine_sync


engine = db_connect()


@deprecation.deprecated(
    deprecated_in="4.0", removed_in="4.1", current_version=get_version(), details="Use the db_connect function instead"
)
def get_database_engine() -> AsyncEngine:
    """
    Gets a database engine connection

    @NOTE deprecate this eventually
    """
    engine = db_connect()

    return engine


# keey the old variable here until we can remove it
SessionLocal: AsyncSession = async_sessionmaker(engine, expire_on_commit=False)
SessionLocalAsync: AsyncSession = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def get_read_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@asynccontextmanager
async def get_write_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocalAsync() as session:
        try:
            async with session.begin():
                yield session
            # The commit will be done automatically when exiting the 'begin' context
        except Exception:
            await session.rollback()
            raise


async def get_scoped_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocalAsync() as session:
        yield session
