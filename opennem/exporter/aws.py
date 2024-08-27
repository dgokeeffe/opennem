"""
OpenNEM S3 Bucket Module

Writes OpennemDataSet's to AWS S3 buckets
"""

import json
import logging
from typing import Any

import boto3
from botocore.exceptions import ClientError

from opennem import settings
from opennem.api.stats.schema import OpennemDataSet
from opennem.utils.numbers import compact_json_output_number_series
from opennem.utils.url import urljoin

logger = logging.getLogger("opennem.exporter.aws")


class OpennemDataSetSerializeS3:
    bucket_name: str
    debug: bool = False
    exclude_unset: bool = False

    def __init__(self, bucket_name: str, exclude_unset: bool = False, debug: bool = False) -> None:
        self.bucket = boto3.resource(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        ).Bucket(bucket_name)
        self.debug = settings.debug
        self.bucket_name = bucket_name

        if debug:
            self.debug = debug

        self.exclude_unset = exclude_unset

    # @TODO return a full OpennemDataSet
    def load(self, key: str) -> Any:
        return json.load(self.bucket.Object(key=key).get()["Body"])

    def dump(self, key: str, stat_set: OpennemDataSet, exclude: set | None = None) -> Any:
        indent = None

        if settings.debug:
            indent = 4

        stat_set_content = stat_set.model_dump_json(exclude_unset=self.exclude_unset, indent=indent, exclude=exclude)

        if settings.compact_number_ouput_in_json:
            logger.debug(f"Applying compact number output to {key}")

            if settings.compact_number_ouput_in_json:
                stat_set_content = compact_json_output_number_series(stat_set_content)

        obj = self.bucket.Object(key=key)
        _write_response = obj.put(Body=stat_set_content, ContentType="application/json")

        _write_response["length"] = len(stat_set_content)

        return _write_response

    def write(self, key: str, content: str, content_type: str = "application/json") -> Any:
        obj = self.bucket.Object(key=key)
        _write_response = obj.put(Body=content, ContentType=content_type)

        _write_response["length"] = len(content)

        return _write_response


def write_statset_to_s3(stat_set: OpennemDataSet, file_path: str, exclude: set | None = None, exclude_unset: bool = False) -> int:
    """
    Write an Opennem data set to an s3 bucket using boto
    """
    s3_save_path = urljoin(f"https://{settings.s3_bucket_path}", file_path)

    if file_path.startswith("/"):
        file_path = file_path[1:]

    if not settings.s3_bucket_name:
        raise Exception("Require an S3 bucket to write to")

    s3bucket = OpennemDataSetSerializeS3(settings.s3_bucket_name, exclude_unset=exclude_unset)
    write_response = None

    try:
        write_response = s3bucket.dump(file_path, stat_set, exclude=exclude)
    except ClientError as e:
        logging.error(e)
        return 0

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

    logger.info("Wrote {} to {}".format(write_response["length"], s3_save_path))

    return write_response["length"]


def write_to_s3(content: str, file_path: str, content_type: str = "text/plain") -> int:
    """
    Write a string to s3
    """
    s3_save_path = urljoin(f"https://{settings.s3_bucket_path}", file_path)

    if file_path.startswith("/"):
        file_path = file_path[1:]

    if not settings.s3_bucket_name:
        raise Exception("Require an S3 bucket to write to")

    s3bucket = OpennemDataSetSerializeS3(settings.s3_bucket_name)
    write_response = None

    logger.info(f"Writing to s3 bucket {settings.s3_bucket_name} at path {file_path}")

    try:
        write_response = s3bucket.write(file_path, content, content_type=content_type)
    except ClientError as e:
        logging.error(e)
        return 0

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

    logger.info("Wrote {} to {}".format(write_response["length"], s3_save_path))

    return write_response["length"]
