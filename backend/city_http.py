"""Bounded read-only provider HTTP; no retries, redirects, or provider-body logging."""
from threading import BoundedSemaphore

import httpx


class CityServiceError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


_SLOTS = BoundedSemaphore(4)
_CLIENT = httpx.Client(timeout=httpx.Timeout(4, connect=2, pool=0.5),
                       limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
                       follow_redirects=False)


def fetch_json(method, url, client=None, **kwargs):
    if not _SLOTS.acquire(blocking=False):
        raise CityServiceError("busy")
    try:
        response = (client or _CLIENT).request(method, url, **kwargs)
        if response.status_code == 429:
            raise CityServiceError("rate_limited")
        if response.status_code in (401, 403):
            raise CityServiceError("authentication_error")
        if not 200 <= response.status_code < 300:
            raise CityServiceError("provider_error")
        if len(response.content) > 1_000_000:
            raise CityServiceError("invalid_response")
        data = response.json()
        if not isinstance(data, dict):
            raise CityServiceError("invalid_response")
        return data
    except httpx.TimeoutException:
        raise CityServiceError("timeout") from None
    except httpx.RequestError:
        raise CityServiceError("connection_error") from None
    except (ValueError, TypeError):
        raise CityServiceError("invalid_response") from None
    finally:
        _SLOTS.release()
