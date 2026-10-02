"""Train the constrained PPO residual policy in the digital twin and export it to ONNX.

    uv run --extra ml python ml/train_ppo.py --steps 500000 --out models/ppo_residual.onnx

PPO-Lagrangian: the Lagrange multiplier on the safety cost (protected-load shortfall + SoC margin)
is updated by dual ascent after each rollout, so the policy learns to respect constraints *before*
the shield has to intervene. Runs are tracked in MLflow; promotion requires the policy to beat
MPC-only on held-out scenarios with zero increase in shield interventions.
"""

from __future__ import annotations

import argparse

import numpy as np


def main() -> None:  # pragma: no cover - requires the `ml` extra
    import gymnasium as gym
    import mlflow
    import torch
    from stable_baselines3 import PPO
    from stable_baselines3.common.callbacks import BaseCallback
    from stable_baselines3.common.vec_env import SubprocVecEnv

    from jyotiveda.rl.env import DTFlexEnv

    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=500_000)
    ap.add_argument("--envs", type=int, default=8)
    ap.add_argument("--cost-limit", type=float, default=0.05, help="kWh protected shortfall per episode")
    ap.add_argument("--out", default="models/ppo_residual.onnx")
    args = ap.parse_args()

    class Lagrangian(gym.Wrapper):
        lam = 1.0

        def step(self, action):
            obs, r, term, trunc, info = self.env.step(action)
            return obs, r - Lagrangian.lam * info["cost"], term, trunc, info

    class DualAscent(BaseCallback):
        def __init__(self) -> None:
            super().__init__()
            self.costs: list[float] = []

        def _on_step(self) -> bool:
            for info in self.locals["infos"]:
                self.costs.append(info.get("cost", 0.0))
            return True

        def _on_rollout_end(self) -> None:
            ep_cost = float(np.mean(self.costs)) * 96
            Lagrangian.lam = max(0.0, Lagrangian.lam + 0.05 * (ep_cost - args.cost_limit))
            mlflow.log_metrics(
                {"lagrange_lambda": Lagrangian.lam, "episode_cost": ep_cost}, step=self.num_timesteps
            )
            self.costs.clear()

    env = SubprocVecEnv([lambda i=i: Lagrangian(DTFlexEnv(randomize=True)) for i in range(args.envs)])
    mlflow.set_experiment("jyotiveda-rl")
    with mlflow.start_run(run_name="ppo-lagrangian-residual"):
        model = PPO(
            "MlpPolicy",
            env,
            n_steps=96 * 4,
            batch_size=256,
            gae_lambda=0.95,
            gamma=0.995,
            learning_rate=3e-4,
            ent_coef=0.005,
            verbose=1,
            policy_kwargs={"net_arch": [128, 128]},
        )
        model.learn(total_timesteps=args.steps, callback=DualAscent())

        class Actor(torch.nn.Module):
            def __init__(self, policy) -> None:
                super().__init__()
                self.policy = policy

            def forward(self, obs):
                return self.policy.get_distribution(obs).distribution.mean.clamp(-1, 1)

        dummy = torch.zeros(1, 13)
        torch.onnx.export(
            Actor(model.policy),
            dummy,
            args.out,
            input_names=["obs"],
            output_names=["action"],
            dynamic_axes={"obs": {0: "batch"}},
            opset_version=17,
        )
        mlflow.log_artifact(args.out)
        print(f"exported {args.out}")


if __name__ == "__main__":
    main()
