"""unkney client for validating api keys"""

import asyncio
import logging
from functools import wraps

import unkey
from cachetools import TTLCache
from pydantic import ValidationError
from unkey import ApiKey, ErrorCode

from opennem import settings
from opennem.users.ratelimit import OPENNEM_RATELIMIT_ADMIN, OPENNEM_RATELIMIT_PRO, OPENNEM_RATELIMIT_USER
from opennem.users.schema import OpennemAPIRequestMeta, OpenNEMRoles, OpenNEMUser, OpenNEMUserRateLimit

logger = logging.getLogger("opennem.clients.unkey")

unkey_client = unkey.Client(api_key=settings.unkey_root_key)

# Cache for unkey validation results - 5 minute TTL
_unkey_cache = TTLCache(maxsize=10000, ttl=60 * 5)


def cache_unkey_result(func):
    """
    Decorator to cache unkey validation results for 5 minutes.

    Args:
        func: The async function to cache

    Returns:
        Cached result if available, otherwise calls function and caches result
    """

    @wraps(func)
    async def wrapper(api_key: str, *args, **kwargs) -> OpenNEMUser | None:
        # Check cache first
        if api_key in _unkey_cache:
            return _unkey_cache[api_key]

        # Call original function
        result = await func(api_key, *args, **kwargs)

        # Cache successful results
        if result:
            _unkey_cache[api_key] = result

        return result

    return wrapper


class UnkeyInvalidUserException(Exception):
    pass


@cache_unkey_result
async def unkey_validate(api_key: str) -> None | OpenNEMUser:
    """
    Validate a key with unkey.
    Results are cached for 5 minutes to reduce API calls.

    Args:
        api_key: The API key to validate

    Returns:
        OpenNEMUser if validation successful, None otherwise

    Raises:
        UnkeyInvalidUserException: If validation fails
    """

    if not settings.unkey_root_key:
        raise Exception("No unkey root key set")

    if not settings.unkey_api_id:
        raise Exception("No unkey app id set")

    try:
        async with unkey_client as c:
            result = await c.keys.verify_key(key=api_key, api_id=settings.unkey_api_id)  # type: ignore

        if not result.is_ok:
            logger.warning("Unkey verification failed")
            raise UnkeyInvalidUserException("Verification failed: invalid key")

        if result.is_err:
            err = result.unwrap_err()
            code = (err.code or unkey.models.ErrorCode.Unknown).value
            logger.info(f"Unkey verification failed: {code}")
            raise UnkeyInvalidUserException("Verification failed: error")

        data = result.unwrap()

        # Check if the code is NOT_FOUND and return None if so
        if data.code == ErrorCode.NotFound:
            logger.info("API key not found")
            raise UnkeyInvalidUserException("Verification failed: not found")

        if not data.valid:
            logger.info("API key is not valid")
            raise UnkeyInvalidUserException("Verification failed: not valid")

        if data.error:
            logger.info(f"API key error: {data.error}")
            raise UnkeyInvalidUserException("Verification failed: data error")

        if not data.id:
            logger.info("API key id is not valid no id")
            raise UnkeyInvalidUserException("Verification failed: no id")

        try:
            model = OpenNEMUser(
                id=data.id,
                valid=data.valid,
                owner_id=data.owner_id,
                meta=data.meta,
                error=data.error,
            )

            model.meta = OpennemAPIRequestMeta(remaining=data.remaining, reset=data.expires)

            if data.ratelimit:
                model.rate_limit = OpenNEMUserRateLimit(
                    limit=data.ratelimit.limit, remaining=data.ratelimit.remaining, reset=data.ratelimit.reset
                )

            if data.meta:
                if "roles" in data.meta:
                    for role in data.meta["roles"]:
                        model.roles.append(OpenNEMRoles(role))

            return model
        except ValidationError as ve:
            logger.error(f"Pydantic validation error: {ve}")
            for error in ve.errors():
                logger.error(f"Field: {error['loc'][0]}, Error: {error['msg']}")
            raise UnkeyInvalidUserException("Unkey verification failed: model validation error") from ve

    except Exception as e:
        logger.exception(f"Unexpected error in unkey_validate: {e}")
        raise UnkeyInvalidUserException("Unkey verification failed: unexpected error") from e


async def unkey_create_key(
    email: str, name: str, roles: list[OpenNEMRoles], ratelimit: unkey.Ratelimit | None = None
) -> ApiKey | None:
    """Create a key with unkey"""
    if not settings.unkey_root_key:
        raise Exception("No unkey root key set")

    if not settings.unkey_api_id:
        raise Exception("No unkey app id set")

    prefix = "on"

    if settings.is_dev:
        prefix = "on_dev"

    meta = {"roles": [role.value for role in roles], "email": email, "name": name}

    if not ratelimit:
        ratelimit = OPENNEM_RATELIMIT_USER

        if "pro" in roles:
            ratelimit = OPENNEM_RATELIMIT_PRO

        if "admin" in roles:
            ratelimit = OPENNEM_RATELIMIT_ADMIN

    try:
        async with unkey.client.Client(api_key=settings.unkey_root_key) as c:
            result = await c.keys.create_key(
                api_id=settings.unkey_api_id, name=name, prefix=prefix, meta=meta, owner_id=email, ratelimit=ratelimit
            )

            if not result.is_ok:
                error = result.unwrap_err()
                logger.error(f"Unkey key creation failed: {error}")
                return None

    except Exception as e:
        logger.exception(f"Unexpected error in unkey_create_key: {e}")
        return None

    data = result.unwrap()

    logger.info(f"Unkey key created: {data.key} {data.key_id}")

    return data


# debug entry point
if __name__ == "__main__":
    import os

    test_key = os.environ.get("OPENNEM_UNKEY_TEST_KEY", None)

    if not test_key:
        raise Exception("No test key set")

    model = asyncio.run(unkey_validate(api_key=test_key))

    if not model:
        print("No model")
    else:
        print(model)
