"""`jyotiveda` CLI: serve, simulate, token, keys, schemas, backtest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jyotiveda")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sv = sub.add_parser("serve", help="run the API (role from JYOTIVEDA_ROLE)")
    sv.add_argument("--host", default="0.0.0.0")  # noqa: S104 - container entrypoint
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--workers", type=int, default=1)
    sm = sub.add_parser("simulate", help="run a counterfactual scenario and print KPIs")
    sm.add_argument("--solar-reduction", type=float, default=0.6)
    sm.add_argument("--cap-kw", type=float, default=70)
    sm.add_argument("--battery-kwh", type=float, default=200)
    sm.add_argument("--json", action="store_true")
    tk = sub.add_parser("token", help="mint a dev JWT")
    tk.add_argument("--role", default="DISCOM_OPERATOR")
    tk.add_argument("--sub", default="demo.operator@discom.in")
    kg = sub.add_parser("keygen", help="generate the Ed25519 command-signing key pair")
    kg.add_argument("--out", default="secrets/dispatch_ed25519.pem")
    sub.add_parser("schemas", help="export OpenAPI + event JSON schemas to ./schemas")
    ed = sub.add_parser("edge", help="run the edge gateway runtime (MQTT)")
    ed.add_argument("--transformer", required=True)
    ed.add_argument("--broker", default="localhost")
    ed.add_argument("--port", type=int, default=1883)
    ed.add_argument("--pubkey", default="/etc/jyotiveda/dispatch_ed25519.pub")
    ed.add_argument("--outbox", default="/var/lib/jyotiveda/outbox.db")
    args = ap.parse_args(argv)

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run(
            "jyotiveda.api.app:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            workers=args.workers,
            proxy_headers=True,
            log_config=None,
        )
    elif args.cmd == "simulate":
        from jyotiveda.twin.scenario import ScenarioSpec, SupplyWindow
        from jyotiveda.twin.simulator import TwinSimulator

        spec = ScenarioSpec(
            solar_reduction=args.solar_reduction,
            battery_kwh=args.battery_kwh,
            supply_windows=[SupplyWindow(start_hour=17.5, end_hour=22.5, cap_kw=args.cap_kw)],
        )
        res = TwinSimulator(spec).run().to_dict(include_series=False)
        if args.json:
            print(json.dumps(res, indent=2, default=str))
        else:
            b, j = res["kpis"]["baseline"], res["kpis"]["jyotiveda"]
            print(f"{'KPI':<42}{'baseline':>12}{'jyotiveda':>12}")
            for k in (
                "critical_outage_home_hours",
                "household_outage_home_hours",
                "lifeline_availability_in_scarcity",
                "energy_not_served_kwh",
                "homes_with_uninterrupted_lifeline",
                "voltage_violation_slots",
                "fairness_gini_served_ratio",
            ):
                print(f"{k:<42}{b[k]:>12}{j[k]:>12}")
            print("improvement:", json.dumps(res["improvement"]))
    elif args.cmd == "edge":
        import asyncio

        from jyotiveda.edge.runner import run_edge

        asyncio.run(
            run_edge(args.transformer, args.broker, args.port, Path(args.pubkey).read_bytes(), args.outbox)
        )
    elif args.cmd == "token":
        from jyotiveda.security.rbac import Role, mint_dev_token

        print(mint_dev_token(args.sub, Role(args.role)))
    elif args.cmd == "keygen":
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        k = Ed25519PrivateKey.generate()
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(
            k.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        out.chmod(0o600)
        out.with_suffix(".pub").write_bytes(
            k.public_key().public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        )
        print(f"wrote {out} and {out.with_suffix('.pub')}")
    elif args.cmd == "schemas":
        from jyotiveda.api.app import create_app
        from jyotiveda.events.envelope import CloudEvent, Topic

        Path("schemas").mkdir(exist_ok=True)
        Path("schemas/openapi.json").write_text(json.dumps(create_app().openapi(), indent=2))
        Path("schemas/cloudevent.schema.json").write_text(
            json.dumps(CloudEvent.model_json_schema(), indent=2)
        )
        Path("schemas/topics.json").write_text(json.dumps([t.value for t in Topic], indent=2))
        print("wrote schemas/openapi.json, schemas/cloudevent.schema.json, schemas/topics.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
