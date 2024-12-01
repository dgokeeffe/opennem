# pylint: disable=no-self-argument
"""OpenNEM WEMDE Client

Four sources:

 * balancing summary live data (from the infographic feeds)
 * live facility generation data
 * nemweb generation data (usually delayed 3-4 days)

See the URL constants for sources and unit tests
"""

import logging
from datetime import datetime

from pydantic import ValidationError

from opennem.core.battery import get_battery_unit_map
from opennem.persistence.schema import BalancingSummarySchema, FacilityScadaSchema
from opennem.utils.archive import download_and_parse_json_zip

logger = logging.getLogger("opennem.client.wemde")

# Old URL
# _AEMO_WEM_LIVE_SCADA_URL = "https://aemo.com.au/aemo/data/wa/infographic/facility-intervals-last96.csv"


# Exceptions
class WEMDEDownloadException(Exception):
    pass


def _wemde_download_dataset(url: str) -> dict:
    try:
        json_dict = download_and_parse_json_zip(url)

        if "data" not in json_dict:
            raise Exception("No data in JSON")

        json_response = json_dict["data"]

    except Exception as e:
        raise WEMDEDownloadException(f"Error downloading WEMDE dataset: {e}") from e

    return json_response


async def wemde_parse_facilityscada(url: str) -> list[FacilityScadaSchema]:
    """Parses a WEMDE dataset"""

    # key to extract
    key_field = "facilityScadaDispatchIntervals"

    download_json = _wemde_download_dataset(url)

    if key_field not in download_json:
        raise Exception(f"No {key_field} in JSON")

    json_records = download_json[key_field]

    logger.debug(f"Got {len(json_records)} records")

    models = []

    # Get battery unit mappings
    battery_unit_map = await get_battery_unit_map()

    # Create a function to map facility codes based on generated value
    def _map_battery_code(facility_code: str, generated: float) -> str:
        if facility_code in battery_unit_map:
            battery_map = battery_unit_map[facility_code]
            if generated < 0:
                return battery_map.charge_unit
            else:
                return battery_map.discharge_unit
        return facility_code

    # map fields
    for entry in json_records:
        interval = datetime.fromisoformat(entry.get("dispatchInterval"))

        # strip timezone from interval
        interval = interval.replace(tzinfo=None)

        facility_code = entry.get("code")
        generated = entry.get("quantity", 0)

        # map battery facility code if needed
        facility_code = _map_battery_code(facility_code, generated)

        try:
            m = FacilityScadaSchema(
                **{
                    "network_id": "WEM",
                    "interval": interval,
                    "facility_code": facility_code,
                    "generated": generated,
                    "energy": generated / 12,
                }
            )
            models.append(m)

        except ValidationError as e:
            logger.error(f"Validation error: {e}")
            continue

    return models


def wemde_parse_trading_price(url: str) -> list[BalancingSummarySchema]:
    """Parse WEMDE trading price"""

    # key to extract
    key_field = "referenceTradingPrices"

    download_json = _wemde_download_dataset(url)

    if key_field not in download_json:
        raise Exception(f"No {key_field} in JSON")

    json_records = download_json[key_field]

    logger.debug(f"Got {len(json_records)} records")

    models = []

    # map fields
    for entry in json_records:
        interval = datetime.fromisoformat(entry.get("tradingInterval"))

        # strip timezone from interval
        interval = interval.replace(tzinfo=None)

        try:
            m = BalancingSummarySchema(
                **{
                    "network_id": "WEM",
                    "interval": interval,
                    "price": entry.get("referenceTradingPrice", 0),
                    "network_region": "WEM",
                }
            )
            models.append(m)

        except ValidationError as e:
            logger.error(f"Validation error: {e}")
            continue

    return models


# debug entry point
if __name__ == "__main__":
    url = (
        "https://data.wa.aemo.com.au/public/market-data/wemde/referenceTradingPrice/current/ReferenceTradingPrice_2024-01-13.json"
    )
    models = wemde_parse_trading_price(url)
    print(len(models))
    # with open("wem-live.json", "w") as fh:
    # fh.write(m.json(indent=4))
