"""Thin wrapper — the backtest ships inside the package so the Kubernetes CronJob can run it:
python -m jyotiveda.ml_backtest --days 30
"""

from jyotiveda.ml_backtest import main

if __name__ == "__main__":
    main()
