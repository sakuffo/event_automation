import requests

from event_sync import wix_client as wix_client_module
from event_sync.wix_client import WixClient


class DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def make_client() -> WixClient:
    return WixClient(api_key="test", site_id="site", account_id="account")


def test_iter_events_uses_cursor_pagination(monkeypatch):
    client = make_client()

    payloads = [
        {
            "events": [{"id": "a"}, {"id": "b"}],
            "pagingMetadata": {"nextCursor": "cursor123"},
        },
        {"events": [{"id": "c"}], "pagingMetadata": {}},
    ]
    calls = []

    def fake_request(method, endpoint, **kwargs):
        assert method == "POST"
        calls.append(kwargs["json"])
        return DummyResponse(payloads.pop(0))

    monkeypatch.setattr(client, "_request", fake_request)

    events = list(client.iter_events(page_size=2))

    assert [event["id"] for event in events] == ["a", "b", "c"]
    assert calls[0]["query"]["paging"] == {"limit": 2}
    assert calls[1]["query"]["paging"] == {"limit": 2, "cursor": "cursor123"}
    assert payloads == []


def test_iter_events_falls_back_to_offset(monkeypatch):
    client = make_client()

    payloads = [
        {
            "events": [{"id": "a"}, {"id": "b"}],
            "pagingMetadata": {},
        },
        {
            "events": [{"id": "c"}, {"id": "d"}],
            "pagingMetadata": {},
        },
        {
            "events": [],
            "pagingMetadata": {},
        },
    ]
    calls = []

    def fake_request(method, endpoint, **kwargs):
        calls.append(kwargs["json"])
        return DummyResponse(payloads.pop(0))

    monkeypatch.setattr(client, "_request", fake_request)

    events = list(client.iter_events(page_size=2))

    assert [event["id"] for event in events] == ["a", "b", "c", "d"]
    assert calls[0]["query"]["paging"] == {"limit": 2}
    assert calls[1]["query"]["paging"] == {"limit": 2, "offset": 2}
    assert calls[2]["query"]["paging"] == {"limit": 2, "offset": 4}


def test_list_events_respects_limit(monkeypatch):
    client = make_client()

    payloads = [{"events": [{"id": "a"}, {"id": "b"}], "pagingMetadata": {}}]

    def fake_request(method, endpoint, **kwargs):
        return DummyResponse(payloads.pop(0))

    monkeypatch.setattr(client, "_request", fake_request)

    events = client.list_events(limit=1)
    assert len(events) == 1



# ---------------------------------------------------------------------------
# Transport retry matrix (Session-level)
# ---------------------------------------------------------------------------


class FakeHttpResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)


class FakeSession:
    """Returns queued responses (or raises queued exceptions) per request."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_transport_client(outcomes, monkeypatch):
    client = make_client()
    client._session = FakeSession(outcomes)
    monkeypatch.setattr(wix_client_module.time, "sleep", lambda s: None)
    return client


def test_transient_502_on_query_post_is_retried(monkeypatch):
    client = make_transport_client(
        [FakeHttpResponse(502), FakeHttpResponse(200, {"events": []})], monkeypatch
    )
    response = client._request("POST", "/events/v3/events/query", json={})
    assert response.json() == {"events": []}
    assert len(client._session.calls) == 2


def test_transient_502_on_create_post_is_not_retried(monkeypatch):
    client = make_transport_client([FakeHttpResponse(502)], monkeypatch)
    try:
        client._request("POST", "/events/v3/events", json={})
        assert False, "expected HTTPError"
    except requests.exceptions.HTTPError:
        pass
    # No second attempt: a create may have been processed server-side.
    assert len(client._session.calls) == 1


def test_rate_limit_is_retried_even_for_creates(monkeypatch):
    client = make_transport_client(
        [FakeHttpResponse(429), FakeHttpResponse(200, {"event": {"id": "e1"}})],
        monkeypatch,
    )
    response = client._request("POST", "/events/v3/events", json={})
    assert response.json()["event"]["id"] == "e1"
    assert len(client._session.calls) == 2


def test_503_on_get_is_retried(monkeypatch):
    client = make_transport_client(
        [FakeHttpResponse(503), FakeHttpResponse(200, {"event": {}})], monkeypatch
    )
    response = client._request("GET", "/events/v3/events/abc")
    assert response.status_code == 200
    assert len(client._session.calls) == 2


def test_timeout_sleeps_and_retries(monkeypatch):
    sleeps = []
    client = make_client()
    client._session = FakeSession(
        [requests.exceptions.Timeout(), FakeHttpResponse(200, {})]
    )
    monkeypatch.setattr(wix_client_module.time, "sleep", lambda s: sleeps.append(s))
    response = client._request("GET", "/events/v3/events/abc")
    assert response.status_code == 200
    assert sleeps == [1]


def test_persistent_502_on_get_raises_after_retries(monkeypatch):
    client = make_transport_client(
        [FakeHttpResponse(502), FakeHttpResponse(502), FakeHttpResponse(502)],
        monkeypatch,
    )
    try:
        client._request("GET", "/events/v3/events/abc")
        assert False, "expected HTTPError"
    except requests.exceptions.HTTPError:
        pass
    assert len(client._session.calls) == 3
