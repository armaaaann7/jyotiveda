"""Jyoti Copilot — a tool-using LLM agent for DISCOM engineers, Urja Sakhis and residents.

* Model: Claude (default `claude-opus-5-5`; `claude-haiku-4-5` for fast narration), via the Anthropic
  Messages API with tool use. The agent reasons over *live platform tools*, not its own memory.
* Read tools: fleet overview, transformer status, grid risk, fairness ledger, what-if simulation.
* One guarded write tool: `propose_dispatch_cycle` — it only creates a PROPOSED dispatch that goes
  through the safety shield and, above the auto-approval envelope, waits for a human.
* Multilingual: answers in the user's language (Hindi, Marathi, Tamil, …); voice I/O is handled by the
  notification service (Sarvam / Bhashini STT-TTS) in front of this agent.
* Without an API key it degrades to a deterministic intent router, so demos never break.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from jyotiveda.security.rbac import Principal
from jyotiveda.twin.scenario import ScenarioSpec, SupplyWindow

if TYPE_CHECKING:  # pragma: no cover
    from jyotiveda.runtime.platform import Platform

log = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You are Jyoti, the operations copilot of Jyotiveda — a reliability operating system that keeps
electricity dependable in low-income Indian neighbourhoods during renewable-energy dips.
Users: DISCOM engineers (JE/AE), Urja Sakhi community operators, and residents.

Rules:
- Always ground answers in tool results; quote concrete numbers (kW, kWh, %, times in IST, ₹).
- Reply in the user's language and script (Hindi, Marathi, Tamil, English…). Keep it short and practical.
- You never control devices. You may propose a dispatch cycle; it is validated by a deterministic safety
  shield and may need a human approval. Say so when you propose one.
- Lifeline and life-critical loads (T0/T1) are never curtailed — explain this when relevant.
- For residents, avoid jargon (no MPC/GNN/PPO); talk about "your essential power" and rewards."""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_fleet_overview",
        "description": "Rank all transformers by stress score with risk level, gap forecast and battery state.",
        "input_schema": {"type": "object", "properties": {"top": {"type": "integer", "default": 5}}},
    },
    {
        "name": "get_transformer_status",
        "description": "Live state of one transformer (demand, solar, supply cap, battery, edge mode).",
        "input_schema": {
            "type": "object",
            "properties": {"transformer_id": {"type": "string"}},
            "required": ["transformer_id"],
        },
    },
    {
        "name": "get_grid_risk",
        "description": "Grid-intelligence risk (overload, voltage, critical nodes) for the next 4 hours.",
        "input_schema": {
            "type": "object",
            "properties": {"transformer_id": {"type": "string"}},
            "required": ["transformer_id"],
        },
    },
    {
        "name": "get_fairness",
        "description": "Fairness Debt ledger: which households carried the most flexibility burden.",
        "input_schema": {
            "type": "object",
            "properties": {"transformer_id": {"type": "string"}, "top": {"type": "integer", "default": 5}},
            "required": ["transformer_id"],
        },
    },
    {
        "name": "run_what_if",
        "description": "Digital-twin what-if: solar cut and evening supply cap; returns baseline vs Jyotiveda KPIs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "transformer_id": {"type": "string"},
                "solar_reduction": {"type": "number", "minimum": 0, "maximum": 1},
                "evening_supply_cap_kw": {"type": "number"},
                "battery_kwh": {"type": "number"},
            },
            "required": ["transformer_id"],
        },
    },
    {
        "name": "propose_dispatch_cycle",
        "description": "Run the full control cycle and PROPOSE a dispatch (safety-shielded; may need approval).",
        "input_schema": {
            "type": "object",
            "properties": {"transformer_id": {"type": "string"}},
            "required": ["transformer_id"],
        },
    },
]


