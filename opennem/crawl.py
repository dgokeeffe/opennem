"""Primary OpenNEM crawler"""

import asyncio
import inspect
import logging
from typing import Any

from pydantic import ValidationError

from opennem import settings
from opennem.controllers.schema import ControllerReturn
from opennem.core.crawlers.meta import CrawlStatTypes, crawler_set_meta, crawlers_get_all_meta
from opennem.core.crawlers.schema import CrawlerDefinition, CrawlerSet
from opennem.core.parsers.aemo.nemweb import parse_aemo_url_optimized
from opennem.crawlers.apvi import (
    APVIRooftopAllCrawler,
    APVIRooftopLatestCrawler,
    APVIRooftopMonthCrawler,
    APVIRooftopTodayCrawler,
)
from opennem.crawlers.bom import BOMCapitals
from opennem.crawlers.mms import (
    AEMOMMSDispatchInterconnector,
    AEMOMMSDispatchPrice,
    AEMOMMSDispatchRegionsum,
    AEMOMMSDispatchScada,
    AEMOMMSTradingPrice,
    AEMOMMSTradingRegionsum,
)
from opennem.crawlers.nemweb import (
    AEMONEMDispatchActualGEN,
    AEMONEMDispatchActualGENArchvie,
    AEMONEMNextDayDispatch,
    AEMONEMNextDayDispatchArchvie,
    AEMONemwebDispatchIS,
    AEMONemwebDispatchISArchive,
    AEMONemwebRooftop,
    AEMONemwebRooftopForecast,
    AEMONemwebTradingIS,
    AEMONemwebTradingISArchive,
    AEMONNemwebDispatchScada,
    AEMONNemwebDispatchScadaArchive,
)
from opennem.crawlers.wem import WEMBalancing, WEMBalancingLive, WEMFacilityScada, WEMFacilityScadaLive
from opennem.crawlers.wemde import AEMOWEMDEFacilityScadaHistory, AEMOWEMDETradingReport, AEMOWEMDETradingReportHistory
from opennem.schema.date_range import CrawlDateRange
from opennem.utils.dates import get_today_opennem
from opennem.utils.modules import load_all_crawler_definitions

logger = logging.getLogger("opennem.crawl")

_CRAWLERS_MODULE = "opennem.crawlers"


async def load_crawlers(live_load: bool = False) -> CrawlerSet:
    """Loads all the crawler definitions from a module and returns a CrawlSet"""
    crawlers = []
    crawler_definitions: list[CrawlerDefinition] = []

    if live_load:
        crawler_definitions = load_all_crawler_definitions(_CRAWLERS_MODULE)

    crawler_definitions = [
        # NEM
        AEMONEMDispatchActualGEN,
        AEMONEMNextDayDispatch,
        # NEMWEB
        AEMONemwebRooftop,
        AEMONemwebRooftopForecast,
        AEMONemwebTradingIS,
        AEMONemwebDispatchIS,
        AEMONNemwebDispatchScada,
        # NEMWEB Archive
        AEMONEMDispatchActualGENArchvie,
        AEMONEMNextDayDispatchArchvie,
        AEMONNemwebDispatchScadaArchive,
        AEMONemwebTradingISArchive,
        AEMONemwebDispatchISArchive,
        # APVI
        APVIRooftopTodayCrawler,
        APVIRooftopLatestCrawler,
        APVIRooftopMonthCrawler,
        APVIRooftopAllCrawler,
        # BOM
        BOMCapitals,
        # WEM
        WEMBalancing,
        WEMBalancingLive,
        WEMFacilityScada,
        WEMFacilityScadaLive,
        # WEMDE
        AEMOWEMDEFacilityScadaHistory,
        AEMOWEMDETradingReport,
        AEMOWEMDETradingReportHistory,
        # MMS Crawlers
        AEMOMMSDispatchInterconnector,
        AEMOMMSDispatchRegionsum,
        AEMOMMSDispatchPrice,
        AEMOMMSDispatchScada,
        AEMOMMSTradingPrice,
        AEMOMMSTradingRegionsum,
    ]

    crawler_meta = await crawlers_get_all_meta()

    for crawler_inst in crawler_definitions:
        if crawler_inst.name not in crawler_meta:
            crawlers.append(crawler_inst)
            continue

        crawler_updated_with_meta: CrawlerDefinition | None = None

        try:
            crawler_field_values = {
                **crawler_inst.dict(),
                **crawler_meta[crawler_inst.name],
                "version": "2",
            }
            crawler_updated_with_meta = CrawlerDefinition(
                **crawler_field_values,
            )
        except ValidationError as e:
            logger.error(f"Validation error for crawler {crawler_inst.name}: {e}")
            raise Exception("Crawler initiation error") from None

        if crawler_updated_with_meta:
            crawlers.append(crawler_updated_with_meta)

    cs = CrawlerSet(crawlers=crawlers)

    logger.debug("Loaded {} crawlers: {}".format(len(cs.crawlers), ", ".join([i.name for i in cs.crawlers])))

    return cs


