"""The notification sender cannot leave loopback through proxies or redirects."""

from email.message import Message
from io import BytesIO
import socket
import urllib.request
from urllib.response import addinfourl

import pytest

from nxt_edge_interventions import InterventionError
from scripts.edge_intervention_service_v0 import HttpNotificationTransport


def test_environment_proxy_cannot_change_the_socket_destination(monkeypatch) -> None:
    monkeypatch.setenv("http_proxy", "http://192.0.2.10:18080")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setattr(urllib.request, "_opener", None)
    destinations = []

    def refuse_connection(address, *args, **kwargs):
        destinations.append(address)
        raise ConnectionRefusedError("socket-free transport test")

    monkeypatch.setattr(socket, "create_connection", refuse_connection)
    transport = HttpNotificationTransport("http://127.0.0.1:18081/notifications", 1, "127.0.0.1")
    result, receipt, _ = transport.send({})
    assert destinations == [("127.0.0.1", 18081)]
    assert result == "unknown" and receipt is None


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
@pytest.mark.parametrize("target", ["http://192.0.2.20:18080/notifications", "/elsewhere"])
def test_redirect_is_refused_without_a_second_request(monkeypatch, status, target) -> None:
    """Synthetic HTTP responses exercise urllib's real redirect processing, without sockets."""
    requests = []

    def respond(_handler, request):
        requests.append((request.full_url, request.get_method()))
        headers = Message()
        headers["Location"] = target
        response = addinfourl(BytesIO(b'{"receipt_id":"must-not-be-accepted"}'), headers,
                              request.full_url, status if len(requests) == 1 else 200)
        response.msg = "synthetic response"
        return response

    monkeypatch.setattr(urllib.request.HTTPHandler, "http_open", respond)
    monkeypatch.setattr(urllib.request, "_opener", None)
    url = "http://127.0.0.1:18081/notifications"
    result, receipt, detail = HttpNotificationTransport(url, 1, "127.0.0.1").send({})
    assert requests == [(url, "POST")]
    assert (result, receipt, detail) == ("failed", None, f"http {status}")


@pytest.mark.parametrize("url", [
    "http://192.0.2.20:18081/notifications",
    "http://localhost:18081/notifications",
    "http://127.0.0.2:18081/notifications",
    "https://127.0.0.1:18081/notifications",
    "http://user@127.0.0.1:18081/notifications",
])
def test_transport_validates_the_url_it_will_send_to(url) -> None:
    with pytest.raises(InterventionError, match="receiver URL"):
        HttpNotificationTransport(url, 1, "127.0.0.1")


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1"])
def test_loopback_receipt_still_means_delivered(monkeypatch, host) -> None:
    requests = []

    def respond(_handler, request):
        requests.append((request.full_url, request.get_method()))
        response = addinfourl(BytesIO(b'{"receipt_id":"local-receipt","duplicate":false}'),
                              Message(), request.full_url, 200)
        response.msg = "OK"
        return response

    monkeypatch.setattr(urllib.request.HTTPHandler, "http_open", respond)
    netloc = f"[{host}]" if ":" in host else host
    url = f"http://{netloc}:18081/notifications"
    assert HttpNotificationTransport(url, 1, host).send({}) == ("delivered", "local-receipt", "receipt")
    assert requests == [(url, "POST")]
