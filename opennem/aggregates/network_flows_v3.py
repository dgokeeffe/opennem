"""OpenNEM Network Flows v3

Creates an aggregate table with network flows (imports/exports), emissions
and market_value

"""

import logging
from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy.dialects.postgresql import insert

from opennem.core.flow_solver import (
    FlowSolverResult,
    InterconnectorNetEmissionsEnergy,
    NetworkInterconnectorEnergyEmissions,
    NetworkRegionsDemandEmissions,
    RegionDemandEmissions,
    RegionFlow,
    solve_flow_emissions_for_interval_range,
)
from opennem.core.profiler import ProfilerLevel, ProfilerRetentionTime, profile_task
from opennem.db import get_database_engine, get_scoped_session
from opennem.db.models.opennem import AggregateNetworkFlows
from opennem.schema.network import NetworkNEM, NetworkSchema
from opennem.utils.dates import get_last_completed_interval_for_network

logger = logging.getLogger("opennem.aggregates.flows_v3")


class FlowWorkerException(Exception):
    pass


class FlowsValidationError(Exception):
    pass


def load_interconnector_intervals(
    network: NetworkSchema, interval_start: datetime, interval_end: datetime | None = None
) -> pd.DataFrame:
    """Load interconnector flows for an interval.

    Returns
        pd.DataFrame: DataFrame containing interconnector flows for an interval.


    Example return dataframe:

        trading_interval    interconnector_region_from interconnector_region_to  generated     energy
        2023-04-09 10:15:00                       NSW1                     QLD1 -669.90010 -55.825008
        2023-04-09 10:15:00                       TAS1                     VIC1 -399.80002 -33.316668
        2023-04-09 10:15:00                       VIC1                     NSW1 -261.80997 -21.817498
        2023-04-09 10:15:00                       VIC1                      SA1  412.31787  34.359822
    """
    engine = get_database_engine()

    if not interval_end:
        interval_end = interval_start

    query = f"""
        select
            fs.trading_interval at time zone '{network.timezone_database}' as trading_interval,
            f.interconnector_region_from,
            f.interconnector_region_to,
            coalesce(sum(fs.generated), 0) as generated,
            coalesce(sum(fs.generated) / 12, 0) as energy
        from facility_scada fs
        left join facility f
            on fs.facility_code = f.code
        where
            fs.trading_interval >= '{interval_start}'
            and fs.trading_interval <= '{interval_end}'
            and f.interconnector is True
            and f.network_id = '{network.code}'
        group by 1, 2, 3
        order by
            1 asc;

    """

    logger.debug(query)

    df_gen = pd.read_sql(query, con=engine.raw_connection(), index_col=["trading_interval"])

    if df_gen.empty:
        raise FlowWorkerException("No results from load_interconnector_intervals")

    # convert index to local timezone
    df_gen.index.tz_localize(network.get_fixed_offset(), ambiguous="infer")

    df_gen.reset_index(inplace=True)

    if df_gen.empty:
        raise FlowWorkerException("No results from load_interconnector_intervals")

    return df_gen


