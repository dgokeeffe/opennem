"""
Runs daily export task JSONs for OpenNEM website
"""

import logging
from datetime import datetime, timedelta

from opennem import settings
from opennem.aggregates.facility_daily import run_aggregate_facility_all_by_year, run_aggregates_facility_year
from opennem.aggregates.network_demand import run_aggregates_demand_network
from opennem.aggregates.network_flows import (
    run_emission_update_day,
    run_flow_updates_all_for_network,
    run_flow_updates_all_per_year,
)
from opennem.aggregates.network_flows_v3 import run_aggregate_flow_for_interval_v3, run_flow_updates_all_for_network_v3
from opennem.api.export.map import PriorityType, StatType, get_export_map
from opennem.api.export.tasks import export_all_daily, export_all_monthly, export_energy, export_power
from opennem.clients.slack import slack_message
from opennem.core.profiler import profile_task
from opennem.exporter.historic import export_historic_intervals
from opennem.schema.network import NetworkAEMORooftop, NetworkAPVI, NetworkNEM, NetworkOpenNEMRooftopBackfill, NetworkWEM
from opennem.utils.dates import get_last_completed_interval_for_network

logger = logging.getLogger("opennem.worker.daily")


def run_export_for_year(year: int, network_region_code: str | None = None) -> None:
    """Run export for latest year"""
    export_map = get_export_map()
    energy_exports = export_map.get_by_stat_type(StatType.energy).get_by_priority(PriorityType.daily).get_by_year(year)

    if network_region_code:
        energy_exports = energy_exports.get_by_network_region(network_region_code)

    logger.info(f"Running {len(energy_exports.resources)} exports")

    export_energy(energy_exports.resources)


# The actual daily runners


def daily_catchup_runner(days: int = 2) -> None:
    """Daily task runner - runs after success of overnight crawls"""
    current_year = datetime.now().year

    # if it is the first day of the year run the previous year
    if datetime.now().month == 1 and datetime.now().day == 1:
        current_year = current_year - 1

    # aggregates
    # 1. flows
    if settings.flows_and_emissions_v3:
        # run for days
        interval_end = get_last_completed_interval_for_network(network=NetworkNEM)
        interval_start = interval_end - timedelta(days=days, hours=12)
        run_aggregate_flow_for_interval_v3(
            network=NetworkNEM,
            interval_start=interval_start,
            interval_end=interval_end,
        )
    else:
        run_flow_updates_all_per_year(current_year, 1)

    # 2. facilities
    for network in [NetworkNEM, NetworkWEM, NetworkAEMORooftop, NetworkAPVI]:
        run_aggregates_facility_year(year=current_year, network=network)

    # 3. network demand
    run_aggregates_demand_network()

    #  flows and flow emissions
    if not settings.flows_and_emissions_v3:
        run_emission_update_day(days=days)

    # 4. Run Exports
    #  run exports for latest year
    export_energy(latest=True)

    #  run exports for last year
    run_export_for_year(current_year - 1)

    # run exports for all
    export_map = get_export_map()
    energy_exports = export_map.get_by_stat_type(StatType.energy).get_by_priority(PriorityType.monthly)
    export_energy(energy_exports.resources)

    export_all_daily()
    export_all_monthly()

    # export historic intervals
    for network in [NetworkNEM, NetworkWEM]:
        export_historic_intervals(limit=2, networks=[network])


@profile_task(send_slack=False)
def daily_runner(days: int = 2) -> None:
    """Daily task runner - runs after success of overnight crawls"""
    current_year = datetime.now().year

    # if it is the first day of the year run the previous year
    if datetime.now().month == 1 and datetime.now().day == 1:
        current_year = current_year - 1

    # aggregates
    # 1. flows
    if settings.flows_and_emissions_v3:
        # run for days
        interval_end = get_last_completed_interval_for_network(network=NetworkNEM)
        interval_start = interval_end - timedelta(days=days, hours=12)
        run_aggregate_flow_for_interval_v3(
            network=NetworkNEM,
            interval_start=interval_start,
            interval_end=interval_end,
        )
    else:
        run_flow_updates_all_per_year(current_year, 1)

    # 2. facilities
    for network in [NetworkNEM, NetworkWEM, NetworkAEMORooftop, NetworkAPVI]:
        run_aggregates_facility_year(year=current_year, network=network)

    # 3. network demand
    run_aggregates_demand_network()

    #  flows and flow emissions
    if not settings.flows_and_emissions_v3:
        run_emission_update_day(days=days)

    # 4. Run Exports
    #  run exports for latest year
    export_energy(latest=True)

    #  run exports for last year
    run_export_for_year(current_year - 1)

    # run exports for all
    export_map = get_export_map()
    energy_exports = export_map.get_by_stat_type(StatType.energy).get_by_priority(PriorityType.monthly)
    export_energy(energy_exports.resources)

    export_all_daily()
    export_all_monthly()

    # export historic intervals
    for network in [NetworkNEM, NetworkWEM]:
        export_historic_intervals(limit=2, networks=[network])


def all_runner() -> None:
    """Like the daily runner but refreshes all tasks"""

    # populates the aggregate tables
    if settings.flows_and_emissions_v3:
        run_flow_updates_all_for_network_v3(network=NetworkNEM)
    else:
        run_flow_updates_all_for_network(network=NetworkNEM)

    for network in [NetworkNEM, NetworkAEMORooftop, NetworkAPVI, NetworkWEM, NetworkOpenNEMRooftopBackfill]:
        run_aggregate_facility_all_by_year(network=network)

    # run the exports for all
    export_power(latest=False)
    export_energy(latest=False)

    export_all_daily()
    export_all_monthly()

    # send slack message when done
    slack_message(f"ran all_runner on {settings.env}")


if __name__ == "__main__":
    # daily_runner(days=2)
    daily_runner()
