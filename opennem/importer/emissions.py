import csv
import logging

from opennem.core.loader import load_data
from opennem.core.normalizers import clean_float, normalize_duid
from opennem.db import SessionLocal
from opennem.db.models.opennem import Unit
from opennem.importer.mms import mms_import

logger = logging.getLogger(__name__)


def import_mms_emissions() -> None:
    mms = mms_import()

    facility_poll_map = {
        # from wem
        "KWINANA_C5": 0.877,
    }

    emission_map = []

    for station in mms:
        for facility in station.facilities:
            emission_factor = facility.emissions_factor_co2
            factor_source = "AEMO"

            if not emission_factor and facility.code in facility_poll_map.keys():
                emission_factor = facility_poll_map[facility.code]
                factor_source = "OpenNEM"

            emission_map.append(
                {
                    "station_name": station.name,
                    "network_id": "NEM",
                    "network_region": facility.network_region,
                    "facility_code": facility.code,
                    "emissions_factor_co2": facility.emissions_factor_co2,
                    "fueltech_id": "",
                    "emission_factor_source": factor_source,
                }
            )

    return emission_map


def import_dump_emissions() -> list[dict]:
    content = load_data("emissions_output.csv", from_project=True, skip_loaders=True)

    csv_content = content.splitlines()
    csvreader = csv.DictReader(csv_content)
    records = []

    for rec in csvreader:
        records.append(
            {
                "facility_code": rec["DUID"],
                "emissions_factor_co2": clean_float(rec["CO2E_EMISSIONS_FACTOR"]),
            }
        )

    return records


def import_emissions_csv() -> None:
    EMISSION_MAPS = [
        {"filename": "emission_factors.csv", "network": "WEM"},
        # {"filename": "nem_emissions.csv", "network": "NEM"},
    ]

    for m in EMISSION_MAPS:
        import_emissions_map(m["filename"])


def import_emissions_map(file_name: str) -> None:
    """Import emission factors from CSV files for each network
    the format of the csv file is

    station_name,network_id,network_region,facility_code,emissions_factor_co2,fueltech_id,emission_factor_source
    """
    session = SessionLocal()

    content = load_data(file_name, from_project=True, skip_loaders=True)

    csv_content = content.splitlines()
    csvreader = csv.DictReader(csv_content)

    for rec in csvreader:
        network_id = rec["network_id"]
        facility_code = normalize_duid(rec["facility_code"])
        emissions_intensity = clean_float(rec["emissions_factor_co2"])

        if not facility_code:
            logger.info(f"No emissions intensity for {facility_code}")
            continue

        facility = session.query(Unit).filter_by(code=facility_code).filter_by(network_id=network_id).one_or_none()

        if not facility:
            logger.info(f"No stored facility for {facility_code}")
            continue

        facility.emissions_factor_co2 = emissions_intensity
        session.add(facility)
        logger.info(f"Updated {facility_code} to {emissions_intensity}")

    session.commit()

    return None


def check_emissions_map() -> None:
    content = load_data("emission_factors.csv", from_project=True, skip_loaders=True)
    mms_emissions = import_dump_emissions()

    def get_emissions_for_code(facility_code: str) -> dict | None:
        facility_lookup = list(filter(lambda x: x["facility_code"] == facility_code, mms_emissions))

        if not facility_lookup or len(facility_lookup) < 1:
            logger.error(f"Could not find facility {facility_code} in MMS emmissions data")
            return None

        facility = facility_lookup.pop()
        return facility

    csv_content = content.splitlines()
    csvreader = csv.DictReader(csv_content)

    csv_out = []

    for rec in csvreader:
        network_id = rec["network_id"]
        facility_code = normalize_duid(rec["facility_code"])
        emissions_intensity = clean_float(rec["emissions_factor_co2"])

        if network_id != "NEM":
            csv_out.append(rec)

            continue

        mms_emission_record = get_emissions_for_code(facility_code)

        if not mms_emission_record:
            csv_out.append(rec)
            continue

        if emissions_intensity != mms_emission_record["emissions_factor_co2"]:
            logger.error(
                "Mismatch for {}: {} and {}".format(
                    facility_code, emissions_intensity, mms_emission_record["emissions_factor_co2"]
                )
            )

            if mms_emission_record["emissions_factor_co2"]:
                rec["emissions_factor_co2"] = mms_emission_record["emissions_factor_co2"]

        csv_out.append(rec)

    fieldnames = [
        "network_id",
        "network_region",
        "facility_code",
        "station_name",
        "fueltech_id",
        "status_id",
        "emissions_factor_co2",
        "emission_factor_source",
    ]

    with open("emission_factors2.csv", "w") as fh:
        csvwriter = csv.DictWriter(fh, fieldnames=fieldnames)
        csvwriter.writeheader()
        csvwriter.writerows(csv_out)


if __name__ == "__main__":
    check_emissions_map()