def load_energy_and_emissions_for_intervals(
    network: NetworkSchema, interval_start: datetime, interval_end: datetime | None = None
) -> pd.DataFrame:
    """
    Fetch all energy and emissions for each network region for a network.

    Non-inclusive of interval_end.

    Args:
        interval_start (datetime): Start of the interval.
        interval_end (datetime): End of the interval.
        network (NetworkSchema): Network schema object.

    Returns:
        pd.DataFrame: DataFrame containing energy and emissions data for each network region.
            Columns:
                - trading_interval (datetime): Trading interval.
                - network_id (str): Network ID.
                - network_region (str): Network region.
                - energy (float): Sum of energy.
                - emissions (float): Sum of emissions.
                - emission_intensity (float): Emission intensity.

    Raises:
        FlowWorkerException: If no results are obtained from load_interconnector_intervals.

    Example return dataframe:


        trading_interval    network_id network_region      energy   emissions  emissions_intensity
        2023-04-09 10:20:00        NEM           NSW1  468.105472  226.549976             0.483972
        2023-04-09 10:20:00        NEM           QLD1  459.590348  295.124417             0.642147
        2023-04-09 10:20:00        NEM            SA1   36.929530    9.063695             0.245432
        2023-04-09 10:20:00        NEM           TAS1   71.088342    0.000000             0.000000
        2023-04-09 10:20:00        NEM           VIC1  387.120670  236.121274             0.609942
    """

    engine = get_database_engine()

    if not interval_end:
        interval_end = interval_start

    query = f"""
        select
            generated_intervals.trading_interval,
            '{network.code}' as network_id,
            generated_intervals.network_region,
            sum(generated_intervals.generated) as generated,
            sum(generated_intervals.energy) as energy,
            sum(generated_intervals.emissions) as emissions,
            case when sum(generated_intervals.emissions) > 0
                then sum(generated_intervals.emissions) / sum(generated_intervals.energy)
                else 0
            end as emissions_intensity
        from
        (
            select
                fs.trading_interval at time zone 'AEST' as trading_interval,
                f.network_id,
                f.network_region,
                fs.facility_code,
                sum(fs.generated) as generated,
                sum(fs.generated) / 12 as energy,
                sum(fs.generated) / 12 * f.emissions_factor_co2 as emissions
            from facility_scada fs
            left join facility f on fs.facility_code = f.code
            where
                fs.trading_interval >= '{interval_start}'
                and fs.trading_interval <= '{interval_end}'
                and f.network_id IN ('{network.code}', 'AEMO_ROOFTOP', 'OPENNEM_ROOFTOP_BACKFILL')
                and f.fueltech_id not in ('battery_charging')
                and f.interconnector is False
                and fs.generated > 0
            group by fs.trading_interval, fs.facility_code, f.emissions_factor_co2, f.network_region, f.network_id
        ) as generated_intervals
        group by 1, 2, 3
        order by 1 asc;
    """

    logger.debug(query)

    df_gen = pd.read_sql(query, con=engine.raw_connection(), index_col=["trading_interval"])

    if df_gen.empty:
        raise FlowWorkerException("No results from load_interconnector_intervals")

    # convert index to local timezone
    df_gen.index.tz_localize(network.get_fixed_offset(), ambiguous="infer")

    df_gen.reset_index(inplace=True)

    if df_gen.empty:
        raise FlowWorkerException("No results from load_interconnector_intervals")

    return df_gen


def calculate_total_import_and_export_per_region_for_interval(interconnector_data: pd.DataFrame) -> pd.DataFrame:
    """Calculates total import and export energy for a region using the interconnector dataframe

    Args:
        interconnector_data (pd.DataFrame): interconnector dataframe from load_interconnector_intervals

    Returns:
        pd.DataFrame: total imports and export for each region for each interval

    Example return dataframe:

    network_id  network_region  energy_imports  energy_exports
    NEM         NSW1                      82.5             0.0
                QLD1                       0.0            55.0
                SA1                       22.0             0.0
                TAS1                       0.0            11.0
                VIC1                      11.0            49.5
    """

    dx = (
        interconnector_data.groupby(["trading_interval", "interconnector_region_from", "interconnector_region_to"])
        .energy.sum()
        .reset_index()
    )

    # invert regions
    dy = dx.rename(
        columns={
            "interconnector_region_from": "interconnector_region_to",
            "interconnector_region_to": "interconnector_region_from",
            "level_1": "network_region",
        }
    )

    # set indexes
    dy.set_index(["trading_interval", "interconnector_region_to", "interconnector_region_from"], inplace=True)
    dx.set_index(["trading_interval", "interconnector_region_to", "interconnector_region_from"], inplace=True)

    dy["energy"] *= -1

    dx.loc[dx.energy < 0, "energy"] = 0
    dy.loc[dy.energy < 0, "energy"] = 0

    f = pd.concat([dx, dy])

    energy_flows = pd.DataFrame(
        {
            "energy_imports": f.groupby(["trading_interval", "interconnector_region_to"]).energy.sum(),
            "energy_exports": f.groupby(["trading_interval", "interconnector_region_from"]).energy.sum(),
            "generated_imports": f.groupby(["trading_interval", "interconnector_region_to"]).energy.sum() * 12,
            "generated_exports": f.groupby(["trading_interval", "interconnector_region_from"]).energy.sum() * 12,
        }
    )

    # imports sum should equal exports sum always
    if not round(energy_flows.energy_exports.sum(), 0) == round(energy_flows.energy_imports.sum(), 0):
        raise FlowWorkerException(
            f"Energy import and export totals do not match: {energy_flows.energy_exports.sum()} and {energy_flows.energy_imports.sum()}"
        )

    energy_flows["network_id"] = "NEM"

    energy_flows.reset_index(inplace=True)
    energy_flows.rename(columns={"index": "network_region"}, inplace=True)
    # energy_flows.set_index(["network_id", "network_region"], inplace=True)

    return energy_flows


