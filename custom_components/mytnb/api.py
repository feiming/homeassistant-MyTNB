"""API client for MyTNB smart meter data."""

from __future__ import annotations

import re
import logging
from datetime import datetime, timedelta
from typing import Any

import requests

_LOGGER = logging.getLogger(__name__)


class MyTNBAPI:
    """API client for MyTNB."""

    def __init__(self, username: str, password: str, smartmeter_url: str, sdpudcid: str) -> None:
        """Initialize the API client."""
        self.username = username
        self.password = password
        self.smartmeter_url = smartmeter_url
        self.session = requests.Session()
        self._sdpudcid = sdpudcid

    def get_login_details(self) -> dict[str, str]:
        """Get login form details."""
        url = "https://www.mytnb.com.my/api/sitecore/Account/Login"
        payload = {"Email": self.username, "Password": self.password}
        response = self.session.request("POST", url, data=payload)
        matches = re.findall(r'name="([^"]+)" value="([^"]*)"', response.text)
        return {name: value for name, value in matches}

    def login(self) -> bool:
        """Login to MyTNB."""
        url = "https://myaccount.mytnb.com.my/SSO/SSOHandler"
        payload = self.get_login_details()
        response = self.session.request("POST", url, data=payload)
        if response.status_code == 200:
            return True
        return False

    def get_smartmeter_path(self) -> str:
        """Extract smartmeter path from URL."""
        pattern = r'/AccountManagement/SmartMeter/Index/TRIL\?caNo=[^"\s]+'
        match = re.search(pattern, self.smartmeter_url)
        if match:
            return match.group(0)
        raise ValueError("Could not find smartmeter URL path")

    def access_smartmeter(self) -> bool:
        """Access smartmeter page."""
        smartmeter_path = self.get_smartmeter_path()
        url = f"https://myaccount.mytnb.com.my{smartmeter_path}"
        response = self.session.request("GET", url)
        return response.status_code == 200

    def get_sdpudcid(self) -> str:
        """Get SDPUDCID (provided by user)."""
        if not self._sdpudcid:
            raise ValueError("sdpudcid not set")
        return self._sdpudcid

    def get_data(
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
        sdpudcid = self.get_sdpudcid()
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

        response = self.session.request("GET", url, headers=headers, params=query_params)
        response.raise_for_status()
        
        # Debug: log the actual URL and params being sent
        if _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(f"API request URL: {response.url}")
            _LOGGER.debug(f"Query params: {query_params}")
        
        return response.json()

    def authenticate(self) -> bool:
        """Authenticate and initialize session."""
        if not self.login():
            return False
        if not self.access_smartmeter():
            return False
        return True
