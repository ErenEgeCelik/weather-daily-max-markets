"""Synthetic observation -> posterior -> P/Q/D example; no network or orders."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from kalman_engine import KalmanWeatherModel  # noqa: E402


def at(hhmm: str) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2026, 6, 15, hour, minute, tzinfo=timezone.utc)


def build_synthetic_model() -> tuple[KalmanWeatherModel, list[dict]]:
    """Hand-authored inputs, not a historical observation or performance sample."""
    model = KalmanWeatherModel(
        "synthetic-temperature", tz_hours=0, target_local_date=date(2026, 6, 15),
        sigma_process_per_hour=0.8, mean_reversion_per_hour=0.5,
        sigma_pws=1.2, fallback_pws_bias=0.3,
    )
    model.set_forecast(["12:00", "15:00", "18:00", "23:00"], [21, 23, 21, 18], 23)
    # Arrival and observation clocks are separate. Injection occurs only on arrival.
    events = [
        ("12:10", "pws", "12:09", 21.1, 300),
        ("12:19", "pws", "12:19", 21.7, 500),
        ("12:23", "metar", "12:20", 21, None),
        ("12:35", "pws", "12:34", 22.1, 700),
        ("12:49", "pws", "12:49", 22.4, 600),
        ("12:53", "metar", "12:50", 22, None),
        ("13:00", "pws", "12:59", 22.2, 400),
    ]
    history = []
    for arrival, kind, observed, value, solar in events:
        if kind == "pws":
            model.add_pws(at(observed), value, solar)
        else:
            model.add_metar(at(observed), int(value))
        model.set_now(at(arrival))
        snap = model.refresh()
        history.append({"arrived_at": at(arrival).isoformat(),
                        "observed_at": at(observed).isoformat(), "kind": kind,
                        "posterior_mean_c": snap.T_mean, "posterior_sigma_c": snap.T_sigma,
                        "confirmed_max_c": snap.confirmed_max,
                        "pws_correction_c": model._pws_bias})
    return model, history


def probability_state(model: KalmanWeatherModel) -> dict:
    """Values a separate decision layer can combine with prices and positions."""
    snap = model.snapshot()
    conditional = {m: model.distribution_given(m) for m in snap.Q_next}
    mixture = {}
    for m, q in snap.Q_next.items():
        for k, p in conditional[m].items():
            mixture[k] = mixture.get(k, 0.0) + q * p
    keys = set(mixture) | set(snap.P_now)
    errors = [abs(mixture.get(k, 0.0) - snap.P_now.get(k, 0.0)) for k in keys]
    return {"when": snap.when.isoformat(), "posterior_mean_c": snap.T_mean,
            "posterior_sigma_c": snap.T_sigma, "confirmed_max_c": snap.confirmed_max,
            "P_now": snap.P_now, "Q_next": snap.Q_next, "D_after": conditional,
            "mixture_QD": mixture, "residual_max_bucket": max(errors, default=0.0),
            "residual_l1": sum(errors)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='emit every input, probability and conditional branch')
    args = parser.parse_args()
    model, history = build_synthetic_model()
    state = probability_state(model)
    if args.json:
        print(json.dumps({"fixture_status": "hand-authored synthetic illustration",
                          "units": "Celsius; process time in hours; UTC offset 0",
                          "monte_carlo": {"paths_per_call": 10000, "seed_per_call": 0},
                          "history": history, "decision_layer_input": state},
                         indent=2, sort_keys=True))
        return
    print('Synthetic weather observations -> posterior -> daily-maximum probabilities')
    print('Celsius; UTC; 10,000 paths and seed 0 per call. No performance estimate.\n')
    print('Arrival  Source   Mean C   Sigma C  Confirmed maximum')
    for row in history:
        print(f"{row['arrived_at'][11:16]:7}  {row['kind']:6}  {row['posterior_mean_c']:7.3f}"
              f"  {row['posterior_sigma_c']:7.3f}  {str(row['confirmed_max_c']):>17}")
    compact = lambda values: '  '.join(f'{k}:{p:.4f}' for k, p in sorted(values.items()))
    print('\nP(daily maximum):', compact(state['P_now']))
    print('Q(next METAR):   ', compact(state['Q_next']))
    print('\nConditional daily maximum after each possible METAR:')
    for m, dist in sorted(state['D_after'].items()):
        print(f'  METAR {m}: {compact(dist)}')
    print(f"\nMixture residual: max bucket={state['residual_max_bucket']:.6f}; L1={state['residual_l1']:.6f}")
    print('Construction and sampling diagnostic; the consistency identity is not assumed.')


if __name__ == "__main__":
    main()
