"""
Replay the Kalman probability engine over a real recorded trading day.

Reads the raw observation journals recorded by the production system (private
weather station readings, METAR observations, forecast revisions), feeds them
to the engine at replay-grid times, admitting only records received by then,
and tracks how the posterior and daily-maximum distribution P_now evolve.
The engine refresh processes PWS before METAR within each batch; sorting the
arrival stream does not make this a globally event-time-ordered filter.

Usage:
    python replay.py                       # istanbul 2026-06-11, 5-minute steps
    python replay.py --step-min 2          # finer replay grid
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt

from kalman_engine import KalmanWeatherModel

# Market definition + per-market calibrated parameters (from the 28-market
# forecast-aware Brier fit described in the "Weather Markets" study).
MARKET = {
    "id": "istanbul",
    "icao": "LTFM",
    "tz_hours": 3,
    "publish_minutes": [20, 50],
    "date": date(2026, 6, 11),
    "beta_h": 0.5,            # forecast-pull rate per hour
    "sigma_proc_h": 0.8,      # process noise, deg C per sqrt(hour)
    "sigma_pws": 1.2,         # PWS observation noise
    "fallback_bias": 0.3,     # METAR - PWS fallback before the rolling window fills
}


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/istanbul_2026-06-11")
    ap.add_argument("--step-min", type=float, default=5.0)
    ap.add_argument("--out", default="output/replay.png")
    args = ap.parse_args()

    data = Path(args.data)
    pws_rows = load_jsonl(data / "pws_raw.jsonl")
    metar_rows = load_jsonl(data / "metar_raw.jsonl")
    fc_rows = load_jsonl(data / "forecast_raw.jsonl")

    # Build the arrival-ordered event stream. `ts` is the moment the live system
    # received the record. Admission is arrival-causal at the chosen grid; source
    # batching inside refresh is retained from the historical model.
    events: list[tuple[datetime, str, dict]] = []
    events += [(parse_ts(r["ts"]), "pws", r) for r in pws_rows]
    events += [(parse_ts(r["ts"]), "metar", r) for r in metar_rows]
    events += [(parse_ts(r["ts"]), "forecast", r) for r in fc_rows]
    events.sort(key=lambda e: e[0])
    t0, t1 = events[0][0], events[-1][0]
    print(f"{len(events)} events  ({len(pws_rows)} pws, {len(metar_rows)} metar, "
          f"{len(fc_rows)} forecast)  {t0:%H:%M}Z -> {t1:%H:%M}Z")

    model = KalmanWeatherModel(
        MARKET["id"],
        tz_hours=MARKET["tz_hours"],
        target_local_date=MARKET["date"],
        publish_minutes=MARKET["publish_minutes"],
        sigma_process_per_hour=MARKET["sigma_proc_h"],
        mean_reversion_per_hour=MARKET["beta_h"],
        sigma_pws=MARKET["sigma_pws"],
        fallback_pws_bias=MARKET["fallback_bias"],
    )

    # replay loop
    hist: list[dict] = []
    residuals: list[tuple[datetime, float]] = []
    i = 0
    now = t0
    last_residual_hour = None
    while now <= t1 + timedelta(minutes=args.step_min):
        while i < len(events) and events[i][0] <= now:
            _, kind, r = events[i]
            if kind == "pws":
                model.add_pws(parse_ts(r["obs_time_utc"]), float(r["temp_c"]), r.get("solar_rad"))
            elif kind == "metar":
                model.add_metar(parse_ts(r["valid_utc"]), int(r["temp_c"]))
            else:
                model.set_forecast(r["curve_times"], r["curve_temps"], r.get("peak_market_unit"))
            i += 1
        model.set_now(now)
        snap = model.refresh()
        if snap.T_mean or snap.confirmed_max is not None:
            hist.append({
                "t": now, "mean": snap.T_mean, "sigma": snap.T_sigma,
                "P_now": snap.P_now, "M": snap.confirmed_max,
                "fc": model._forecast_at(now),
            })
            if now.hour != last_residual_hour and snap.Q_next:
                residuals.append((now, model.consistency_residual()))
                last_residual_hour = now.hour
        now += timedelta(minutes=args.step_min)

    # ---- console summary ----
    final = hist[-1]
    print(f"\nfinal posterior: T = {final['mean']:.2f} +/- {final['sigma']:.2f} C, "
          f"confirmed max = {final['M']}")
    print("final P_now:", {k: round(v, 3) for k, v in sorted(final["P_now"].items())})
    print("consistency residual  max_k |sum_m Q(m) D(k|m) - P_now(k)|  (hourly):")
    for t, r in residuals:
        print(f"  {t:%H:%M}Z  {r:.4f}")

    # ---- plot ----
    tz = timedelta(hours=MARKET["tz_hours"])
    loc = lambda t: t + tz  # naive local time for display
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11, 7.5), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 2]})
    fig.suptitle(f"Kalman engine replay — Istanbul (LTFM) daily-max market, "
                 f"{MARKET['date']}  (real recorded data)", fontsize=12)

    ts = [loc(h["t"]) for h in hist]
    mean = [h["mean"] for h in hist]
    lo = [h["mean"] - h["sigma"] for h in hist]
    hi = [h["mean"] + h["sigma"] for h in hist]
    ax1.fill_between(ts, lo, hi, alpha=0.20, color="tab:blue", label="posterior ±1σ")
    ax1.plot(ts, mean, color="tab:blue", lw=1.6, label="posterior mean T(t)")
    ax1.plot(ts, [h["fc"] for h in hist], color="tab:green", ls="--", lw=1.0,
             label="forecast (latest revision)")
    ax1.scatter([loc(parse_ts(r["obs_time_utc"])) for r in pws_rows],
                [r["temp_c"] for r in pws_rows], s=4, color="0.65", label="PWS raw (5-min)")
    seen = {}
    for r in metar_rows:
        seen[r["valid_utc"]] = r["temp_c"]
    ax1.scatter([loc(parse_ts(k)) for k in seen], list(seen.values()),
                marker="s", s=45, color="tab:red", zorder=5, label="METAR (official)")
    ax1.step(ts, [h["M"] for h in hist], where="post", color="tab:orange", lw=1.2,
             ls=":", label="confirmed max")
    ax1.set_ylabel("°C")
    ax1.legend(loc="upper right", fontsize=8, ncols=2)
    ax1.grid(alpha=0.25)

    buckets = sorted({k for h in hist for k in h["P_now"]})
    for k in buckets:
        series = [h["P_now"].get(k, 0.0) for h in hist]
        if max(series) < 0.05:
            continue
        ax2.plot(ts, series, lw=1.6, label=f"P(daily max = {k}°C)")
    ax2.set_ylim(-0.03, 1.05)
    ax2.set_ylabel("probability")
    ax2.set_xlabel(f"local time (UTC+{MARKET['tz_hours']})")
    ax2.legend(loc="center right", fontsize=8)
    ax2.grid(alpha=0.25)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
