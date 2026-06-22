"""
Tests for WebmotorsClient HTTP layer:
  - _get retry on 403 (single remint, then success)
  - _get second failure propagates (returns 403 response; caller raises via raise_for_status)
  - search: URL shape and response passthrough
  - get_detail: missing-arg guard, url= branch, listing= branch
  - iter_details: UniqueId==0 items skipped, used items fetched

Mock library: requests-mock (fixture `requests_mock`).
Client is constructed WITHOUT minting by passing a pre-built fresh WebmotorsSession.
"""

from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pytest
import requests

import webmotors_scraper as wm
from webmotors_scraper import (
    BASE_URL,
    WebmotorsClient,
    WebmotorsSession,
    detail_url_from_listing,
)
from tests.conftest import make_listing, make_detail  # plain functions, not fixtures


# ---------------------------------------------------------------------------
# Local helpers
# ---------------------------------------------------------------------------

def _fresh_session(
    *,
    cookies: dict[str, str] | None = None,
    user_agent: str = "TestUA/1.0",
) -> WebmotorsSession:
    """Build a WebmotorsSession that is_fresh() == True (no browser needed)."""
    if cookies is None:
        cookies = {"_px3": "testtoken", "_pxvid": "y"}
    return WebmotorsSession(
        cookies=cookies,
        user_agent=user_agent,
        minted_at=time.time(),
    )


def _client_with_fresh_session() -> WebmotorsClient:
    """Return a WebmotorsClient backed by a fresh in-memory session."""
    return WebmotorsClient(session=_fresh_session())


_DUMMY_URL = f"{BASE_URL}/api/detail/car/honda/city/15-i-vtec/4-portas/2024/12345"


# ---------------------------------------------------------------------------
# _get: retry on 403 → 200
# ---------------------------------------------------------------------------