def invert_interconnectors_invert_all_flows(interconnector_data: pd.DataFrame) -> pd.DataFrame:
    """Inverts the flows per interconnector to show net values"""
    original_set = interconnector_data.copy()

    inverted_set = interconnector_data.copy().rename(
        columns={
            "interconnector_region_from": "interconnector_region_to",
            "interconnector_region_to": "interconnector_region_from",
        }
    )

    inverted_set["generated"] *= -1
    inverted_set["energy"] *= -1

    original_set.loc[original_set.energy <= 0, "generated"] = 0
    inverted_set.loc[inverted_set.energy <= 0, "generated"] = 0
    original_set.loc[original_set.energy <= 0, "energy"] = 0
    inverted_set.loc[inverted_set.energy <= 0, "energy"] = 0

    result = pd.concat([original_set, inverted_set])

    # result = result.sort_values("interconnector_region_from")

    return result


def calculate_demand_region_for_interval(energy_and_emissions: pd.DataFrame, imports_and_export: pd.DataFrame) -> pd.DataFrame:
    """
    Takes energy and emissions and imports and exports and calculates demand for each region and adds it to a merged
    total dataframe
    """

    df_with_demand = pd.merge(energy_and_emissions, imports_and_export)
    df_with_demand["demand"] = df_with_demand["energy"]
    # - df_with_demand["energy_exports"]

    # add emissions intensity for debugging
    df_with_demand["emissions_intensity"] = df_with_demand["emissions"] / df_with_demand["demand"]

    return df_with_demand


def persist_network_flows_and_emissions_for_interval(network: NetworkSchema, flow_results: pd.DataFrame) -> int:
    """persists the records to at_network_flows"""
    session = get_scoped_session()
    engine = get_database_engine()

    records_to_store = flow_results.to_dict(orient="records")

    for rec in records_to_store:
        rec["network_id"] = network.code
        rec["trading_interval"] = rec["trading_interval"].replace(tzinfo=network.get_fixed_offset())

    # insert
    stmt = insert(AggregateNetworkFlows).values(records_to_store)
    stmt.bind = engine
    stmt = stmt.on_conflict_do_update(
        index_elements=["trading_interval", "network_id", "network_region"],
        set_={
            "energy_imports": stmt.excluded.energy_imports,
            "energy_exports": stmt.excluded.energy_exports,
            "emissions_exports": stmt.excluded.emissions_exports,
            "emissions_imports": stmt.excluded.emissions_imports,
            "market_value_exports": stmt.excluded.market_value_exports,
            "market_value_imports": stmt.excluded.market_value_imports,
        },
    )

    try:
        session.execute(stmt)
        session.commit()
    except Exception as e:
        logger.error("Error inserting records")
        raise e
    finally:
        session.rollback()
        session.close()
        engine.dispose()

    return len(records_to_store)


