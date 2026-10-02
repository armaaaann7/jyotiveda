"""CloudEvents 1.0 envelope + the platform's topic catalogue.

Every inter-service message is a CloudEvent whose `data` is validated against a pydantic schema,
so producers and consumers (including the frontend over WebSocket) share one contract.
JSON Schemas for all topics are exported with `jyotiveda schemas export`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Topic(StrEnum):
    METER_READING = "meter.reading"
    TRANSFORMER_STATE = "transformer.state"
    SOLAR_UPDATE = "solar.update"
    BATTERY_UPDATE = "battery.update"
    WEATHER_UPDATE = "weather.update"
    FORECAST_CREATED = "forecast.created"
    RISK_UPDATED = "risk.updated"
    BUDGET_CREATED = "reliability.budget.created"
    DISPATCH_PROPOSED = "dispatch.proposed"
    DISPATCH_VALIDATED = "dispatch.validated"
    DISPATCH_APPROVED = "dispatch.approved"
    DISPATCH_SENT = "dispatch.sent"
    DISPATCH_ACKNOWLEDGED = "dispatch.acknowledged"
    DISPATCH_EXECUTED = "dispatch.executed"
    DISPATCH_VERIFIED = "dispatch.verified"
    DISPATCH_REJECTED = "dispatch.rejected"
    FLEXIBILITY_OFFERED = "flexibility.offered"
    FLEXIBILITY_ACCEPTED = "flexibility.accepted"
    FAIRNESS_UPDATED = "fairness.updated"
    ALARM_RAISED = "alarm.raised"
    SIMULATION_COMPLETED = "simulation.completed"
    EDGE_HEARTBEAT = "edge.heartbeat"


class CloudEvent(BaseModel):
    specversion: str = "1.0"
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    source: str
    type: Topic
    subject: str | None = Field(None, description="Usually the transformer id — also the Kafka partition key")
    time: datetime = Field(default_factory=lambda: datetime.now(UTC))
    datacontenttype: str = "application/json"
    traceparent: str | None = None
    data: dict[str, Any]

    @classmethod
    def of(
        cls, topic: Topic, source: str, data: BaseModel | dict[str, Any], subject: str | None = None
    ) -> CloudEvent:
        payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
        return cls(type=topic, source=source, subject=subject, data=payload)
