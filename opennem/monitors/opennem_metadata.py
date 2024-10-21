import logging

import requests
from pydantic import ValidationError

from opennem import settings
from opennem.api.export.map import StatMetadata
from opennem.utils.url import bucket_to_website, urljoin

logger = logging.getLogger(__name__)


def check_metadata_status() -> bool:
    metadata_path = bucket_to_website(urljoin(settings.s3_bucket_public_url, "metadata.json"))

    resp = requests.get(metadata_path)

    if resp.status_code != 200:
        logger.error("Error retrieving opennem metadata")
        return False

    resp_json = resp.json()

    metadata = None

    try:
        metadata = StatMetadata.parse_obj(resp_json)
    except ValidationError as e:
        logger.error(f"Validation error in metadata: {e}")

    if not metadata:
        return False

    for resource in metadata.resources:
        if not resource.path:
            logger.info("Resource without path")
            continue

        resource_website_path = bucket_to_website(urljoin(settings.s3_bucket_public_url, resource.path))

        r = requests.get(resource_website_path)

        if r.status_code != 200:
            logger.error(f"Error with metadata resource: {resource_website_path}")

    return True


if __name__ == "__main__":
    check_metadata_status()