def convert_dataframes_to_interconnector_format(
    interconnector_df: pd.DataFrame, network: NetworkSchema
) -> NetworkInterconnectorEnergyEmissions:
    """Converts pandas data frame of interconnector flows to a format that can be used by the flow solver"""
    records = [
        InterconnectorNetEmissionsEnergy(
            interval=rec["trading_interval"].to_pydatetime().replace(tzinfo=network.get_fixed_offset()),
            region_flow=RegionFlow(f"{rec['interconnector_region_from']}->{rec['interconnector_region_to']}"),
            generated_mw=rec["generated"],
            energy_mwh=rec["energy"],
        )
        for rec in interconnector_df.to_dict(orient="records")
    ]

    return NetworkInterconnectorEnergyEmissions(network=NetworkNEM, data=records)


def convert_dataframe_to_energy_and_emissions_format(
    region_demand_emissions_df: pd.DataFrame, network: NetworkSchema
) -> NetworkRegionsDemandEmissions:
    """Converts pandas data frame of region demand and emissions to a format that can be used by the flow solver"""
    records = [
        RegionDemandEmissions(
            interval=rec["trading_interval"].to_pydatetime().replace(tzinfo=network.get_fixed_offset()),
            region_code=rec["network_region"],
            emissions_t=rec["emissions"],
            energy_mwh=rec["demand"],
        )
        for rec in region_demand_emissions_df.to_dict(orient="records")
    ]

    return NetworkRegionsDemandEmissions(network=NetworkNEM, data=records)


def shape_flow_results_into_records_for_persistance(
    network: NetworkSchema,
    interconnector_data: pd.DataFrame,
    interconnector_emissions: FlowSolverResult,
) -> pd.DataFrame:
    """shape into import/exports energy and emissions for each region for a given interval"""

    flow_emissions_df = interconnector_emissions.to_dataframe()

    flows_with_emissions = interconnector_data.reset_index().merge(
        flow_emissions_df, on=["trading_interval", "interconnector_region_from", "interconnector_region_to"]
    )

    merged_df = pd.DataFrame(
        {
            "energy_exports": flows_with_emissions.groupby(["trading_interval", "interconnector_region_from"]).energy.sum(),
            "energy_imports": flows_with_emissions.groupby(["trading_interval", "interconnector_region_to"]).energy.sum(),
            "emissions_exports": flows_with_emissions.groupby(["trading_interval", "interconnector_region_from"]).emissions.sum(),
            "emissions_imports": flows_with_emissions.groupby(["trading_interval", "interconnector_region_to"]).emissions.sum(),
        }
    )

    # @TODO merge market value
    merged_df["market_value_exports"] = 0.0
    merged_df["market_value_imports"] = 0.0

    merged_df["network_id"] = network.code

    merged_df.fillna(0, inplace=True)

    merged_df.reset_index(inplace=True)
    merged_df.rename(columns={"index": "trading_interval", "level_1": "network_region"}, inplace=True)

    return merged_df


@profile_task(
    send_slack=False,
    message_fmt="Running flow v3 for {interval_number} intervals",
    level=ProfilerLevel.INFO,
    retention_period=ProfilerRetentionTime.FOREVER,
)
def run_flows_for_last_intervals(interval_number: int, network: NetworkSchema) -> None:
    """ " Run flow processor for last x interval starting from now"""

    logger.info(f"Running flows for last {interval_number} intervals")

    if not network:
        network = NetworkNEM

    first_interval = get_last_completed_interval_for_network(network=network)

    for interval in [first_interval - timedelta(minutes=5 * i) for i in range(1, interval_number + 1)]:
        logger.debug(f"Running flow for interval {interval}")
        run_aggregate_flow_for_interval_v3(interval_start=interval, network=network, validate_results=False)


@profile_task(
    send_slack=False,
    message_fmt="Running flow v3 for {days} days",
    level=ProfilerLevel.INFO,
    retention_period=ProfilerRetentionTime.FOREVER,
)
def run_flows_for_last_days(days: int, network: NetworkSchema, start_date: datetime | None = None) -> None:
    """ " Run flow processor for last x interval starting from now"""

    logger.info(f"Running flows for last {days}")

    latest_interval = get_last_completed_interval_for_network(network=network)
    series_start_date = datetime.now(NetworkNEM.get_fixed_offset()).replace(hour=0, minute=0, second=0, microsecond=0)

    if start_date:
        series_start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

    series_end_date = series_start_date - timedelta(days=days)

    for day_num in range(0, (series_start_date - series_end_date).days):
        day = series_start_date - timedelta(days=day_num)
        interval_start = day
        interval_end = interval_start + timedelta(days=1) - timedelta(minutes=5)

        if day_num == 0 and not start_date:
            interval_end = latest_interval

        logger.debug(f"Running flow for day {day} from {interval_start} to {interval_end}")
        run_aggregate_flow_for_interval_v3(
            network=network, interval_start=interval_start, interval_end=interval_end, validate_results=True
        )


