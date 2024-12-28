"""
RecordReactor engine
"""

import logging
from datetime import datetime, timedelta

import tqdm

from opennem import settings
from opennem.recordreactor.buckets import get_period_start_end, is_end_of_period
from opennem.recordreactor.processors.demand import run_price_demand_milestone_for_interval
from opennem.recordreactor.processors.energy import run_energy_emissions_milestones
from opennem.recordreactor.processors.power import run_power_milestones, run_renewable_power_milestones
from opennem.recordreactor.processors.renewable_proportion import run_renewable_proportion_milestones
from opennem.recordreactor.schema import MilestonePeriod, MilestoneType
from opennem.schema.network import NetworkNEM, NetworkSchema, NetworkWEM
from opennem.utils.dates import get_last_completed_interval_for_network

logger = logging.getLogger("opennem.recordreactor.engine")

# Engine configs

_DEFAULT_METRICS = [
    MilestoneType.demand,
    MilestoneType.price,
    MilestoneType.power,
    MilestoneType.energy,
    MilestoneType.emissions,
    MilestoneType.proportion,
]

_DEFAULT_BUCKET_SIZES = [
    MilestonePeriod.interval,
    MilestonePeriod.day,
    # MilestonePeriod.week,
    MilestonePeriod.month,
    MilestonePeriod.quarter,
    # MilestonePeriod.season,
    MilestonePeriod.year,
    MilestonePeriod.financial_year,
]

_DEFAULT_NETWORKS = [NetworkNEM, NetworkWEM]


async def run_milestone_engine(
    start_interval: datetime,
    end_interval: datetime | None = None,
    metrics: list[MilestoneType] | None = None,
    networks: list[NetworkSchema] | None = None,
    periods: list[MilestonePeriod] | None = None,
    bulk_insert: bool = False,
    bulk_insert_batch_size: int = 100,
):
    # normalise start and end intervals with no timezone info (ie. make unaware)
    start_interval = start_interval.replace(tzinfo=None)

    if end_interval:
        end_interval = end_interval.replace(tzinfo=None)

    if not metrics:
        metrics = _DEFAULT_METRICS

    if not networks:
        networks = _DEFAULT_NETWORKS

    if not periods:
        periods = _DEFAULT_BUCKET_SIZES

    logger.info(
        f"Running milestone engine for {networks}, {len(periods)} periods and"
        f" {len(metrics)} metrics from {start_interval} to {end_interval}"
    )

    for network in networks:
        if not network.interval_size:
            logger.info(f"Skipping {network.code} as it has no interval size")
            continue

        if not network.data_first_seen:
            logger.info(f"Skipping {network.code} as it has no data first seen")
            continue

        logger.info(f"Processing milestone data network {network.code}")

        if not end_interval:
            end_interval = get_last_completed_interval_for_network(network)
            logger.info(f"Set end interval for {network.code} to {end_interval}")

        # how many intervals we process at a time
        interval_leap_size = network.interval_size

        current_interval = start_interval

        total_intervals = (end_interval - start_interval).total_seconds() / 60 / interval_leap_size

        with tqdm.tqdm(total=total_intervals, unit="interval") as progress_bar:
            while current_interval <= end_interval:
                # don't process the network data if it is after the last data seen
                if network.data_last_seen and current_interval > network.data_last_seen.replace(tzinfo=None):
                    logger.info(f"Breaking at {current_interval} as it is after the last data seen for {network.code}")
                    break

                # don't process the network data if it is before the first data seen
                if current_interval < network.data_first_seen.replace(tzinfo=None):
                    logger.info(f"Skipping {current_interval} as it is before the first data seen for {network.code}")
                    current_interval += timedelta(minutes=network.interval_size)
                    continue

                tasks = []  # Move this inside the loop

                for bucket_size in periods:
                    if not is_end_of_period(current_interval, bucket_size):
                        continue

                    period_start, period_end = get_period_start_end(dt=current_interval, bucket_size=bucket_size, network=network)

                    if settings.dry_run:
                        continue

                    if MilestoneType.power in metrics:
                        tasks.append(
                            run_power_milestones(
                                network=network,
                                bucket_size=bucket_size,
                                period_start=period_start,
                                period_end=period_end,
                            )
                        )

                        tasks.append(
                            run_renewable_power_milestones(
                                network=network,
                                bucket_size=bucket_size,
                                period_start=period_start,
                                period_end=period_end,
                            )
                        )

                    if MilestoneType.demand in metrics or MilestoneType.price in metrics:
                        tasks.append(
                            run_price_demand_milestone_for_interval(
                                network=network,
                                bucket_size=bucket_size,
                                interval=period_start,
                            )
                        )

                    if MilestoneType.energy in metrics or MilestoneType.emissions in metrics:
                        tasks.append(
                            run_energy_emissions_milestones(
                                network=network,
                                bucket_size=bucket_size,
                                period_start=period_start,
                                period_end=period_end,
                            )
                        )

                    if MilestoneType.proportion in metrics:
                        tasks.append(
                            run_renewable_proportion_milestones(
                                network=network,
                                bucket_size=bucket_size,
                                start_date=period_start,
                                end_date=period_end,
                            )
                        )

                # Move to the next interval and pad it out
                current_interval += timedelta(minutes=interval_leap_size)

                if tasks:
                    await asyncio.gather(*tasks)  # Await tasks for each interval

                progress_bar.update(1)

        # Remove the final gather outside the loop, as it's no longer needed


# debug entry point
if __name__ == "__main__":
    import asyncio

    nem_start = datetime.fromisoformat("1998-12-08 00:00:00")
    # start_interval = datetime.fromisoformat("1999-03-26 04:55:00")
    # test_start_interval = datetime.fromisoformat("2010-01-01 00:00:00")

    # Test entry point
    start_interval = datetime.fromisoformat("2024-12-16 00:00:00")
    end_interval = get_last_completed_interval_for_network(network=NetworkNEM)
    asyncio.run(
        run_milestone_engine(
            start_interval=start_interval,
            end_interval=end_interval,
            # metrics=[MilestoneType.proportion],
            # periods=[
            #     # MilestonePeriod.interval,
            #     MilestonePeriod.day,
            #     MilestonePeriod.week_rolling,
            #     MilestonePeriod.month,
            #     # MilestonePeriod.quarter,
            #     MilestonePeriod.year,
            # ],
            # networks=[NetworkNEM],
        )
    )