def test_get_retries_on_403_and_returns_200(
    requests_mock: Any,
    webmotors_cookie_env: None,  # so forced remint uses env-bypass, not Chrome
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    First call returns 403 → _get calls ensure_session(force=True) then retries.
    The retry returns 200. Final response must be the 200.
    request count must be exactly 2.
    """
    payload = {"UniqueId": 1, "ok": True}
    requests_mock.get(
        _DUMMY_URL,
        [
            {"status_code": 403},
            {"json": payload, "status_code": 200},
        ],
    )

    # Spy: track how many times ensure_session(force=True) is called.
    remint_calls: list[dict[str, Any]] = []
    original_ensure = WebmotorsClient.ensure_session

    def spy_ensure(
        self: WebmotorsClient, *, force: bool = False, clear_profile: bool = False
    ) -> WebmotorsSession:
        if force:
            remint_calls.append({"force": force, "clear_profile": clear_profile})
        return original_ensure(self, force=force, clear_profile=clear_profile)

    monkeypatch.setattr(WebmotorsClient, "ensure_session", spy_ensure)

    client = _client_with_fresh_session()
    resp = client._get(_DUMMY_URL)

    assert resp.status_code == 200
    assert resp.json() == payload
    assert requests_mock.call_count == 2
    # ensure_session(force=True) must have been called exactly once for the remint
    assert len(remint_calls) == 1


# ---------------------------------------------------------------------------
# _get: second 403 propagates (no infinite retry)
# ---------------------------------------------------------------------------


def test_get_second_403_propagates(
    requests_mock: Any,
    webmotors_cookie_env: None,
) -> None:
    """
    When both calls return 403, _get returns the 403 response (does not raise
    itself). search/get_detail callers would call raise_for_status; here we
    call get_detail (which wraps _get + raise_for_status) and confirm it raises
    requests.exceptions.HTTPError.

    Also confirm the request was made at most twice (no runaway retry).
    """
    requests_mock.get(
        _DUMMY_URL,
        [
            {"status_code": 403},
            {"status_code": 403},
        ],
    )

    client = _client_with_fresh_session()

    # get_detail calls _get then raise_for_status — so HTTPError is raised on
    # the propagated 403 response.
    with pytest.raises(requests.exceptions.HTTPError):
        client.get_detail(url=_DUMMY_URL)

    # At most 2 requests: original + one retry; must NOT have looped further.
    assert requests_mock.call_count == 2


# ---------------------------------------------------------------------------
# search: URL shape and payload passthrough
# ---------------------------------------------------------------------------


def test_search_returns_payload_and_url_shape(
    requests_mock: Any,
    search_payload: dict[str, Any],
) -> None:
    """
    search(make='honda', model='city') must:
      - return the raw JSON dict from the mock
      - hit /api/search/car with a url= query param whose decoded value
        contains '/carros/estoque' with marca1=honda and modelo1=city
    """
    # Match any request to /api/search/car (the query string is complex)
    search_endpoint = re.compile(r".*/api/search/car.*")
    requests_mock.get(search_endpoint, json=search_payload, status_code=200)

    client = _client_with_fresh_session()
    result = client.search(make="honda", model="city")

    assert result == search_payload

    # Inspect the actual request URL
    last_req = requests_mock.last_request
    parsed = urlparse(last_req.url)
    qs = parse_qs(parsed.query, keep_blank_values=True)

    # The url= param must be present and decode to the inner estoque URL
    assert "url" in qs, "search must send a 'url' query param"
    decoded_inner = unquote(qs["url"][0])
    assert "/carros/estoque" in decoded_inner
    assert "marca1=honda" in decoded_inner
    assert "modelo1=city" in decoded_inner

    # displayPerPage, actualPage, order must also be present
    assert qs.get("displayPerPage") == ["24"]
    assert qs.get("actualPage") == ["1"]
    assert qs.get("order") == ["1"]


def test_search_forwards_extra_filters_and_encodes_values(
    requests_mock: Any,
    search_payload: dict[str, Any],
) -> None:
    """search() forwards filters used by FastAPI: localidade, cor, ano_de, ano_ate."""
    search_endpoint = re.compile(r".*/api/search/car.*")
    requests_mock.get(search_endpoint, json=search_payload, status_code=200)

    client = _client_with_fresh_session()
    extra = wm._build_search_extra(
        localidade="São Paulo",
        cor="Branco",
        ano_de=2019,
        ano_ate=2022,
    )
    result = client.search(make="honda", model="city", extra=extra)

    assert result == search_payload

    parsed = urlparse(requests_mock.last_request.url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    decoded_inner = unquote(qs["url"][0])
    assert "localizacao=São+Paulo" in decoded_inner
    assert "cor1=Branco" in decoded_inner
    assert "anode=2019" in decoded_inner
    assert "anoate=2022" in decoded_inner


# ---------------------------------------------------------------------------
# get_detail: missing-arg guard
# ---------------------------------------------------------------------------


def test_get_detail_requires_listing_or_url() -> None:
    """Calling get_detail() with neither listing= nor url= raises ValueError."""
    client = _client_with_fresh_session()
    with pytest.raises(ValueError, match="listing=|url="):
        client.get_detail()


# ---------------------------------------------------------------------------
# get_detail: url= branch
# ---------------------------------------------------------------------------


def test_get_detail_with_url(
    requests_mock: Any,
    detail_payload: dict[str, Any],
) -> None:
    """get_detail(url=...) fetches that URL and returns the parsed JSON."""
    requests_mock.get(_DUMMY_URL, json=detail_payload, status_code=200)

    client = _client_with_fresh_session()
    result = client.get_detail(url=_DUMMY_URL)

    assert result == detail_payload
    assert requests_mock.call_count == 1
    assert requests_mock.last_request.url == _DUMMY_URL


# ---------------------------------------------------------------------------
# get_detail: listing= branch (derives URL via detail_url_from_listing)
# ---------------------------------------------------------------------------


def test_get_detail_with_listing(
    requests_mock: Any,
    detail_payload: dict[str, Any],
) -> None:
    """
    get_detail(listing=...) derives the URL via detail_url_from_listing and
    fetches it. We mock the derived URL with a regex to tolerate slug details.
    """
    listing = make_listing()
    expected_url = detail_url_from_listing(listing)

    # Register the exact derived URL
    requests_mock.get(expected_url, json=detail_payload, status_code=200)

    client = _client_with_fresh_session()
    result = client.get_detail(listing=listing)

    assert result == detail_payload
    # Confirm the fetched URL matches what detail_url_from_listing produced
    assert requests_mock.last_request.url == expected_url


# ---------------------------------------------------------------------------
# iter_details: skips UniqueId==0; fetches all UniqueId>0
# ---------------------------------------------------------------------------


def test_iter_details_skips_zero_unique_ids(
    requests_mock: Any,
    search_payload: dict[str, Any],
    detail_payload: dict[str, Any],
) -> None:
    """
    iter_details must skip SearchResults where UniqueId==0 (0km placeholders)
    and call get_detail only for UniqueId>0 items.

    The search fixture has 15 items: 3 zero-km (UniqueId=0) + 12 used (UniqueId>0).
    Expected: 12 detail calls, 12 items in result.
    """
    search_endpoint = re.compile(r".*/api/search/car.*")
    requests_mock.get(search_endpoint, json=search_payload, status_code=200)

    # Match ALL /api/detail/car/... paths with a regex
    detail_endpoint = re.compile(r".*/api/detail/car/.*")
    requests_mock.get(detail_endpoint, json=detail_payload, status_code=200)

    client = _client_with_fresh_session()
    results = client.iter_details(make="honda", model="city", pages=1)

    # Count detail requests (exclude the search request)
    detail_requests = [
        r for r in requests_mock.request_history if "/api/detail/car/" in r.url
    ]
    used_count = sum(
        1
        for item in search_payload.get("SearchResults", [])
        if item.get("UniqueId", 0) > 0
    )

    assert len(results) == used_count  # 12 used items
    assert len(detail_requests) == used_count  # exactly 12 detail calls
    # Zero-km items must NOT appear in detail requests
    zero_ids = {
        str(item["UniqueId"])
        for item in search_payload.get("SearchResults", [])
        if item.get("UniqueId", 0) == 0
    }
    for req in detail_requests:
        # None of the zero UniqueId values should appear in any detail URL
        # (UniqueId=0 so "0" would be the last path segment — unlikely to match
        # any used-car URL, but let's be explicit)
        for zid in zero_ids:
            assert not req.url.endswith(f"/{zid}"), (
                f"detail request for UniqueId=0 must not happen: {req.url}"
            )
