import re

import aiohttp
import logging

# logging.basicConfig(level=logging.DEBUG)


class TNBSmartMeter:
    """A client for interacting with the TNB Smart Meter API."""

    def __init__(self) -> None:
        """Initialize the client."""
        self.SDPUDCID = None
        connector = aiohttp.TCPConnector(keepalive_timeout=30, limit=1)
        self.session = aiohttp.ClientSession(connector=connector)

    async def _get_login_details(self, username: str, password: str) -> dict:
        url = "https://www.mytnb.com.my/api/sitecore/Account/Login"
        payload = {"Email": username, "Password": password}

        async with self.session.post(
            url,
            data=payload,
        ) as response:
            response_text = await response.text()
            matches = re.findall(r'name="([^"]+)" value="([^"]*)"', response_text)
            return dict(matches)

    async def login(self, username: str, password: str) -> bool:
        """Authenticate to MyTNB website."""
        url = "https://myaccount.mytnb.com.my/SSO/SSOHandler"
        payload = await self._get_login_details(username, password)

        async with self.session.post(
            url,
            data=payload,
        ) as response:
            return response.status == 200

    async def _get_smartmeter_url(self) -> dict:
        """Extract Account details."""
        url = "https://myaccount.mytnb.com.my/AccountManagement/IndividualDashboard"
        headers = {
            "Host": "myaccount.mytnb.com.my",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Referer": "https://myaccount.mytnb.com.my/SSO/SSOHandler",
            "DNT": "1",
            "Connection": "keep-alive",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/png,image/svg+xml,*/*;q=0.8",
        }

        async with self.session.get(
            url,
            headers=headers,
        ) as response:
            response_text = await response.text()

            url_match = re.search(
                r'href="(/AccountManagement/SmartMeter/Index/TRIL\?caNo=[^"]+)"',
                response_text,
            )
            return url_match.group(1) if url_match else None

    async def login_smart_meter(self) -> bool:
        smartmeter_url = await self._get_smartmeter_url()
        url = f"https://myaccount.mytnb.com.my{smartmeter_url}"
        print(url)

        async with self.session.get(
            url,
        ) as response:
            # print(response.status)
            return response.status == 200

    async def get_sdpudcid(self) -> str:
        url = "https://smartliving.myaccount.mytnb.com.my/dashboard"
        headers = {
            "Host": "smartliving.myaccount.mytnb.com.my",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "DNT": "1",
            "Connection": "keep-alive",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/png,image/svg+xml,*/*;q=0.8",
        }

        async with self.session.get(
            url,
            headers=headers,
            allow_redirects=True,
        ) as response:
            response_text = await response.text()
            match = re.search(r'"sdpudcid":"(\d+)"', response_text)
            if match:
                sdpudcid = match.group(1)
                return sdpudcid
            return ""
        return ""

    async def get_data(self, query_params: dict) -> dict:
        """Get Data."""
        url = "https://smartliving.myaccount.mytnb.com.my/my_energy_request/timeseries"

        # Update query parameters with `sdpudcid`
        query_params["sdpudcid"] = await self._get_sdpudcid()
        print(query_params)
        headers = {
            "Referer": f'https://smartliving.myaccount.mytnb.com.my/commodity/electric/{query_params["metric"]}',
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0",
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

        # Make asynchronous GET request
        async with self.session.get(
            url, headers=headers, params=query_params
        ) as response:
            # Check if response is successful
            if response.status == 200:
                return await response.json()
            print(f"Request failed with status: {response.status}")
            return None

    # async def get_data(self, query_params):
    #     """Get Data."""
    #     url = "https://smartliving.myaccount.mytnb.com.my/my_energy_request/timeseries"
    #     headers = {
    #         "Referer": 'https://smartliving.myaccount.mytnb.com.my/commodity/electric/{query_params["metric"]}',
    #         "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:129.0) Gecko/20100101 Firefox/129.0",
    #         "Accept": "application/json",
    #         "Accept-Language": "en-US,en;q=0.5",
    #         "X-Requested-With": "XMLHttpRequest",
    #         "X-Request": "JSON",
    #         "DNT": "1",
    #         "Accept-Encoding": "gzip, deflate, br, zstd",
    #         "Host": "smartliving.myaccount.mytnb.com.my",
    #         "Sec-Fetch-Dest": "empty",
    #         "Sec-Fetch-Mode": "cors",
    #         "Sec-Fetch-Site": "same-origin",
    #     }

    #     query_params["sdpudcid"] = await self._get_sdpudcid()

    #     await self.start_session()

    #     async with self.session.get(
    #         url, headers=headers, params=query_params
    #     ) as response:
    #         return await response.json()

    async def close(self):
        """Close the aiohttp session."""
        await self.session.close()
