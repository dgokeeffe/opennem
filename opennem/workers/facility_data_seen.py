"""
For each facility calculate the first and last data seen dates from facility_scada

The min dates are run less regularly since data is less likely to grow in that direction
for most recent this is the last active date

@TODO move the utility functions into core/facility use this as only the worker
"""

import logging
from datetime import datetime, timedelta
from textwrap import dedent

from sqlalchemy import TextClause, text

from opennem.db import db_connect, get_database_engine
from opennem.queries.utils import duid_to_case
from opennem.schema.core import BaseConfig
from opennem.utils.dates import get_today_opennem

logger = logging.getLogger("opennem.workers.facility_data_ranges")


def get_update_seen_query(
    include_first_seen: bool = False,
    facility_codes: list[str] | None = None,
    interval_window_days: int | None = 7,
) -> TextClause:
    date_min: datetime | None = None

    if interval_window_days:
        date_min = get_today_opennem().replace(tzinfo=None) - timedelta(days=interval_window_days)

    __query = """
    update units u set
        {fs}data_first_seen = seen_query.data_first_seen,
        data_last_seen = seen_query.data_last_seen
    from (
        select
            fs.facility_code as code,
            {fs}min(fs.interval) as data_first_seen,
            max(fs.interval) as data_last_seen
        from facility_scada fs
        where
            fs.generated > 0
            {facility_codes_query}
            {trading_interval_window}
        group by 1
    ) as seen_query
    where u.code = seen_query.code;
    """

    fs = "" if include_first_seen else "--"
    facility_codes_query = f"and f.code in ({duid_to_case(facility_codes)})" if facility_codes else ""

    trading_interval_window = ""

    if not include_first_seen:
        trading_interval_window = f"and fs.interval > '{date_min}'"

    query = __query.format(fs=fs, facility_codes_query=facility_codes_query, trading_interval_window=trading_interval_window)

    return text(dedent(query))


# @profile_task(send_slack=True)
async def update_facility_seen_range(
    include_first_seen: bool = False,
    facility_codes: list[str] | None = None,
    interval_window_days: int | None = 7,
) -> bool:
    """Updates last seen and first seen. For each facility updates the date the facility
    was seen for the first and last time in the power data from FacilityScada.

    Args:
        include_first_seen (bool, optional): Include earliest seen time. Defaults to False.
        facility_codes (Optional[List[str]], optional): List of facility codes to update. Defaults to None.

    Returns:
        bool: Ran successfuly
    """

    engine = db_connect()

    __query = get_update_seen_query(
        include_first_seen=include_first_seen,
        facility_codes=facility_codes,
        interval_window_days=interval_window_days,
    )

    async with engine.begin() as conn:
        logger.debug(__query)
        await conn.execute(__query)

    return True


class FacilitySeenRange(BaseConfig):
    date_min: datetime | None = None
    date_max: datetime | None = None


def get_facility_seen_range(facility_codes: list[str]) -> FacilitySeenRange:
    """Gets the date range that a facility or list of facilities was seen in SCADA data.


    Args:
        facility_codes (Optional[List[str]], optional): List of facility codes to update. Defaults to None.

    Returns:
        FacilitySeenRange: Schema defining the date range
    """

    __query = """
        select
            min(data_first_seen) as first_seen,
            max(data_last_seen) as last_seen
        from facility
        where code in ({facilities});
    """

    engine = get_database_engine()
    result = []

    query = __query.format(facilities=duid_to_case(facility_codes))

    with engine.begin() as c:
        logger.debug(query)
        result = list(c.execute(query))

    if not result:
        raise Exception("Could not get facility seen range: No results")

    _record = result.pop()

    if len(_record) != 2:
        raise Exception("Coult not get facility seen range: Invalid result or unknown")

    schema = FacilitySeenRange(
        date_min=_record[0],
        date_max=_record[1],
    )

    return schema


if __name__ == "__main__":
    import asyncio

    asyncio.run(update_facility_seen_range(interval_window_days=1))
