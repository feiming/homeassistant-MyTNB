"""Shared fixtures for the MyTNB client tests.

api.py is imported by path because custom_components/mytnb/__init__.py
pulls in Home Assistant, which isn't needed (or installed) for these tests.

Instead of mocking the HTTP layer, tests run a small fake TNB portal on
localhost (aiohttp's TestServer) and point the client's URLs at it.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

_API_PATH = Path(__file__).resolve().parents[1] / "custom_components" / "mytnb" / "api.py"
_spec = importlib.util.spec_from_file_location("mytnb_api", _API_PATH)
api = importlib.util.module_from_spec(_spec)
sys.modules["mytnb_api"] = api
_spec.loader.exec_module(api)

SDPUDCID = "40000000000020022594"
LOGIN_PAGE = '<input name="wa" value="wsignin1.0" /><input name="wresult" value="token" />'
DASHBOARD_PAGE = f'<script>var x = {{"sdpudcid":"{SDPUDCID}"}};</script>'
SMARTMETER_URL = (
    "https://myaccount.mytnb.com.my/AccountManagement/SmartMeter/Index/TRIL?caNo=TESTCA"
)
SESSION_COOKIE_VALUE = "sessioncookievalue123"
# base64, so it holds the characters aiohttp quotes by default
AUTH_TOKEN = "eyJ0b2tlbiI6+dGVzdC/9Qw=="
_RAW_AUTH_TOKEN_RE = re.compile(r"(?:^|;\s*)e3AuthToken=([^;]*)")
LOGIN_REDIRECT_JSON = {
    "data": [],
    "html": "",
    "errorMessage": "",
    "errors": [],
    "fieldErrors": [],
    "redirect": True,
    "redirectTo": "/login",
}


class FakeTNB:
    """A configurable fake of the TNB portal.

    Response queues pop one entry per request and repeat their last entry
    once exhausted, so "keeps failing" and "recovers on retry" are both easy
    to express.
    """

    def __init__(self) -> None:
        self.login_body = LOGIN_PAGE
        self.dashboard_bodies: list[str] = [DASHBOARD_PAGE]
        # per-metric queues of dict (JSON payload) or int (HTTP status)
        self.timeseries: dict[str, list[dict | int]] = {}
        self.login_count = 0
        self.commodity_visits: list[str] = []

        self.app = web.Application()
        self.app.router.add_post("/api/sitecore/Account/Login", self._login)
        self.app.router.add_post("/SSO/SSOHandler", self._sso)
        self.app.router.add_get("/AccountManagement/SmartMeter/Index/TRIL", self._smartmeter)
        self.app.router.add_get("/dashboard", self._dashboard)
        self.app.router.add_get("/commodity/electric/{metric}", self._commodity)
        self.app.router.add_get("/my_energy_request/timeseries", self._timeseries)

    @staticmethod
    def _pop(queue: list) -> object:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    async def _login(self, request: web.Request) -> web.Response:
        self.login_count += 1
        return web.Response(text=self.login_body, content_type="text/html")

    async def _sso(self, request: web.Request) -> web.Response:
        response = web.Response(text="")
        response.set_cookie("ASP.NET_SessionId", SESSION_COOKIE_VALUE)
        return response

    async def _smartmeter(self, request: web.Request) -> web.Response:
        return web.Response(text="")

    async def _dashboard(self, request: web.Request) -> web.Response:
        return web.Response(text=self._pop(self.dashboard_bodies), content_type="text/html")

    async def _commodity(self, request: web.Request) -> web.Response:
        self.commodity_visits.append(request.match_info["metric"])
        response = web.Response(text="", content_type="text/html")
        response.set_cookie("e3AuthToken", AUTH_TOKEN)
        return response

    async def _timeseries(self, request: web.Request) -> web.Response:
        # Like TNB's smartliving site (issue #6): the auth token must come
        # back exactly as issued (browsers never quote cookie values), or the
        # request is bounced to login and the token deleted.
        match = _RAW_AUTH_TOKEN_RE.search(request.headers.get("Cookie", ""))
        if not match or match.group(1) != AUTH_TOKEN:
            entry: dict | int = LOGIN_REDIRECT_JSON
        else:
            entry = self._pop(self.timeseries[request.query.get("metric", "")])
        if isinstance(entry, int):
            return web.Response(status=entry)
        response = web.json_response(entry)
        if entry.get("redirect"):
            response.del_cookie("e3AuthToken")
        return response


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    """Make retries and request pacing instant so tests don't sleep."""
    monkeypatch.setattr(api, "_SESSION_LAG_DELAY", 0)
    monkeypatch.setattr(api, "_PAGE_VIEW_DELAY", 0)
    monkeypatch.setattr(api, "_BETWEEN_METRICS_DELAY", 0)


@pytest.fixture
async def fake_tnb():
    """The fake portal, running on localhost."""
    fake = FakeTNB()
    server = TestServer(fake.app, host="127.0.0.1")
    await server.start_server()
    # Addressed by name, not IP: aiohttp's cookie jar ignores cookies from
    # IP addresses, and the client must run on the production cookie jar.
    fake.base_url = f"http://localhost:{server.port}"
    yield fake
    await server.close()


@pytest.fixture
async def session():
    # The same cookie jar the integration gives its session.
    async with aiohttp.ClientSession(cookie_jar=api.create_cookie_jar()) as client_session:
        yield client_session


@pytest.fixture
def client(session, fake_tnb, monkeypatch):
    """A MyTNBClient whose endpoints point at the fake portal."""
    base = fake_tnb.base_url
    monkeypatch.setattr(api, "_LOGIN_URL", f"{base}/api/sitecore/Account/Login")
    monkeypatch.setattr(api, "_SSO_URL", f"{base}/SSO/SSOHandler")
    monkeypatch.setattr(api, "_ACCOUNT_BASE", base)
    monkeypatch.setattr(api, "_SMARTLIVING_BASE", base)
    monkeypatch.setattr(api, "_DASHBOARD_URL", f"{base}/dashboard")
    monkeypatch.setattr(api, "_TIMESERIES_URL", f"{base}/my_energy_request/timeseries")
    return api.MyTNBClient(session, "user@example.com", "secret", SMARTMETER_URL)