async def run_crawl(
    crawler: CrawlerDefinition,
    last_crawled: bool = True,
    limit: int | None = None,
    latest: bool = True,
    date_range: CrawlDateRange | None = None,
    reverse: bool = True,
) -> ControllerReturn:
    """Runs a crawl from the crawl definition with ability to overwrite last crawled and obey the defined
    limit"""

    if not settings.run_crawlers:
        logger.info(f"Crawlers are disabled. Skipping {crawler.name}")
        raise Exception("Crawl controller error: crawling disabled") from None

    logger.info(
        f"Crawling: {crawler.name}. (Last Crawled: {crawler.last_crawled}. "
        f"Limit: {limit or crawler.limit}. Server latest: {crawler.server_latest})"
    )

    # now in opennem time which is Australia/Sydney
    now_opennem_time = get_today_opennem()

    await crawler_set_meta(crawler.name, CrawlStatTypes.version, crawler.version)
    await crawler_set_meta(crawler.name, CrawlStatTypes.last_crawled, now_opennem_time)

    cr: ControllerReturn | None = None

    # build the params for the crawler
    params: dict[str, Any] = {"crawler": crawler}

    if "last_crawled" in inspect.signature(crawler.processor).parameters:
        params["last_crawled"] = last_crawled

    if "date_range" in inspect.signature(crawler.processor).parameters:
        params["date_range"] = date_range

    if "reverse" in inspect.signature(crawler.processor).parameters:
        params["reverse"] = reverse

    if "limit" in inspect.signature(crawler.processor).parameters:
        params["limit"] = limit or crawler.limit

    if "latest" in inspect.signature(crawler.processor).parameters:
        params["latest"] = latest

    # run the crawl processor
    try:
        if inspect.iscoroutinefunction(crawler.processor):
            cr = await crawler.processor(**params)
        else:
            cr = crawler.processor(**params)
    except Exception as e:
        raise Exception(f"Crawl controller error for {crawler.name}: {e}") from e

    if not cr:
        raise Exception(f"Crawl controller error no ControllerReturn for {crawler.name}") from None

    # run here
    has_errors = False

    logger.info(f"{crawler.name} Inserted {cr.inserted_records} of {cr.total_records} records")

    if cr.errors > 0:
        has_errors = True
        logger.error(f"Crawl controller error for {crawler.name}: {cr.error_detail}")
        raise Exception("Crawl controller error") from None

    if not has_errors:
        if cr.server_latest:
            await crawler_set_meta(crawler.name, CrawlStatTypes.latest_processed, cr.server_latest)
            await crawler_set_meta(crawler.name, CrawlStatTypes.server_latest, cr.server_latest)
            logger.info(f"Set last_processed to {crawler.last_processed} and server_latest to {cr.server_latest}")
        else:
            logger.debug(f"{crawler.name} has no server_latest return")

    return cr


async def run_crawl_urls(urls: list[str]) -> None:
    """Crawl a lsit of urls
    @TODO support directories
    """

    for url in urls:
        if url.lower().endswith(".zip") or url.lower().endswith(".csv"):
            try:
                cr = await parse_aemo_url_optimized(url)
                logger.info(f"Parsed {url} and got {cr.inserted_records or 0} inserted")
            except Exception as e:
                logger.error(e)


_CRAWLER_SET: CrawlerSet | None = None


async def get_crawl_set() -> CrawlerSet:
    """Access method for crawler set"""
    global _CRAWLER_SET

    if not _CRAWLER_SET:
        _CRAWLER_SET = await load_crawlers()

    return _CRAWLER_SET


if __name__ == "__main__":
    import asyncio

    asyncio.run(run_crawl(BOMCapitals))