class Copilot:
    def __init__(self, platform: Platform) -> None:
        self.p = platform
        s = platform.settings
        self.model = s.llm_model
        self.client = None
        if s.anthropic_api_key:
            from anthropic import AsyncAnthropic

            self.client = AsyncAnthropic(api_key=s.anthropic_api_key.get_secret_value())

    # ------------------------------------------------------------------ tools ----------
    async def call_tool(self, name: str, args: dict, principal: Principal) -> dict:
        p = self.p
        dt = args.get("transformer_id")
        if dt is not None and dt not in p.fleet:
            return {"error": f"unknown transformer {dt}", "known": list(p.fleet)}
        if name == "get_fleet_overview":
            rows = p.fleet_overview()[: int(args.get("top", 5))]
            return {
                "transformers": [
                    {
                        k: r[k]
                        for k in ("transformer_id", "ward", "stress_score", "gap_p90_kwh_24h", "loading_pct")
                    }
                    | {"risk": r["risk"]["level"], "soc": r["battery"]["soc"]}
                    for r in rows
                ]
            }
        if name == "get_transformer_status":
            return p.fleet[dt].live_state()
        if name == "get_grid_risk":
            return p.risk(p.fleet[dt])
        if name == "get_fairness":
            snap = sorted(p.fleet[dt].ledger.snapshot(), key=lambda r: -r["debt"])
            return {"top": snap[: int(args.get("top", 5))]}
        if name == "run_what_if":
            rt = p.fleet[dt]
            spec = rt.spec.model_copy(
                update={
                    "solar_reduction": float(args.get("solar_reduction", rt.spec.solar_reduction)),
                    "battery_kwh": float(args.get("battery_kwh", rt.spec.battery_kwh)),
                    "supply_windows": [
                        SupplyWindow(
                            start_hour=17.5,
                            end_hour=22.5,
                            cap_kw=float(args.get("evening_supply_cap_kw", rt.spec.supply_windows[0].cap_kw)),
                        )
                    ],
                    "run_power_flow": False,
                }
            )
            res = await p.simulate(ScenarioSpec.model_validate(spec.model_dump()))
            return {
                "simulation_id": res["id"],
                "improvement": res["improvement"],
                "baseline_critical_outage_home_hours": res["kpis"]["baseline"]["critical_outage_home_hours"],
                "jyotiveda_critical_outage_home_hours": res["kpis"]["jyotiveda"][
                    "critical_outage_home_hours"
                ],
            }
        if name == "propose_dispatch_cycle":
            out = await p.run_cycle(dt, principal)
            d = out["dispatch"]
            return {
                "dispatch_id": d["id"],
                "state": d["state"],
                "battery_kw": (d["shield"] or {}).get("action", {}).get("battery_kw"),
                "shield_verdict": (d["shield"] or {}).get("verdict"),
                "why": out["dispatch"]["reason"],
            }
        return {"error": f"unknown tool {name}"}

    # ------------------------------------------------------------------ chat -----------
    async def chat(
        self,
        message: str,
        principal: Principal,
        transformer_id: str | None = None,
        history: list[dict] | None = None,
    ) -> dict:
        if self.client is None:
            return await self._offline(message, principal, transformer_id)
        msgs: list[dict] = [
            *(history or []),
            {
                "role": "user",
                "content": message + (f"\n(context: transformer {transformer_id})" if transformer_id else ""),
            },
        ]
        trace = []
        for _ in range(6):
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=self.p.settings.llm_max_tokens,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=msgs,
            )
            msgs.append({"role": "assistant", "content": [b.model_dump() for b in resp.content]})
            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if b.type == "text")
                return {"answer": text, "tool_calls": trace, "model": self.model, "mode": "llm"}
            results = []
            for block in resp.content:
                if block.type == "tool_use":
                    out = await self.call_tool(block.name, dict(block.input), principal)
                    trace.append({"tool": block.name, "input": block.input})
                    results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(out, default=str)[:12000],
                        }
                    )
            msgs.append({"role": "user", "content": results})
        return {
            "answer": "I could not complete the request within the tool budget.",
            "tool_calls": trace,
            "model": self.model,
            "mode": "llm",
        }

    async def _offline(self, message: str, principal: Principal, transformer_id: str | None) -> dict:
        m = message.lower()
        dt = transformer_id or next((k for k in self.p.fleet if k.lower() in m), None)
        if (
            any(
                w in m
                for w in ("overload", "risk", "stress", "which transformer", "kaunse", "tomorrow", "kal")
            )
            and not dt
        ):
            out = await self.call_tool("get_fleet_overview", {"top": 5}, principal)
            lines = [
                f"{i + 1}. {r['transformer_id']} ({r['ward']}) — risk {r['risk']}, stress {r['stress_score']}, "
                f"P90 gap {r['gap_p90_kwh_24h']} kWh, battery {r['soc']:.0%}"
                for i, r in enumerate(out["transformers"])
            ]
            return {
                "answer": "Most stressed transformers in the next 24 h:\n"
                + "\n".join(lines)
                + "\nSay 'propose dispatch for DT-xxx' to create a safety-validated plan.",
                "tool_calls": [{"tool": "get_fleet_overview"}],
                "mode": "offline",
            }
        if dt and any(w in m for w in ("propose", "dispatch", "act", "plan")):
            out = await self.call_tool("propose_dispatch_cycle", {"transformer_id": dt}, principal)
            return {
                "answer": f"Proposed dispatch {out['dispatch_id']} for {dt}: battery {out['battery_kw']} kW, shield "
                f"{out['shield_verdict']}, state {out['state']}. Reason: {out['why']}",
                "tool_calls": [{"tool": "propose_dispatch_cycle"}],
                "mode": "offline",
            }
        if dt and any(w in m for w in ("what if", "cloud", "simulate", "agar")):
            out = await self.call_tool("run_what_if", {"transformer_id": dt}, principal)
            return {
                "answer": f"What-if on {dt}: critical outage home-hours {out['baseline_critical_outage_home_hours']} → "
                f"{out['jyotiveda_critical_outage_home_hours']} with Jyotiveda ({out['improvement']['critical_outage_reduction_pct']}% lower).",
                "tool_calls": [{"tool": "run_what_if"}],
                "mode": "offline",
            }
        dt = dt or next(iter(self.p.fleet))
        st = await self.call_tool("get_transformer_status", {"transformer_id": dt}, principal)
        return {
            "answer": f"{dt} ({st['ward']}) at {st['ts'][11:16]}: demand {st['demand_kw']} kW, solar {st['solar_kw']} kW, "
            f"supply cap {st['grid_cap_kw']} kW, battery {st['battery']['soc']:.0%}. Edge mode {st['edge_mode']}.",
            "tool_calls": [{"tool": "get_transformer_status"}],
            "mode": "offline",
        }
