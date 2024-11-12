"""
OpenNEM R2 Bucket Module

Writes OpennemDataSet's to AWS R2 buckets
"""

import logging
from typing import Any
from urllib.parse import urljoin

from aioboto3.session import Session
from aiohttp import ClientError

from opennem import settings
from opennem.api.stats.schema import OpennemDataSet

logger = logging.getLogger("opennem.exporter.r2_bucket")

_botocore_session = Session()


class OpennemDataSetSerialize:
    session: Session
    bucket: Any
    bucket_name: str
    debug: bool = False
    exclude_unset: bool = False

    def __init__(self, bucket_name: str, exclude_unset: bool = False, debug: bool = False) -> None:
        self.session = _botocore_session
        self.bucket = None
        self.debug = settings.debug
        self.bucket_name = bucket_name
        self.exclude_unset = exclude_unset

        if debug:
            self.debug = debug

    async def create_S3_bucket(self):
        """Create an S3 bucket if it doesn't exist"""
        if not settings.s3_bucket_name:
            raise Exception("Require an R2 bucket to write to")

        async with self.session.resource("s3", endpoint_url=settings.s3_endpoint_url) as s3:
            try:
                self.bucket = await s3.Bucket(self.bucket_name)
            except Exception as e:
                raise RuntimeError(e) from e

    async def dump(self, key: str, stat_set_to_write: OpennemDataSet, exclude: set | None = None) -> Any:
        indent = None

        if settings.debug:
            indent = 4

        stat_set_content = stat_set_to_write.model_dump_json(
            exclude_unset=self.exclude_unset, indent=indent if settings.is_dev else None, exclude=exclude
        )

        try:
            obj_to_write = await self.bucket.Object(key=key)
            write_result = await obj_to_write.put(Body=stat_set_content, ContentType="application/json")
        except Exception as e:
            raise RuntimeError(e) from e

        write_result["length"] = len(stat_set_content)

        return write_result

    async def write(self, key: str, content_to_write: str, content_type: str = "text/plain") -> Any:
        obj_to_write = await self.bucket.Object(key=key)

        write_result = await obj_to_write.put(Body=content_to_write, ContentType=content_type)

        write_result["length"] = len(content_to_write)

        return write_result

    async def __aenter__(self):
        await self.create_S3_bucket()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        pass


async def write_stat_set_to_r2(
    stat_set: OpennemDataSet, file_path: str, exclude: set | None = None, exclude_unset: bool = False
) -> int:
    r2_save_path = urljoin(f"https://{settings.s3_bucket_public_url}", file_path)

    if file_path.startswith("/"):
        file_path = file_path[1:]

    if not settings.s3_bucket_name:
        raise Exception("Require an R2 bucket to write to")

    write_response = None

    async with OpennemDataSetSerialize(settings.s3_bucket_name, exclude_unset=exclude_unset) as r2_bucket:
        try:
            write_response = await r2_bucket.dump(file_path, stat_set, exclude=exclude)
        except ClientError as e:
            logger.error(e)
            return 1

    if "ResponseMetadata" not in write_response:
        raise Exception(f"Error writing stat set to {file_path} invalid write response")

    if "HTTPStatusCode" not in write_response["ResponseMetadata"]:
        raise Exception(f"Error writing stat set to {file_path} invalid write response")

    if write_response["ResponseMetadata"]["HTTPStatusCode"] != 200:
        raise Exception(
            "Error writing stat set to {} - response code {}".format(
                file_path, write_response["ResponseMetadata"]["HTTPStatusCode"]
            )
        )

    logger.info("Wrote {:.2f} MB to {}".format(write_response["length"] / (1024), r2_save_path))

    return write_response["length"]


async def write_content_to_r2(content: str, file_path: str, content_type: str = "text/plain") -> int:
    if not settings.s3_bucket_name:
        raise Exception("Require an R2 bucket to write to")

    r2_save_path = urljoin(f"https://{settings.s3_bucket_public_url}", file_path)

    if file_path.startswith("/"):
        file_path = file_path[1:]

    logger.debug(f"setting up r2 bucket {settings.s3_bucket_name}")
    r2_bucket = OpennemDataSetSerialize(settings.s3_bucket_name)
    write_response = None

    try:
        await r2_bucket.create_S3_bucket()
        logger.debug(f"writing {len(content)} to {file_path} of type {content_type}")
        write_response = await r2_bucket.write(file_path, content, content_type=content_type)
    except ClientError as e:
        logger.error(e)
        return 1
    except Exception as e:
        logger.error(e)
        return 1

    logger.debug(write_response)

    if "ResponseMetadata" not in write_response:
        raise Exception(f"Error writing stat set to {file_path} invalid write response")

    if "HTTPStatusCode" not in write_response["ResponseMetadata"]:
        raise Exception(f"Error writing stat set to {file_path} invalid write response")

    if write_response["ResponseMetadata"]["HTTPStatusCode"] != 200:
        raise Exception(
            "Error writing stat set to {} - response code {}".format(
                file_path, write_response["ResponseMetadata"]["HTTPStatusCode"]
            )
        )

    logger.info("Wrote {:.2f} MB to {}".format(write_response["length"] / (1024), r2_save_path))

    return write_response["length"]
