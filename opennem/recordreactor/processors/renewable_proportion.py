"""
This module contains the logic for the renewable proportion milestone.
"""

import logging
from datetime import datetime

from opennem.queries.renewable_proportion import get_renewable_energy_proportion
from opennem.recordreactor.persistence import check_and_persist_milestones
from opennem.recordreactor.schema import (
    MilestoneAggregate,
    MilestoneFueltechGrouping,
    MilestonePeriod,
    MilestoneRecordSchema,
    MilestoneType,
)
from opennem.recordreactor.unit import get_milestone_unit
from opennem.schema.network import NetworkSchema

logger = logging.getLogger("opennem.recordreactor.processors.renewable_proportion")


async def _aggregate_renewable_proportion_data(
    network: NetworkSchema,
    bucket_size: MilestonePeriod,
    start_date: datetime,
    end_date: datetime,
    group_by_region: bool,
    group_by_fueltech: bool = False,
    group_by_renewable: bool = False,
) -> list[MilestoneRecordSchema]:
    """
    Aggregate the renewable proportion data for a given network and date range.
    """
    results = await get_renewable_energy_proportion(
        network=network,
        bucket_size=bucket_size,
        start_date=start_date,
        end_date=end_date,
        group_by_region=group_by_region,
        group_by_fueltech=group_by_fueltech,
        group_by_renewable=group_by_renewable,
    )

    milestone_records: list[MilestoneRecordSchema] = []

    for row in results:
        for aggregate in [MilestoneAggregate.low, MilestoneAggregate.high]:
            milestone_records.append(
                MilestoneRecordSchema(
                    interval=row["interval"],
                    aggregate=aggregate,
                    metric=MilestoneType.proportion,
                    period=bucket_size,
                    network=network,
                    unit=get_milestone_unit(MilestoneType.proportion),
                    value=row["proportion"],
                    network_region=row["network_region"] if row["network_region"] else None,
                    fueltech=MilestoneFueltechGrouping(row["fueltech_id"]) if row["fueltech_id"] else None,
                )
            )

    return milestone_records


async def run_renewable_proportion_milestones(
    network: NetworkSchema, bucket_size: MilestonePeriod, start_date: datetime, end_date: datetime
):
    """
    Run the renewable proportion milestone for a given network and date range.
    """

    # only run on interval, day and week rolling for now
    if bucket_size not in [
        MilestonePeriod.interval,
        MilestonePeriod.day,
        MilestonePeriod.week_rolling,
        MilestonePeriod.month,
        MilestonePeriod.year,
    ]:
        return

    for group_by_region in [True, False]:
        milestone_data = await _aggregate_renewable_proportion_data(
            network=network,
            bucket_size=bucket_size,
            start_date=start_date,
            end_date=end_date,
            group_by_region=group_by_region,
            group_by_fueltech=False,
            group_by_renewable=True,
        )

        await check_and_persist_milestones(
            milestones=milestone_data,
        )