def validate_network_flows(flow_records: pd.DataFrame, raise_exception: bool = True) -> None:
    """Validate network flows and sanity checking"""
    # 1. Check values are positive
    validate_fields = ["energy_exports", "energy_imports", "emissions_exports", "emissions_imports"]

    for field in validate_fields:
        bad_values = flow_records.query(f"{field} < 0")

        if not bad_values.empty:
            for rec in bad_values.to_dict(orient="records"):
                raise FlowsValidationError(f"Bad value: {rec['trading_interval']} {rec['network_region']} {field} {rec[field]}")

    # 2. Check emission factors
    flow_records_validation = flow_records.copy()

    flow_records_validation.loc[flow_records_validation["energy_exports"] > 0, "exports_emission_factor"] = (
        flow_records_validation["emissions_exports"] / flow_records_validation["energy_exports"]
    )
    flow_records_validation.loc[flow_records_validation["energy_imports"] > 0, "imports_emission_factor"] = (
        flow_records_validation["emissions_imports"] / flow_records_validation["energy_imports"]
    )

    bad_factors_exports = flow_records_validation.query("exports_emission_factor > 1.7 or exports_emission_factor < 0")
    bad_factors_imports = flow_records_validation.query("imports_emission_factor > 1.7 or imports_emission_factor < 0")

    if not bad_factors_exports.empty:
        for rec in bad_factors_exports.to_dict(orient="records"):
            bad_factor_message = (
                f"Bad exports emission factor: {rec['trading_interval']} {rec['network_region']} {rec['exports_emission_factor']}"
            )
            logger.error(bad_factor_message)

            if raise_exception:
                raise FlowsValidationError(bad_factor_message)

    if not bad_factors_imports.empty:
        for rec in bad_factors_imports.to_dict(orient="records"):
            bad_factor_message = (
                f"Bad imports emission factor: {rec['trading_interval']} {rec['network_region']} {rec['imports_emission_factor']}"
            )
            logger.error(bad_factor_message)

            if raise_exception:
                raise FlowsValidationError(bad_factor_message)

    return None


