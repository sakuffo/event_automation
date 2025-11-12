from wix_client import WixClient


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


def test_iter_rsvps_applies_filter(monkeypatch):
    client = make_client()

    payloads = [{"rsvps": [{"id": "r1"}], "pagingMetadata": {}}]
    calls = []

    def fake_request(method, endpoint, **kwargs):
        calls.append(kwargs["json"])
        return DummyResponse(payloads.pop(0))

    monkeypatch.setattr(client, "_request", fake_request)

    rsvps = list(client.iter_rsvps("event-1", page_size=25))

    assert rsvps == [{"id": "r1"}]
    assert calls[0]["query"]["paging"] == {"limit": 25}
    assert calls[0]["query"]["filter"] == {"eventId": "event-1"}


def test_list_events_respects_limit(monkeypatch):
    client = make_client()

    payloads = [{"events": [{"id": "a"}, {"id": "b"}], "pagingMetadata": {}}]

    def fake_request(method, endpoint, **kwargs):
        return DummyResponse(payloads.pop(0))

    monkeypatch.setattr(client, "_request", fake_request)

    events = client.list_events(limit=1)
    assert len(events) == 1

