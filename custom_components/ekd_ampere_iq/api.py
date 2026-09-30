"""Read-only EKD customer API client; never include response bodies in errors."""

import asyncio
from datetime import date
from uuid import UUID

import aiohttp

BASE_URL = "https://product.ekd-iot.de"


class ApiAuthError(Exception):
    """Credentials refused or permission denied."""


class ApiConnectionError(Exception):
    """Transport unavailable."""


class ApiResponseError(Exception):
    """Unexpected server response."""


class EkdApi:
    def __init__(self, session: aiohttp.ClientSession, api_key: str) -> None:
        self._session = session
        self._api_key = api_key

    async def _get(self, path: str, params: dict | None = None):
        try:
            async with self._session.get(
                f"{BASE_URL}{path}",
                headers={
                    "x-client-api-key": self._api_key,
                    "x-client-type": "de.ekd.customer.apiclient",
                },
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status in (401, 403):
                    raise ApiAuthError(f"EKD authentication rejected (HTTP {response.status})")
                if response.status != 200:
                    raise ApiResponseError(f"EKD returned HTTP {response.status}")
                data = await response.json()
                return data
        except (ValueError, aiohttp.ContentTypeError):
            raise ApiResponseError("Invalid EKD JSON response") from None
        except asyncio.TimeoutError:
            raise ApiConnectionError("EKD request timed out") from None
        except aiohttp.ClientError:
            raise ApiConnectionError("EKD connection failed") from None

    async def installations(self) -> list[str]:
        data = await self._get("/api/v1/customer/installation")
        if not isinstance(data, list) or not data:
            raise ApiResponseError("No EKD installations returned")
        uuids = []
        for item in data:
            try:
                value = item["uuid"]
                if not isinstance(value, str):
                    raise ValueError
                canonical = str(UUID(value))
                if canonical != value:
                    raise ValueError
            except (TypeError, ValueError, KeyError) as err:
                raise ApiResponseError("Invalid EKD installation identifier") from err
            uuids.append(value)
        return list(dict.fromkeys(uuids))

    async def power(self, installation_uuid: str) -> dict:
        return await self._get(f"/api/v1/installation/{installation_uuid}/now/all/power")

    async def daily_work(self, installation_uuid: str, day: date) -> dict:
        return await self._get(
            f"/api/v1/installation/{installation_uuid}/total/common/work",
            {"period": "day", "date": day.isoformat()},
        )
