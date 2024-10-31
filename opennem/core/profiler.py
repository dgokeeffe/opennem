"""OpenNEM module for tasks and their analytics

This will track the tasks that are run and their status and output
as well as the time taken to run them.

Optionally log to the database or another data persistance option
"""

import enum
import functools
import inspect
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from types import FrameType
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import text as sql_text

from opennem import settings
from opennem.clients.slack import slack_message
from opennem.db import db_connect
from opennem.db.models.opennem import NetworkRegion
from opennem.schema.network import NetworkSchema

# from opennem.utils.timedelta import timedelta_to_string

logger = logging.getLogger("opennem.profiler")


# levels of profiler, and methods and the global used down in the decorator
class ProfilerLevel(enum.Enum):
    """Profiler levels"""

    NOISY = 0
    DEBUG = 1
    INFO = 2
    ESSENTIAL = 3


class ProfilerRetentionTime(enum.Enum):
    """How long to retain profiler record for"""

    FOREVER = "forever"
    MONTH = "month"
    WEEK = "week"
    DAY = "day"


def profiler_level_string_to_enum(level: str) -> ProfilerLevel:
    """Converts a string to a profiler level"""
    if level.upper() == "NOISY":
        return ProfilerLevel.NOISY

    if level.upper == "INFO":
        return ProfilerLevel.INFO

    if level.upper == "ESSENTIAL":
        return ProfilerLevel.ESSENTIAL

    raise Exception("Invalid profiler level")


PROFILE_LEVEL = ProfilerLevel.NOISY


# method used to discover the invokee
def profile_retrieve_caller_name() -> str:
    """Return the calling function's name.

    @NOTE need to step up twice since it's a decorator. Don't use this on
    other generic methods
    """
    # Ref: https://stackoverflow.com/a/57712700/
    return cast(FrameType, cast(FrameType, inspect.currentframe()).f_back.f_back).f_code.co_name  # type: ignore


def get_now() -> datetime:
    """Utility function to get now

    @NOTE add timezone
    """
    return datetime.now().astimezone(ZoneInfo("Australia/Sydney"))


def chop_delta_microseconds(delta: timedelta) -> timedelta:
    """Removes microsevonds from a timedelta"""
    return delta - timedelta(microseconds=delta.microseconds)


def parse_kwargs_value(value: Any) -> str:
    """Parses profiler argument objects. This should be done
    using dunder methods but pydantic has a bug with sub-classes"""
    if isinstance(value, NetworkSchema):
        return f"NetworkSchema({value.code})"

    if isinstance(value, NetworkRegion):
        return f"NetworkRegion({value.code})"

    if hasattr(value, "code"):
        return f"{value.code}"

    return str(value)


def format_args_into_string(args: tuple[str], kwargs: dict[str, str]) -> str:
    """This takes args and kwargs from the profiled method and formats
    them out into a string for logging."""
    args_string = ", ".join([f"'{i}'" for i in args]) if args else ""
    kwargs_string = ", ".join(f"{key}={parse_kwargs_value(value)}" for key, value in kwargs.items())

    # add a separator if both default and named args are present
    if args_string and kwargs_string:
        args_string += ", "

    return f"({args_string}{kwargs_string})"


async def cleanup_database_task_profiles_basedon_retention() -> None:
    """This will clean up the database tasks based on their retention period"""
    engine = db_connect()

    query = """
        delete from task_profile where
            (retention_period = 'day' and now() - interval '1 day' < time_start) or
            (retention_period = 'week' and now() - interval '7 days' < time_start) or
            (retention_period = 'month' and now() - interval '30 days' < time_start)
    """

    async with engine.begin() as conn:
        await conn.execute(sql_text(query))


