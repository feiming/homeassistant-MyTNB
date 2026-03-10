"""API client for MyTNB smart meter data."""

from __future__ import annotations

import re
import logging
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import aiohttp

_LOGGER = logging.getLogger(__name__)

# Sensitive keys that should be redacted from logs
SENSITIVE_KEYS = {"password", "Password", "Email", "email", "username", "Username", "token", "Token"}


def _redact_url(url: str) -> str:
    """Redact sensitive query parameters from URL."""
    try:
        parsed = urlparse(url)
        if parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=True)
            # Redact sensitive parameters
            redacted_params = {}
            for key, values in params.items():
                if any(sensitive in key for sensitive in SENSITIVE_KEYS):
                    redacted_params[key] = ["***"]
                else:
                    redacted_params[key] = values
            # Rebuild URL with redacted params
            redacted_query = urlencode(redacted_params, doseq=True)
            parsed = parsed._replace(query=redacted_query)
            return urlunparse(parsed)
    except Exception:
        # If URL parsing fails, return original but mask common patterns
        return re.sub(r'[?&](password|Password|token|Token|email|Email|username|Username)=[^&]*', r'\1=***', url)
    return url


def _redact_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Redact sensitive values from dictionary."""
    redacted = {}
    for key, value in data.items():
        if any(sensitive in str(key) for sensitive in SENSITIVE_KEYS):
            redacted[key] = "***"
        elif isinstance(value, dict):
            redacted[key] = _redact_dict(value)
        elif isinstance(value, str) and ("password" in key.lower() or "token" in key.lower()):
            redacted[key] = "***"
        else:
            redacted[key] = value
    return redacted


class MyTNBAPI:
    """API client for MyTNB."""

    def __init__(self, username: str, password: str, smartmeter_url: str) -> None:
        """Initialize the API client."""
        self.username = username
        self.password = password
        self.smartmeter_url = smartmeter_url
        self._session: aiohttp.ClientSession | None = None
        self._sdpudcid: str | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> MyTNBAPI:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.close()

    async def get_login_details(self) -> dict[str, str]:
        """Get login form details."""
        session = await self._get_session()
        url = "https://www.mytnb.com.my/api/sitecore/Account/Login"
        # Note: payload contains credentials - never log payload values directly
        payload = {"Email": self.username, "Password": self.password}
        
        _LOGGER.debug("Getting login details from: %s", url)
        
        async with session.post(url, data=payload) as response:
            status = response.status
            text = await response.text()
            _LOGGER.debug("Login details response: status=%d, response_length=%d", status, len(text))
            matches = re.findall(r'name="([^"]+)" value="([^"]*)"', text)
            result = {name: value for name, value in matches}
            # Only log count, not the actual form field values (may contain sensitive data)
            _LOGGER.debug("Extracted %d form fields from login page", len(result))
            return result

    async def login(self) -> bool:
        """Login to MyTNB."""
        session = await self._get_session()
        url = "https://myaccount.mytnb.com.my/SSO/SSOHandler"
        payload = await self.get_login_details()
        
        _LOGGER.debug("Logging in to: %s", url)
        # Only log payload keys, never values (may contain sensitive form data)
        _LOGGER.debug("Login payload keys: %s", list(payload.keys()))
        
        async with session.post(url, data=payload) as response:
            status = response.status
            _LOGGER.debug("Login response: status=%d, url=%s", status, _redact_url(str(response.url)))
            return status == 200

    def get_smartmeter_path(self) -> str:
        """Extract smartmeter path from URL."""
        pattern = r'/AccountManagement/SmartMeter/Index/TRIL\?caNo=[^"\s]+'
        match = re.search(pattern, self.smartmeter_url)
        if match:
            return match.group(0)
        raise ValueError("Could not find smartmeter URL path")

    async def access_smartmeter(self) -> bool:
        """Access smartmeter page."""
        session = await self._get_session()
        smartmeter_path = self.get_smartmeter_path()
        url = f"https://myaccount.mytnb.com.my{smartmeter_path}"
        
        _LOGGER.debug("Accessing smartmeter page: %s", _redact_url(url))
        
        async with session.get(url) as response:
            status = response.status
            _LOGGER.debug("Smartmeter access response: status=%d, url=%s", status, _redact_url(str(response.url)))
            return status == 200

    async def get_sdpudcid(self) -> str:
        """Get SDPUDCID from dashboard."""
        if self._sdpudcid:
            _LOGGER.debug("Using cached sdpudcid: %s", self._sdpudcid)
            return self._sdpudcid

        session = await self._get_session()
        url = "https://smartliving.myaccount.mytnb.com.my/dashboard"
        headers = {
            "Host": "smartliving.myaccount.mytnb.com.my",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "DNT": "1",
            "Connection": "keep-alive",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/png,image/svg+xml,*/*;q=0.8",
        }
        
        _LOGGER.debug("Fetching sdpudcid from dashboard: %s", url)
        
        async with session.get(url, headers=headers) as response:
            response.raise_for_status()
            status = response.status
            text = await response.text()
            
            _LOGGER.debug("Dashboard response: status=%d, response_length=%d", status, len(text))
            
            match = re.search(r'"sdpudcid":"(\d+)"', text)
            if match:
                self._sdpudcid = match.group(1)
                _LOGGER.debug("Extracted sdpudcid: %s", self._sdpudcid)
                return self._sdpudcid
            
            # Log more details for debugging
            _LOGGER.warning(
                "Could not find sdpudcid in dashboard response. Status: %s, Response length: %d",
                response.status,
                len(text) if text else 0,
            )
            raise ValueError("Could not find sdpudcid in dashboard response")

    async def get_data(
        self,
        metric: str,
        view: str = "BILL",
        granularity: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> dict[str, Any]:
        """Get energy data from API.

        Args:
            metric: 'usage' or 'cost'
            view: 'BILL' or other view type
            granularity: 'MIN30', 'HOUR', 'DAY', etc.
            start: Start date in format 'YYYY-MM-DD+00:00'
            end: End date in format 'YYYY-MM-DD+00:00'
        """
        sdpudcid = await self.get_sdpudcid()
        session = await self._get_session()
        url = "https://smartliving.myaccount.mytnb.com.my/my_energy_request/timeseries"
        headers = {
            "Referer": f"https://smartliving.myaccount.mytnb.com.my/commodity/electric/{metric}",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.5",
            "X-Requested-With": "XMLHttpRequest",
            "X-Request": "JSON",
            "DNT": "1",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Host": "smartliving.myaccount.mytnb.com.my",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
        query_params: dict[str, Any] = {
            "metric": metric,
            "view": view,
            "sdpudcid": sdpudcid,
        }
        if granularity:
            query_params["granularity"] = granularity
        if start:
            query_params["start"] = start
        if end:
            query_params["end"] = end

        async with session.get(url, headers=headers, params=query_params) as response:
            response.raise_for_status()
            
            # Debug: log the actual URL and params being sent (with sensitive data redacted)
            _LOGGER.debug("API request URL: %s", _redact_url(str(response.url)))
            _LOGGER.debug("Query params: %s", _redact_dict(query_params))
            
            return await response.json()

    async def authenticate(self) -> bool:
        """Authenticate and initialize session."""
        _LOGGER.debug("Starting authentication flow")
        
        if not await self.login():
            _LOGGER.debug("Authentication failed at login step")
            return False
        
        if not await self.access_smartmeter():
            _LOGGER.debug("Authentication failed at smartmeter access step")
            return False
        
        _LOGGER.debug("Authentication flow completed successfully")
        return True
