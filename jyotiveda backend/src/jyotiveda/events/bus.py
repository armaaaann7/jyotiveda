"""Event bus abstraction: in-memory for dev/test/edge, Kafka/Redpanda for production.

Kafka semantics used in prod:
* topic per event type, partitioned by `subject` (transformer id) => per-DT ordering
* idempotent producer (enable_idempotence) + acks=all
* consumer groups per service role; at-least-once delivery, consumers dedupe on CloudEvent.id
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Protocol

import orjson
import structlog

from jyotiveda.events.envelope import CloudEvent, Topic

log = structlog.get_logger(__name__)
Handler = Callable[[CloudEvent], Awaitable[None]]


class EventBus(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    async def publish(self, event: CloudEvent) -> None: ...
    def subscribe(self, topic: Topic | str, handler: Handler, group: str = "default") -> None: ...
    def recent(self, limit: int = 100) -> list[CloudEvent]: ...


class InMemoryBus:
    """Async fan-out bus with a replay buffer. Wildcard subscription with topic '*'."""

    def __init__(self, replay: int = 2000) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._log: deque[CloudEvent] = deque(maxlen=replay)
        self._seen: set[str] = set()

    async def start(self) -> None:  # pragma: no cover - nothing to connect
        return None

    async def stop(self) -> None:  # pragma: no cover
        return None

    async def publish(self, event: CloudEvent) -> None:
        if event.id in self._seen:  # idempotent publish
            return
        self._seen.add(event.id)
        self._log.append(event)
        handlers = [*self._subs.get(str(event.type), []), *self._subs.get("*", [])]
        for h in handlers:
            try:
                await h(event)
            except Exception:  # a failing consumer must never break the producer
                log.exception("event_handler_failed", topic=str(event.type), event_id=event.id)

    def subscribe(self, topic: Topic | str, handler: Handler, group: str = "default") -> None:
        self._subs[str(topic)].append(handler)

    def recent(self, limit: int = 100) -> list[CloudEvent]:
        return list(self._log)[-limit:]


class KafkaBus:  # pragma: no cover - exercised in docker-compose integration environment
    def __init__(self, bootstrap: str, client_id: str) -> None:
        self.bootstrap, self.client_id = bootstrap, client_id
        self._producer = None
        self._subs: list[tuple[str, Handler, str]] = []
        self._tasks: list[asyncio.Task[None]] = []
        self._local = InMemoryBus()

    async def start(self) -> None:
        from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

        self._producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap,
            client_id=self.client_id,
            enable_idempotence=True,
            acks="all",
            compression_type="zstd",
            value_serializer=lambda e: orjson.dumps(e.model_dump(mode="json")),
            key_serializer=lambda k: k.encode() if k else None,
        )
        await self._producer.start()
        for topic, handler, group in self._subs:
            consumer = AIOKafkaConsumer(
                topic,
                bootstrap_servers=self.bootstrap,
                group_id=f"jyotiveda.{group}",
                enable_auto_commit=False,
                auto_offset_reset="latest",
            )
            await consumer.start()
            self._tasks.append(asyncio.create_task(self._consume(consumer, handler)))

    async def _consume(self, consumer, handler: Handler) -> None:
        try:
            async for msg in consumer:
                event = CloudEvent.model_validate(orjson.loads(msg.value))
                await handler(event)
                await consumer.commit()
        finally:
            await consumer.stop()

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        if self._producer:
            await self._producer.stop()

    async def publish(self, event: CloudEvent) -> None:
        assert self._producer is not None, "KafkaBus.start() not called"
        await self._producer.send_and_wait(str(event.type), event, key=event.subject)
        await self._local.publish(event)  # keep a local replay buffer for WebSocket catch-up

    def subscribe(self, topic: Topic | str, handler: Handler, group: str = "default") -> None:
        if str(topic) == "*":
            self._local.subscribe("*", handler)
        else:
            self._subs.append((str(topic), handler, group))

    def recent(self, limit: int = 100) -> list[CloudEvent]:
        return self._local.recent(limit)


def build_bus(kind: str, bootstrap: str = "", client_id: str = "jyotiveda") -> EventBus:
    if kind == "kafka":
        return KafkaBus(bootstrap, client_id)
    return InMemoryBus()
