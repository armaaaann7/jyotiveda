"""Persistence: SQLAlchemy 2.0 async. SQLite for dev/CI, PostgreSQL + TimescaleDB + PostGIS in prod
(see migrations/*.sql for hypertables, continuous aggregates, retention and spatial indexes)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, DateTime, Float, Index, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


class AuditRow(Base):
    __tablename__ = "audit_log"
    seq: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    ts: Mapped[str] = mapped_column(String(40))
    actor: Mapped[str] = mapped_column(String(128))
    actor_role: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)


class DispatchRow(Base):
    __tablename__ = "dispatch"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    transformer_id: Mapped[str] = mapped_column(String(32), index=True)
    state: Mapped[str] = mapped_column(String(24), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    body: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class SimulationRow(Base):
    __tablename__ = "simulation"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    transformer_id: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    scenario: Mapped[dict] = mapped_column(JSON)
    summary: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)


class TelemetryRow(Base):
    """TimescaleDB hypertable in prod (partitioned by ts, segmented by transformer_id)."""

    __tablename__ = "telemetry"
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    transformer_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    metric: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    __table_args__ = (Index("ix_telemetry_dt_metric_ts", "transformer_id", "metric", "ts"),)


class FlexOfferRow(Base):
    __tablename__ = "flex_offer"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    household_id: Mapped[str] = mapped_column(String(32), index=True)
    transformer_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="OPEN")
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Database:
    def __init__(self, url: str) -> None:
        kw = {"pool_pre_ping": True} if not url.startswith("sqlite") else {}
        self.engine: AsyncEngine = create_async_engine(url, **kw)
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def init(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    async def add_audit(self, rec) -> None:
        async with self.session() as s, s.begin():
            s.add(AuditRow(**rec.to_dict()))

    async def upsert_dispatch(self, rec) -> None:
        async with self.session() as s, s.begin():
            row = await s.get(DispatchRow, rec.id)
            body = rec.to_dict()
            if row is None:
                s.add(
                    DispatchRow(
                        id=rec.id,
                        transformer_id=rec.transformer_id,
                        state=rec.state.value,
                        idempotency_key=rec.idempotency_key,
                        body=body,
                    )
                )
            else:
                row.state, row.body = rec.state.value, body

    async def save_simulation(
        self, sim_id: str, transformer_id: str, scenario: dict, summary: dict, result: dict
    ) -> None:
        async with self.session() as s, s.begin():
            s.add(
                SimulationRow(
                    id=sim_id,
                    transformer_id=transformer_id,
                    scenario=scenario,
                    summary=summary,
                    result=result,
                )
            )

    async def get_simulation(self, sim_id: str) -> dict | None:
        async with self.session() as s:
            row = await s.get(SimulationRow, sim_id)
            return None if row is None else row.result

    async def list_simulations(self, limit: int = 20) -> list[dict]:
        async with self.session() as s:
            rows = (
                await s.execute(select(SimulationRow).order_by(SimulationRow.created_at.desc()).limit(limit))
            ).scalars()
            return [
                {
                    "id": r.id,
                    "transformer_id": r.transformer_id,
                    "created_at": r.created_at.isoformat(),
                    "summary": r.summary,
                }
                for r in rows
            ]

    async def audit_rows(self, entity_id: str | None = None, limit: int = 200) -> list[dict]:
        async with self.session() as s:
            q = select(AuditRow).order_by(AuditRow.seq.desc()).limit(limit)
            if entity_id:
                q = q.where(AuditRow.entity_id == entity_id)
            return [
                {c.name: getattr(r, c.name) for c in AuditRow.__table__.columns}
                for r in (await s.execute(q)).scalars()
            ]

    async def write_telemetry(self, rows: list[tuple[datetime, str, str, float]]) -> None:
        async with self.session() as s, s.begin():
            for ts, dt, metric, val in rows:
                await s.merge(TelemetryRow(ts=ts, transformer_id=dt, metric=metric, value=val))