@profile_task(
    send_slack=True,
    message_fmt="Running aggregate flow v3 for interval {interval_start} for {network.code}",
    level=ProfilerLevel.INFO,
    retention_period=ProfilerRetentionTime.FOREVER,
)
def run_aggregate_flow_for_interval_v3(
    network: NetworkSchema, interval_start: datetime, interval_end: datetime | None = None, validate_results: bool = True
) -> int:
    """This method runs the aggregate for an interval and for a network using flow solver

    This is version 3 of the method and sits behind the settings.network_flows_v3 feature flag

    Args:
        interval (datetime): _description_
        network (NetworkSchema): _description_
    """
    # 0. support single interval
    if not interval_end:
        interval_end = interval_start

    # If intervals are inverted set them back the right way around
    if interval_start > interval_end:
        interval_start, interval_end = interval_end, interval_start

    # 1. get
    try:
        energy_and_emissions = load_energy_and_emissions_for_intervals(
            network=network, interval_start=interval_start, interval_end=interval_end
        )
    except Exception as e:
        logger.error(e)
        return 0

    # 2. get interconnector data and calculate region imports/exports net
    try:
        interconnector_data = load_interconnector_intervals(
            network=network, interval_start=interval_start, interval_end=interval_end
        )
    except Exception as e:
        logger.error(e)
        return 0

    interconnector_data_net = invert_interconnectors_invert_all_flows(interconnector_data)

    region_imports_and_exports = calculate_total_import_and_export_per_region_for_interval(
        interconnector_data=interconnector_data
    )

    # 3. calculate demand for each region and add it to the dataframe
    region_net_demand = calculate_demand_region_for_interval(
        energy_and_emissions=energy_and_emissions, imports_and_export=region_imports_and_exports
    )

    # 4. convert to format for solver
    interconnector_data_for_solver = convert_dataframes_to_interconnector_format(
        interconnector_df=interconnector_data_net, network=network
    )
    region_data_for_solver = convert_dataframe_to_energy_and_emissions_format(
        region_demand_emissions_df=region_net_demand, network=network
    )

    # 5. Solve.

    # interconnector_emissions = solve_flow_emissions_for_interval(
    #     network=network,
    #     interval=interval,
    #     interconnector_data=interconnector_data_for_solver,
    #     region_data=region_data_for_solver,
    # )

    interconnector_emissions = solve_flow_emissions_for_interval_range(
        network=network,
        interconnector_data=interconnector_data_for_solver,
        region_data=region_data_for_solver,
    )

    # 6. merge results into final records for the interval inserted into at_network_flows
    network_flow_records = shape_flow_results_into_records_for_persistance(
        network=network,
        interconnector_data=interconnector_data_net,
        interconnector_emissions=interconnector_emissions,
    )

    # 7. Validate flows - this will throw errors on bad values
    if validate_results:
        validate_network_flows(flow_records=network_flow_records)

    # 7. Persist to database aggregate table
    inserted_records = persist_network_flows_and_emissions_for_interval(network=network, flow_results=network_flow_records)

    logger.info(
        f"{network.code} flow records: Inserted {inserted_records} records for interval {interval_start} => {interval_end}"
    )

    return inserted_records


def run_aggregate_flows_per_interval_block(date_start: datetime, date_end: datetime, network: NetworkSchema) -> None:
    """Run aggregate flows in interval (7 day) blocks

    This works **backwards** from the start date to the end date, calculating aggregate flows in 7 day blocks.

    NOTE: If an aggregate calculation fails it will continue to the next block without raising an exception.
    This can leave gaps in the flow data.

    Args:
        date_start (datetime): Start date to work backwards from
        date_end (datetime): End date to calculate aggregate flows to. Must be **earlier** than date_start.
        network (NetworkSchema): Network schema object
    """
    interval_start = date_start
    interval_end = date_start - timedelta(days=1)

    while interval_start > date_end:
        logger.info(f"Running aggregate flows for interval {interval_start} to {interval_end}")

        try:
            run_aggregate_flow_for_interval_v3(
                network=network, interval_start=interval_start, interval_end=interval_end, validate_results=True
            )
        except Exception as e:
            logger.error(f"Error running aggregate flows for interval {interval_start} to {interval_end}: {e}")

        interval_start = interval_end
        interval_end -= timedelta(days=7)

        if interval_end < date_end:
            interval_end = date_end

    logger.info("Completed.")


def run_flow_updates_all_for_network_v3(network: NetworkSchema) -> None:
    """Run the entire emissions flow_v3 for a network

    Args:
        network (NetworkSchema): Network schema object

    Raises:
        FlowWorkerException: Errors if it can't find date of first seen data for the network
    """
    current_date = datetime.now().astimezone(tz=network.get_fixed_offset())

    if not network.data_first_seen:
        raise FlowWorkerException(f"No data first seen for network {network.code}")

    # Calculate flows in 7-day blocks
    # This is very confusing because the date_start is the end of the block - it works backwards...
    run_aggregate_flows_per_interval_block(
        date_end=network.data_first_seen,
        date_start=current_date,
        network=network,
    )


# debug entry point
if __name__ == "__main__":
    # run_flows_for_last_intervals(interval_number=24 * 12, network=NetworkNEM)

    interval_start = datetime.fromisoformat("2011-01-10T00:00:00+10:00")
    interval_end = datetime.fromisoformat("2009-06-01T00:00:00+10:00")
    run_aggregate_flows_per_interval_block(
        network=NetworkNEM,
        date_start=interval_start,
        date_end=interval_end,
    )
