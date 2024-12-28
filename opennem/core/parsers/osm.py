"""
OpenNEM Open Street Map Parser

"""

import logging

import osm2geojson
from geoalchemy2.elements import WKBElement
from geoalchemy2.shape import from_shape
from shapely.geometry import shape

from opennem.utils.httpx import http

logger = logging.getLogger(__name__)

OSM_API_WAY_URI = "https://www.openstreetmap.org/api/0.6/way/{way_id}/full"


def get_osm_way_url(way_id: str) -> str:
    """Returns an XML thing for an OSM way id"""
    return OSM_API_WAY_URI.format(way_id=way_id)


async def get_osm_way(way_id: str) -> dict:
    """Returns the xml payload from the osm api"""
    way_url = get_osm_way_url(way_id)

    way_resp = await http.get(way_url)

    if not way_resp.is_success:
        logger.error("No way")
        raise Exception(f"Could not get way: {way_resp.status_code}")

    way_resp_content = way_resp.text

    geojson_response = osm2geojson.xml2geojson(way_resp_content, filter_used_refs=False, log_level="INFO")

    if not isinstance(geojson_response, dict):
        raise Exception("Did not get a valid server response from OSM API")

    if "type" not in geojson_response:
        raise Exception("Did not get a valid server response from OSM API")

    if geojson_response["type"] != "FeatureCollection":
        raise Exception("Did not get a valid FeatureCollection from OSM API")

    if "features" not in geojson_response:
        raise Exception("GeoJSON has no features")

    return geojson_response


async def get_osm_geom(way_id: str, srid: int = 4326) -> WKBElement:
    """Returns an WKT element from an osm way id"""
    poly = None

    osm_way = await get_osm_way(way_id)

    for feature in osm_way["features"]:
        if feature["geometry"]["type"] == "Polygon":
            poly = feature["geometry"]

    if not poly:
        raise Exception("No polygon found in OSM way")

    geom = from_shape(shape(poly), srid=srid)

    return geom
