"""Transport port and the two V0 implementations for the Edge Task rehearsal.

SIMULATION ONLY.  The ``nxt_edge_task`` package is transport-neutral; this
composition-root module owns the only transport code:

* ``InMemoryBroker`` -- a synchronous, fault-injectable broker double used
  by the logic tests (no sockets, no threads, deterministic delivery);
* ``PahoClient`` -- the MQTT 3.1.1 adapter over ``paho-mqtt`` for the local
  Mosquitto rehearsal: stable client id, ``clean_session=False``, QoS 1
  subscriptions for task topics, QoS 0 for status, manual PUBACK so the
  receiver acknowledges only after its journal append is durable.

Delivery semantics exposed to the gateway and the mock robot:

* ``publish()`` returns a handle whose ``wait(timeout)`` is the hop-1
  confirmation (broker PUBACK to the publisher) -- proof the broker took
  the packet, nothing more;
* ``ack(delivery)`` is the hop-2 manual PUBACK for a QoS 1 delivery; a
  QoS 0 delivery has no acknowledgement;
* ``on_connect(session_present)`` lets the process detect a lost persistent
  session (broker restart) and run its reconnect procedure.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from nxt_edge_task.contracts import assert_local_broker_endpoint


@dataclass(frozen=True, slots=True)
class Delivery:
    topic: str
    payload: bytes
    qos: int
    retain: bool
    mid: int
    duplicate: bool = False


class PublishHandle(Protocol):
    def wait(self, timeout_s: float) -> bool: ...


class TransportClient(Protocol):
    client_id: str

    def set_handlers(
        self,
        *,
        on_message: Callable[[Delivery], None],
        on_connect: Callable[[bool], None],
        on_disconnect: Callable[[str], None],
    ) -> None: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def subscribe(self, topic: str, qos: int) -> None: ...

    def publish(self, topic: str, payload: bytes, qos: int) -> PublishHandle: ...

    def ack(self, delivery: Delivery) -> None: ...

    def loop(self, timeout_s: float) -> None: ...


# --------------------------------------------------------------------------
# In-memory broker double
# --------------------------------------------------------------------------


@dataclass
class _ImmediateHandle:
    confirmed: bool

    def wait(self, timeout_s: float) -> bool:  # noqa: ARG002 - signature parity
        return self.confirmed


@dataclass
class _Session:
    client_id: str
    clean_session: bool
    subscriptions: dict[str, int] = field(default_factory=dict)
    queue: deque[Delivery] = field(default_factory=deque)
    inflight: dict[int, Delivery] = field(default_factory=dict)
    connected: bool = False
    next_mid: int = 1
    client: "InMemoryClient | None" = None


class InMemoryBroker:
    """Deterministic broker double with fault injection for tests.

    Fault hooks (all optional):

    * ``drop`` -- ``(topic, payload) -> bool``; a True result discards the
      publication at the broker (simulates loss);
    * ``hold`` -- ``(topic, payload) -> bool``; held publications are parked
      until ``release_held()``;
    * ``withhold_puback`` -- ``(topic, payload) -> bool``; the publisher's
      hop-1 confirmation reports False although the broker delivered it;
    * ``duplicate`` -- ``(topic, payload) -> bool``; the delivery is queued
      twice (simulates broker redelivery).
    """

    def __init__(self) -> None:
        self.sessions: dict[str, _Session] = {}
        self.drop: Callable[[str, bytes], bool] | None = None
        self.hold: Callable[[str, bytes], bool] | None = None
        self.withhold_puback: Callable[[str, bytes], bool] | None = None
        self.duplicate: Callable[[str, bytes], bool] | None = None
        self.held: list[tuple[str, bytes, int]] = []
        self.publications: list[tuple[str, str, bytes, int]] = []

    def client(self, client_id: str, *, clean_session: bool = False) -> "InMemoryClient":
        return InMemoryClient(self, client_id, clean_session)

    # -- broker-side operations --------------------------------------------

    def _session(self, client_id: str, clean_session: bool) -> _Session:
        session = self.sessions.get(client_id)
        if session is None or clean_session:
            session = _Session(client_id=client_id, clean_session=clean_session)
            self.sessions[client_id] = session
        return session

    def route(self, publisher_id: str, topic: str, payload: bytes, qos: int) -> bool:
        self.publications.append((publisher_id, topic, payload, qos))
        if self.drop is not None and self.drop(topic, payload):
            return not (self.withhold_puback is not None and self.withhold_puback(topic, payload))
        if self.hold is not None and self.hold(topic, payload):
            self.held.append((topic, payload, qos))
            return True
        self._enqueue(topic, payload, qos)
        if self.duplicate is not None and self.duplicate(topic, payload):
            self._enqueue(topic, payload, qos, duplicate=True)
        if self.withhold_puback is not None and self.withhold_puback(topic, payload):
            return False
        return True

    def _enqueue(self, topic: str, payload: bytes, qos: int, *, duplicate: bool = False) -> None:
        for session in self.sessions.values():
            sub_qos = session.subscriptions.get(topic)
            if sub_qos is None:
                continue
            effective = min(qos, sub_qos)
            if effective == 0 and not session.connected:
                continue  # QoS 0 is never queued for an offline session
            delivery = Delivery(topic=topic, payload=payload, qos=effective, retain=False, mid=session.next_mid, duplicate=duplicate)
            session.next_mid += 1
            session.queue.append(delivery)

    def release_held(self) -> None:
        held, self.held = self.held, []
        for topic, payload, qos in held:
            self._enqueue(topic, payload, qos)

    def pump(self, *, max_rounds: int = 64) -> int:
        """Deliver queued messages to connected clients until quiescent."""

        delivered = 0
        for _ in range(max_rounds):
            progressed = False
            for session in list(self.sessions.values()):
                if not session.connected or session.client is None:
                    continue
                while session.queue:
                    delivery = session.queue.popleft()
                    if delivery.qos >= 1:
                        session.inflight[delivery.mid] = delivery
                    session.client._deliver(delivery)
                    delivered += 1
                    progressed = True
            if not progressed:
                break
        return delivered

    def restart(self) -> None:
        """Nonpersistent broker restart: every session, queue, and inflight is lost."""

        for session in self.sessions.values():
            session.connected = False
            if session.client is not None:
                session.client._broker_gone()
        self.sessions = {}

    def disconnect_client(self, client_id: str) -> None:
        session = self.sessions.get(client_id)
        if session is not None and session.connected:
            session.connected = False
            if session.client is not None:
                session.client._on_disconnect("broker_closed")


class InMemoryClient:
    def __init__(self, broker: InMemoryBroker, client_id: str, clean_session: bool) -> None:
        self.broker = broker
        self.client_id = client_id
        self.clean_session = clean_session
        self._on_message: Callable[[Delivery], None] | None = None
        self._on_connect: Callable[[bool], None] | None = None
        self._on_disconnect: Callable[[str], None] | None = None
        self._session: _Session | None = None
        self.acked: list[int] = []

    def set_handlers(self, *, on_message, on_connect, on_disconnect) -> None:
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

    def connect(self) -> None:
        existing = self.broker.sessions.get(self.client_id)
        session_present = existing is not None and not self.clean_session
        session = self.broker._session(self.client_id, self.clean_session)
        session.connected = True
        session.client = self
        self._session = session
        # Unacknowledged inflight deliveries are redelivered on reconnect.
        for mid, delivery in sorted(session.inflight.items(), reverse=True):
            session.queue.appendleft(Delivery(delivery.topic, delivery.payload, delivery.qos, delivery.retain, mid, duplicate=True))
        session.inflight.clear()
        if self._on_connect is not None:
            self._on_connect(session_present)

    def disconnect(self) -> None:
        if self._session is not None:
            self._session.connected = False
        if self._on_disconnect is not None:
            self._on_disconnect("client_disconnect")

    def _broker_gone(self) -> None:
        self._session = None
        if self._on_disconnect is not None:
            self._on_disconnect("broker_restart")

    def _on_disconnect_hook(self, reason: str) -> None:
        if self._on_disconnect is not None:
            self._on_disconnect(reason)

    def subscribe(self, topic: str, qos: int) -> None:
        assert self._session is not None, "subscribe before connect"
        self._session.subscriptions[topic] = qos

    def publish(self, topic: str, payload: bytes, qos: int) -> PublishHandle:
        if self._session is None or not self._session.connected:
            return _ImmediateHandle(False)
        return _ImmediateHandle(self.broker.route(self.client_id, topic, payload, qos))

    def ack(self, delivery: Delivery) -> None:
        if delivery.qos == 0:
            return
        if self._session is not None:
            self._session.inflight.pop(delivery.mid, None)
        self.acked.append(delivery.mid)

    def loop(self, timeout_s: float) -> None:  # noqa: ARG002 - synchronous double
        self.broker.pump()

    def _deliver(self, delivery: Delivery) -> None:
        if self._on_message is not None:
            self._on_message(delivery)

    def _on_disconnect_reason(self, reason: str) -> None:
        if self._on_disconnect is not None:
            self._on_disconnect(reason)

    # kept for InMemoryBroker.disconnect_client
    def _on_disconnect(self, reason: str) -> None:  # type: ignore[override]
        self._on_disconnect_reason(reason)


# --------------------------------------------------------------------------
# Paho adapter (real local Mosquitto)
# --------------------------------------------------------------------------


def _paho():
    try:
        import paho.mqtt.client as mqtt
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("install the edge-gateway extra (paho-mqtt) to use MQTT") from exc
    return mqtt


class _PahoHandle:
    """Hop-1 confirmation: pumps the single-threaded network loop until PUBACK."""

    def __init__(self, info: Any, pump: Callable[[float], int], success_code: int) -> None:
        self._info = info
        self._pump = pump
        self._success = success_code

    def wait(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                if self._info.is_published():
                    return True
            except RuntimeError:
                # paho raises when the client was not connected at publish
                # time: the packet never reached the broker.  Unconfirmed.
                return False
            if time.monotonic() >= deadline:
                return False
            if self._pump(0.05) != self._success:
                return False


class PahoClient:
    """MQTT 3.1.1 adapter with a persistent session and manual PUBACK."""

    def __init__(self, client_id: str, host: str, port: int, keepalive_s: int, *, clean_session: bool = False) -> None:
        # The local-broker boundary is enforced here as well as in the config
        # parser: no caller can hand this adapter a non-loopback or
        # conventional-port endpoint, whatever object it built the values from.
        assert_local_broker_endpoint(host, port)
        mqtt = _paho()
        self.client_id = client_id
        self._host, self._port, self._keepalive = host, port, keepalive_s
        self._client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            clean_session=clean_session,
            reconnect_on_failure=True,
        )
        self._client.manual_ack_set(True)
        self._mqtt = mqtt
        self._on_message: Callable[[Delivery], None] | None = None
        self._on_connect: Callable[[bool], None] | None = None
        self._on_disconnect: Callable[[str], None] | None = None
        self._subscriptions: dict[str, int] = {}
        self._lock = threading.Lock()
        self._connected = False
        self._ever_connected = False
        self._lost = False
        self._next_reconnect_at = 0.0
        self._client.on_connect = self._paho_connect
        self._client.on_message = self._paho_message
        self._client.on_disconnect = self._paho_disconnect

    def set_handlers(self, *, on_message, on_connect, on_disconnect) -> None:
        self._on_message = on_message
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect

    @property
    def connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        assert_local_broker_endpoint(self._host, self._port)
        result = self._client.connect(self._host, self._port, keepalive=self._keepalive)
        if result != self._mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT connect returned {result}")
        self._ever_connected = True
        self._lost = False
        self._next_reconnect_at = time.monotonic() + 1.0

    def disconnect(self) -> None:
        self._ever_connected = False
        self._client.disconnect()

    def subscribe(self, topic: str, qos: int) -> None:
        self._subscriptions[topic] = qos
        result, _mid = self._client.subscribe(topic, qos)
        if result != self._mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT subscribe returned {result}")

    def publish(self, topic: str, payload: bytes, qos: int) -> PublishHandle:
        if not self._connected:
            # Never hand paho a packet while the socket is known down: it would
            # sit in paho's memory-only outbox and flush on reconnect behind the
            # journal's back.  Unconfirmed; the caller retries under its bounds.
            return _ImmediateHandle(False)
        info = self._client.publish(topic, payload=payload, qos=qos, retain=False)
        return _PahoHandle(info, lambda t: self._client.loop(timeout=t), self._mqtt.MQTT_ERR_SUCCESS)

    def ack(self, delivery: Delivery) -> None:
        if delivery.qos == 0:
            return
        result = self._client.ack(delivery.mid, delivery.qos)
        if result != self._mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"manual PUBACK returned {result}")

    def loop(self, timeout_s: float) -> None:
        # paho's manual loop() never reconnects on its own; a lost broker
        # (restart) is retried here with a fixed one-second backoff.  The
        # session_present flag on the resulting on_connect tells the process
        # whether its persistent session survived.
        if self._ever_connected and self._lost and not self._connected:
            if time.monotonic() >= self._next_reconnect_at:
                self._next_reconnect_at = time.monotonic() + 1.0
                try:
                    self._client.reconnect()
                except OSError:
                    # Broker still down: no socket to select on, so do not
                    # busy-spin the process loop.
                    time.sleep(min(timeout_s, 0.1))
                    return
        # Always pump: a fresh reconnect needs the loop to read its CONNACK,
        # which is what flips ``_connected`` back to True.
        rc = self._client.loop(timeout=timeout_s)
        if rc != self._mqtt.MQTT_ERR_SUCCESS and not self._connected:
            time.sleep(min(timeout_s, 0.1))

    # -- paho callbacks -----------------------------------------------------

    def _paho_connect(self, client, userdata, flags, reason_code, properties):  # noqa: ANN001
        del client, userdata, properties
        if getattr(reason_code, "is_failure", False):
            if self._on_disconnect is not None:
                self._on_disconnect(f"connect_refused:{reason_code}")
            return
        self._connected = True
        self._lost = False
        session_present = bool(getattr(flags, "session_present", False))
        # The process's on_connect handler owns (re)subscription.
        if self._on_connect is not None:
            self._on_connect(session_present)

    def _paho_message(self, client, userdata, message):  # noqa: ANN001
        del client, userdata
        delivery = Delivery(
            topic=message.topic,
            payload=bytes(message.payload),
            qos=int(message.qos),
            retain=bool(message.retain),
            mid=int(message.mid),
            duplicate=bool(getattr(message, "dup", False)),
        )
        if self._on_message is not None:
            self._on_message(delivery)

    def _paho_disconnect(self, client, userdata, flags, reason_code, properties):  # noqa: ANN001
        del client, userdata, flags, properties
        self._connected = False
        self._lost = True
        if self._on_disconnect is not None:
            self._on_disconnect(f"disconnected:{reason_code}")


__all__ = ["Delivery", "InMemoryBroker", "InMemoryClient", "PahoClient", "PublishHandle", "TransportClient"]
