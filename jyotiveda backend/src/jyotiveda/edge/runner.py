"""Edge runtime process (runs on the gateway at each DT, under k3s or systemd).

    jyotiveda edge --transformer DT-104 --broker emqx.local --pubkey /etc/jyotiveda/dispatch.pub

Loops:
  * command loop  — MQTT 5 QoS-1 subscribe  jyotiveda/v1/dt/{id}/cmd  → verify → local shield → apply → ack
  * autonomy loop — tick every 2 s (cloud-loss detection, cached plan, lifeline controller)
  * uplink loop   — drain the SQLite outbox to  jyotiveda/v1/dt/{id}/telemetry  when the broker is reachable
"""

from __future__ import annotations

import asyncio

import orjson
import structlog

from jyotiveda.edge.gateway import EdgeGateway, Outbox
from jyotiveda.edge.protocols import VirtualMeterBank, VirtualPCS
from jyotiveda.safety.shield import SafetyLimits, SafetyShield
from jyotiveda.security.signing import CommandVerifier

log = structlog.get_logger(__name__)


async def run_edge(
    transformer_id: str, broker: str, port: int, pubkey_pem: bytes, outbox_path: str
) -> None:  # pragma: no cover
    import aiomqtt

    gw = EdgeGateway(
        transformer_id=transformer_id,
        verifier=CommandVerifier(pubkey_pem),
        shield=SafetyShield(SafetyLimits()),
        pcs=VirtualPCS(),  # swap for SunSpec/Modbus PCS driver on real hardware
        meters=VirtualMeterBank(),  # swap for DLMS/COSEM HES driver
        lifeline_kw_by_household={},
        outbox=Outbox(outbox_path),
    )
    base = f"jyotiveda/v1/dt/{transformer_id}"

    async def autonomy() -> None:
        while True:
            res = await gw.tick()
            if res.get("mode") != "CLOUD_COORDINATED":
                log.warning("edge_autonomous", **{k: v for k, v in res.items() if k != "violations"})
            await asyncio.sleep(2)

    _autonomy = asyncio.create_task(autonomy())  # noqa: F841 - lives for the process lifetime
    while True:
        try:
            async with aiomqtt.Client(broker, port, identifier=f"edge-{transformer_id}", keepalive=30) as c:
                await c.subscribe(f"{base}/cmd", qos=1)
                await c.subscribe(f"{base}/heartbeat", qos=0)
                gw.heartbeat()

                async def uplink() -> None:
                    while True:
                        batch = gw.outbox.drain(200)
                        for _, topic, body in batch:
                            await c.publish(f"{base}/{topic}", orjson.dumps(body), qos=1)
                        gw.outbox.ack([i for i, _, _ in batch])
                        await asyncio.sleep(5)

                up = asyncio.create_task(uplink())
                async for msg in c.messages:
                    if msg.topic.matches(f"{base}/heartbeat"):
                        gw.heartbeat()
                        continue
                    res = await gw.handle_command(orjson.loads(msg.payload))
                    await c.publish(f"{base}/ack", orjson.dumps(res), qos=1)
                up.cancel()
        except Exception as exc:  # broker down: keep running autonomously, retry
            log.warning("edge_uplink_down", error=str(exc))
            await asyncio.sleep(5)
