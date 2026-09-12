"""Transport adapter behaviour that the in-memory double cannot cover."""

from __future__ import annotations

import pytest

from scripts.edge_task_transport import InMemoryBroker


def test_paho_publish_while_disconnected_never_enters_paho_outbox() -> None:
    pytest.importorskip("paho.mqtt.client")
    from scripts.edge_task_transport import PahoClient

    client = PahoClient("t", "127.0.0.1", 1, 5)  # never connected

    def forbidden(*args, **kwargs):
        raise AssertionError("paho publish called while disconnected")

    client._client.publish = forbidden  # type: ignore[method-assign]
    handle = client.publish("nxt/v1/sites/s/robots/r/task/request", b"x", 1)
    assert handle.wait(0.1) is False


def test_in_memory_reconnect_redelivers_inflight_in_original_order() -> None:
    broker = InMemoryBroker()
    seen: list[int] = []
    client = broker.client("c")
    client.set_handlers(on_message=lambda d: seen.append(d.mid), on_connect=lambda p: None, on_disconnect=lambda r: None)
    client.connect()
    client.subscribe("t", 1)
    publisher = broker.client("p")
    publisher.set_handlers(on_message=lambda d: None, on_connect=lambda p: None, on_disconnect=lambda r: None)
    publisher.connect()
    for n in range(3):
        publisher.publish("t", str(n).encode(), 1)
    broker.pump()
    assert seen == [1, 2, 3]  # delivered, never acknowledged
    client.disconnect()
    seen.clear()
    client.connect()
    broker.pump()
    assert seen == [1, 2, 3]
