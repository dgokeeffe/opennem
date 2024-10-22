"""OpenNEM v4 Stations Output

Output controller for stations that is both v3 and v4 comparible.
"""

from pydantic_geojson import PointModel

from opennem.db.models.opennem import Facility

from .schema import FacilityFeature, FacilityGeo


async def stations_to_geojson(stations: list[Facility]) -> FacilityGeo:
    """Takes a list of station models and returns a GeoJSON FeatureCollection"""
    features = []

    for station in stations:
        if not station.location:
            continue

        if not station.units:
            continue

        feature_dict: dict = {"properties": {}}

        feature_dict["properties"] = {
            "station_id": station.id,
            "station_code": station.code,
            "facility_id": station.code,
            "network": station.units[0].network.label,
            "network_country": station.units[0].network.country,
            "state": station.location.state,
            "postcode": station.location.postcode,
            "name": station.name,
            "capacity_registered": station.capacity_registered,
            # "capacity_aggregate": station.capacity_aggregate,
            "duid_data": [],
        }

        if station.location.osm_way_id:
            feature_dict["properties"]["osm_way_id"] = station.location.osm_way_id

        for facility in station.units:
            if not facility.fueltech:
                continue

            feature_dict["properties"]["duid_data"].append(
                {
                    # "oid": facility.oid,
                    "duid": facility.code,
                    "fuel_tech": facility.fueltech.code,
                    "fuel_tech_label": facility.fueltech.label,
                    "fuel_tech_renewable": facility.fueltech.renewable,
                    "commissioned_date": facility.registered,
                    "decommissioned_date": facility.deregistered,
                    "status": facility.status.code,
                    "status_label": facility.status.label,
                    "unit_id": facility.unit_id,
                    "unit_number": facility.unit_number,
                    "unit_size": facility.unit_capacity,
                    "unit_alias": facility.unit_alias,
                    # capacities for the unit
                    "capacity_registered": facility.capacity_registered,
                    # "capacity_aggregate": facility.capacity_aggregate,
                    # network specific fields (DUID is one)
                    "data_first_seen": facility.data_first_seen,
                    "data_last_seen": facility.data_last_seen,
                    "network_region": facility.network_region,
                    "emissions_factor_co2": facility.emissions_factor_co2,
                }
            )

        feature = FacilityFeature(**feature_dict)

        if station.location and station.location.geom:
            geom = PointModel(type="Point", coordinates=[station.location.lng, station.location.lat])

            if not feature.geometry:
                feature.geometry = geom

        features.append(feature)

    crs = {
        "type": "name",
        "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"},
    }

    geo = FacilityGeo(features=features, crs=crs, name="opennem.facilities")

    return geo