async def log_task_profile_to_database(
    task_name: str,
    time_start: datetime,
    time_end: datetime,
    invokee_name: str = "",
    level: ProfilerLevel | None = None,
    retention_period: ProfilerRetentionTime | None = None,
) -> uuid.UUID:
    """Log the task profile to the database asynchronously"""

    engine = db_connect()
    id = uuid.uuid4()

    async with engine.begin() as conn:
        try:
            await conn.execute(
                sql_text(
                    """
                        INSERT INTO task_profile (
                            id,
                            task_name,
                            time_start,
                            time_end,
                            errors,
                            retention_period,
                            level,
                            invokee_name
                        ) VALUES (
                            :id,
                            :task_name,
                            :time_start,
                            :time_end,
                            :errors,
                            :retention_period,
                            :level,
                            :invokee_name
                        ) returning id
                        """
                ),
                {
                    "id": id,
                    "task_name": task_name,
                    "time_start": time_start,
                    "time_end": time_end,
                    "errors": 0,
                    "retention_period": retention_period.value if retention_period else "forever",
                    "level": level.value if level else ProfilerLevel.NOISY.value,
                    "invokee_name": invokee_name,
                },
            )
        except Exception as e:
            logger.error(f"Error logging task profile: {e}")

    return id


def profile_task(
    send_slack: bool = False,
    message_fmt: str | None = None,
    level: ProfilerLevel = ProfilerLevel.NOISY,
    retention_period: ProfilerRetentionTime = ProfilerRetentionTime.FOREVER,
) -> Callable:
    """Profile a task and log the time taken to run it

    :param send_slack: Send a slack message with the profile
    :param message_fmt: A custom message format string
    :param level: The level of the profiler
    :retention_period: The retention period of the profiler
    """

    def profile_task_decorator(task: Any, *args: Any, **kwargs: Any) -> Any:
        @functools.wraps(task)
        async def _async_task_profile_wrapper(*args: Any, **kwargs: Any) -> Any:
            """Wrapper for async tasks"""
            logger.info(f"Running async task: {task.__name__}")

            invokee_method_name = profile_retrieve_caller_name()
            logger.info(f"Invoked by: {invokee_method_name}")

            dtime_start = get_now()

            run_task_output = await task(*args, **kwargs)

            dtime_end = get_now()

            if level and level.value < PROFILE_LEVEL.value:
                logger.debug(f"Async task {task.__name__} complete and returning since not level")
                return run_task_output

            wall_clock_time = chop_delta_microseconds(dtime_end - dtime_start)
            wall_clock_time_seconds = wall_clock_time.total_seconds()
            wall_clock_human = (
                f"{int(wall_clock_time_seconds)}s" if wall_clock_time_seconds >= 1 else f"{wall_clock_time_seconds:.2f}s"
            )

            await log_task_profile_to_database(
                task_name=task.__name__,
                time_start=dtime_start,
                time_end=dtime_end,
                retention_period=retention_period,
                level=level,
            )

            profile_message = f"[{settings.env}] `{task.__name__}` in {wall_clock_human}"

            if message_fmt:
                combined_arg_and_env_dict = {**locals(), **kwargs}
                try:
                    custom_message = message_fmt.format(**combined_arg_and_env_dict)
                    profile_message = f"[{settings.env}] {custom_message} in {wall_clock_human}"
                except Exception as e:
                    logger.info(f"Error formatting custom message: {e}")

            if send_slack and settings.slack_hook_monitoring:
                slack_message(
                    webhook_url=settings.slack_hook_monitoring,
                    message=profile_message,
                )

            logger.info(profile_message)

            return run_task_output

        @functools.wraps(task)
        def _sync_task_profile_wrapper(*args: Any, **kwargs: Any) -> Any:
            """Wrapper for synchronous tasks"""
            pass

        if inspect.iscoroutinefunction(task):
            return _async_task_profile_wrapper
        else:
            return _sync_task_profile_wrapper

    return profile_task_decorator


def run_outer_test_task() -> None:
    test_task(message="test inner")


@profile_task(send_slack=True, message_fmt="arg={message}=")
def test_task(message: str | None = None) -> None:
    """Test task"""
    # time.sleep(random.randint(1, ))
    print(f"complete: {message}")


if __name__ == "__main__":
    import asyncio

    @profile_task(send_slack=True, message_fmt="Async arg={message}")
    async def async_test_task(message: str | None = None) -> None:
        """Async test task"""
        await asyncio.sleep(1)
        print(f"Async complete: {message}")

    async def run_async_test():
        await async_test_task(message="async test")

    asyncio.run(run_async_test())
    run_outer_test_task()
