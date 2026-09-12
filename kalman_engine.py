"""
KalmanWeatherModel — Bayesian state estimator for temperature prediction markets.

Adapted for offline replay from the inference research for Polymarket's
daily maximum-temperature markets (see github.com/ErenEgeCelik/prediction-market-research,
"Weather Markets: A Succession of Edges"). The live network pollers are removed;
observations are injected by the replay driver and a replay clock replaces wall time.
Deployment and calibration versions are documented separately in docs/.

Hidden state:
  T(t)   = instantaneous temperature at the resolving station (continuous)
  sigma2 = posterior variance of T(t)

Process model (AR(1) random walk pulled toward the forecast curve):
  T(t+dt) = (1 - beta*dt) * T(t) + beta*dt * forecast(t+dt) + eps,
  eps ~ N(0, sigma_proc^2 * dt)

Observation models (each triggers a scalar Kalman update):
  PWS   : pv     = T(t) + bias_pws + N(0, sigma_pws^2)   (solar-regime adjusted)
  METAR : m      = round(T(t)) + N(0, sigma_metar^2)     (sigma ~= 0.29, uniform round noise)

Outputs (all descending from the same posterior):
  P_now  = P(daily_max = k)          — Monte Carlo over the remaining hours
  Q_next = P(next METAR = m)         — analytic Gaussian round-PMF at the publish minute
  D_after(m) = P(daily_max = k | next METAR = m)  — hypothetical-observation conditioning

The implementation evaluates the intended approximate consistency identity
  sum_m Q(m) * D_after(k|m) ~= P_now(k)
as a diagnostic; Monte Carlo error and construction differences require separate evaluation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------- helpers ----

def gaussian_round_pmf(mu: float, sigma: float, k_range: Tuple[int, int]) -> Dict[int, float]:
    """P(round(X) = k) for X ~ N(mu, sigma^2), integer k.
    P(round(X)=k) = Phi((k+0.5-mu)/sigma) - Phi((k-0.5-mu)/sigma)."""
    out: Dict[int, float] = {}
    for k in range(k_range[0], k_range[1] + 1):
        p = 0.5 * (math.erf((k + 0.5 - mu) / (sigma * math.sqrt(2))) -
                   math.erf((k - 0.5 - mu) / (sigma * math.sqrt(2))))
        if p >= 1e-5:
            out[k] = p
    s = sum(out.values()) or 1.0
    return {k: v / s for k, v in out.items()}


def kalman_update(prior_mean: float, prior_var: float,
                  obs: float, obs_var: float, obs_offset: float = 0.0) -> Tuple[float, float]:
    """Scalar Kalman update for y = T + offset + N(0, obs_var)."""
    if prior_var + obs_var <= 0:
        return prior_mean, prior_var
    K = prior_var / (prior_var + obs_var)
    new_mean = prior_mean + K * ((obs - obs_offset) - prior_mean)
    new_var = (1 - K) * prior_var
    return new_mean, max(1e-6, new_var)


def solar_regime_adjustment(solar_rad: Optional[float], sigma_pws_base: float,
                            b_pws_base: float, *, low: float = 200.0, high: float = 800.0,
                            alpha: float = 1.0, beta: float = 0.5) -> Tuple[float, float]:
    """Regime-aware PWS noise/bias: a small station enclosure in direct sun over an
    artificial surface heats faster than the shielded airport instrument, so under
    strong solar radiation the reading is trusted less and pushed down.

      sunny  = clip((solar - low) / (high - low), 0, 1)
      sigma' = sigma_pws * (1 + alpha * sunny)
      bias'  = b_pws + beta * sunny
    """
    if solar_rad is None or solar_rad <= 0:
        return sigma_pws_base, b_pws_base
    sunny = max(0.0, min(1.0, (solar_rad - low) / max(1.0, high - low)))
    return sigma_pws_base * (1.0 + alpha * sunny), b_pws_base + beta * sunny


def interp_pws(series_sorted: List[Tuple[datetime, float]], when: datetime,
               max_gap_min: float = 12.0) -> Optional[float]:
    """Linear interpolation of the PWS value at `when`; None if no point within max_gap."""
    if not series_sorted:
        return None
    prev = nxt = None
    for t, v in series_sorted:
        if t <= when:
            prev = (t, v)
        elif nxt is None:
            nxt = (t, v)
            break
    if prev and nxt:
        if (when - prev[0]).total_seconds() / 60 > max_gap_min and \
           (nxt[0] - when).total_seconds() / 60 > max_gap_min:
            return None
        span = (nxt[0] - prev[0]).total_seconds()
        if span <= 0:
            return prev[1]
        w = (when - prev[0]).total_seconds() / span
        return prev[1] + w * (nxt[1] - prev[1])
    if prev and (when - prev[0]).total_seconds() / 60 <= max_gap_min:
        return prev[1]
    if nxt and (nxt[0] - when).total_seconds() / 60 <= max_gap_min:
        return nxt[1]
    return None


def rolling_bias(metar_sorted: List[Tuple[datetime, int]], pws_sorted: List[Tuple[datetime, float]],
                 when: datetime, window_min: float = 150.0) -> Optional[float]:
    """Mean of (METAR - PWS_at_that_time) over the METARs in the last `window_min`
    before `when` — the intraday re-calibration of the PWS offset.
    None if fewer than 2 usable pairs."""
    lo = when - timedelta(minutes=window_min)
    biases: List[float] = []
    for pt, ptemp in metar_sorted:
        if not (lo <= pt < when):
            continue
        ppv = interp_pws(pws_sorted, pt)
        if ppv is not None:
            biases.append(ptemp - ppv)
    return (sum(biases) / len(biases)) if len(biases) >= 2 else None


# ---------------------------------------------------------------- snapshot ---

@dataclass
class Snapshot:
    when: datetime
    T_mean: float
    T_sigma: float
    P_now: Dict[int, float]        # P(daily_max = k)
    Q_next: Dict[int, float]       # P(next METAR = m)
    confirmed_max: Optional[int]
    forecast_peak: Optional[float]
    last_update_kind: Optional[str]


# ---------------------------------------------------------------- model ------

class KalmanWeatherModel:
    """Single hidden-state Bayesian filter for one temperature market (offline replay)."""

    def __init__(self, market_id: str, *, tz_hours: int,
                 target_local_date: date,
                 publish_minutes: Optional[List[int]] = None,
                 # calibrated per market (see the 28-market Brier fit in the study)
                 sigma_process_per_hour: float = 0.4,
                 mean_reversion_per_hour: float = 0.25,
                 sigma_pws: float = 0.6,
                 sigma_metar_round: float = 0.29,
                 fallback_pws_bias: float = 0.0,
                 # robustness guards (each fixed a documented live failure)
                 cold_start_warmup_h: float = 3.0,
                 innovation_outlier_k: float = 2.0,
                 innovation_inflation_max: float = 5.0,
                 solar_sigma_alpha: float = 1.0,
                 solar_bias_beta: float = 0.5) -> None:
        self.market_id = market_id
        self.tz = timedelta(hours=tz_hours)
        self.target_local_date = target_local_date
        self.publish_minutes = sorted(set(publish_minutes or [20, 50]))

        self.sigma_process_h = sigma_process_per_hour
        self.beta_h = mean_reversion_per_hour
        self.sigma_pws = max(0.5, sigma_pws)   # floor prevents over-trust -> degenerate P_now
        self.sigma_metar = sigma_metar_round
        self.fallback_pws_bias = fallback_pws_bias

        self.cold_start_warmup_h = cold_start_warmup_h
        self.innov_k = innovation_outlier_k
        self.innov_max = innovation_inflation_max
        self.solar_alpha = solar_sigma_alpha
        self.solar_beta = solar_bias_beta

        # posterior
        self._T_mean: Optional[float] = None
        self._T_var: float = 5.0               # initial uncertainty
        self._last_update_time: Optional[datetime] = None
        self._last_update_kind: Optional[str] = None

        # data buffers (injected by the replay driver)
        self._pws: List[Tuple[datetime, float, Optional[float]]] = []   # (t, temp, solar)
        self._metars: List[Tuple[datetime, int]] = []
        self._forecast_curve: Dict[int, float] = {}
        self._forecast_peak: Optional[float] = None

        self._pws_processed_until: Optional[datetime] = None
        self._metars_processed_until: Optional[datetime] = None
        self._pws_bias: float = fallback_pws_bias

        # replay clock — the driver sets this before each refresh
        self._replay_now: Optional[datetime] = None

    # ---- replay clock & time helpers ----
    def set_now(self, when: datetime) -> None:
        self._replay_now = when

    def _now(self) -> datetime:
        return self._replay_now or datetime.now(timezone.utc)

    def _local(self, dt: datetime) -> datetime:
        return (dt + self.tz).replace(tzinfo=None)

    def _day_window(self) -> Tuple[datetime, datetime]:
        td = self.target_local_date
        d0 = datetime(td.year, td.month, td.day, tzinfo=timezone.utc) - self.tz
        return d0, d0 + timedelta(days=1)

    # ---- data injection (replaces the live pollers) ----
    def add_pws(self, t: datetime, temp_c: float, solar_rad: Optional[float]) -> None:
        self._pws.append((t, temp_c, solar_rad))

    def add_metar(self, valid: datetime, temp: int) -> None:
        if valid not in {t for t, _ in self._metars}:
            self._metars.append((valid, int(temp)))
            self._metars.sort()

    def set_forecast(self, times_hh: List[str], temps: List[float],
                     peak: Optional[float]) -> None:
        curve: Dict[int, float] = {}
        for t_str, val in zip(times_hh, temps):
            try:
                curve[int(t_str.split(":")[0])] = float(val)
            except (ValueError, IndexError):
                continue
        if curve:
            self._forecast_curve = curve
            self._forecast_peak = peak if peak is not None else max(temps)

    # ---- forecast pull ----
    def _forecast_at(self, when: datetime) -> Optional[float]:
        if not self._forecast_curve:
            return None
        local = self._local(when)
        h = local.hour + local.minute / 60.0
        keys = sorted(self._forecast_curve.keys())
        if h <= keys[0]:
            return self._forecast_curve[keys[0]]
        if h >= keys[-1]:
            return self._forecast_curve[keys[-1]]
        for i in range(len(keys) - 1):
            if keys[i] <= h <= keys[i + 1]:
                frac = (h - keys[i]) / (keys[i + 1] - keys[i])
                return self._forecast_curve[keys[i]] * (1 - frac) + self._forecast_curve[keys[i + 1]] * frac
        return None

    # ---- Kalman: process advance + observation update ----
    def _process_advance(self, target_time: datetime) -> None:
        """Advance T(t) to target_time under the process model."""
        if self._T_mean is None:
            return
        if self._last_update_time is None:
            self._last_update_time = target_time
            return
        dt_h = (target_time - self._last_update_time).total_seconds() / 3600.0
        if dt_h <= 0:
            return
        fc = self._forecast_at(target_time)
        if fc is not None and self.beta_h > 0:
            pull = self.beta_h * dt_h
            self._T_mean = (1 - pull) * self._T_mean + pull * fc
        if self.beta_h > 0:
            decay = (1 - self.beta_h * dt_h) ** 2
            self._T_var = max(1e-6, self._T_var * decay + (self.sigma_process_h ** 2) * dt_h)
        else:
            self._T_var = max(1e-6, self._T_var + (self.sigma_process_h ** 2) * dt_h)
        self._last_update_time = target_time

    def _observation_update(self, obs: float, obs_var: float, obs_offset: float = 0.0,
                            kind: str = "?") -> None:
        """Kalman update with innovation-based variance inflation.

        If an observation lands more than k sigma from the posterior (~95% outlier),
        the posterior variance is force-inflated first — this stops a tight overnight
        posterior from rejecting genuine daytime warming (a documented live failure)."""
        if self._T_mean is None:
            self._T_mean = obs - obs_offset
            self._T_var = obs_var
            self._last_update_kind = kind
            return
        innovation = (obs - obs_offset) - self._T_mean
        innov_std = math.sqrt(self._T_var + obs_var)
        if abs(innovation) > self.innov_k * innov_std and innov_std > 0:
            factor = (abs(innovation) / (self.innov_k * innov_std)) ** 2
            self._T_var = min(self.innov_max, self._T_var * factor)
        self._T_mean, self._T_var = kalman_update(self._T_mean, self._T_var, obs, obs_var, obs_offset)
        self._last_update_kind = kind

    # ---- ingestion (chronological, day-window filtered) ----
    def _current_bias(self) -> float:
        pws_series = [(t, v) for t, v, _ in self._pws]
        ra = rolling_bias(self._metars, pws_series, self._now(), window_min=150)
        self._pws_bias = ra if ra is not None else self.fallback_pws_bias
        return self._pws_bias

    def _ingest_pws(self) -> int:
        n = 0
        d0, d1 = self._day_window()
        now = self._now()
        new_rows = sorted(
            [(t, v, sr) for t, v, sr in self._pws
             if (self._pws_processed_until is None or t > self._pws_processed_until)
             and d0 <= t <= d1 and t <= now],
            key=lambda x: x[0],
        )
        if not new_rows:
            return 0
        # cold start: only observations from the last warmup window, so the posterior
        # initializes near the current temperature instead of anchoring on old readings
        if self._T_mean is None and self._last_update_time is None:
            cutoff = now - timedelta(hours=self.cold_start_warmup_h)
            new_rows = [r for r in new_rows if r[0] >= cutoff]
            if not new_rows:
                return 0
            self._last_update_time = new_rows[0][0]
        # correction = rolling mean of (METAR - PWS): how much to ADD to a raw PWS
        # reading to align it with the official instrument. The observation model is
        # y = T + offset, so offset = (PWS - TRUE) = -correction; the solar term then
        # ADDS to the offset (sun-baked stations over-read -> reading pushed down).
        correction = self._current_bias()
        for t, pv, solar in new_rows:
            sigma_eff, offset_eff = solar_regime_adjustment(
                solar, self.sigma_pws, -correction,
                alpha=self.solar_alpha, beta=self.solar_beta)
            self._process_advance(t)
            self._observation_update(pv, sigma_eff ** 2, obs_offset=offset_eff, kind="pws")
            n += 1
        self._pws_processed_until = new_rows[-1][0]
        return n

    def _ingest_metars(self) -> int:
        n = 0
        d0, d1 = self._day_window()
        now = self._now()
        new_metars = sorted(
            [(t, m) for t, m in self._metars
             if (self._metars_processed_until is None or t > self._metars_processed_until)
             and d0 <= t <= d1 and t <= now],
            key=lambda x: x[0],
        )
        if self._T_mean is None and self._last_update_time is None and new_metars:
            self._last_update_time = new_metars[0][0]
        for t, m in new_metars:
            self._process_advance(t)
            self._observation_update(float(m), self.sigma_metar ** 2, kind="metar")
            n += 1
        if new_metars:
            self._metars_processed_until = new_metars[-1][0]
        return n

    # ---- outcome distributions ----
    def _sample_remaining_paths(self, n_paths: int = 10000,
                                rng: Optional[np.random.Generator] = None) -> Optional[np.ndarray]:
        """Sample hourly paths from the CURRENT posterior to end of local day.
        Starting from the posterior (not the latest raw observation) is what fixed
        the 'binary cliff' problem in the legacy engine."""
        if self._T_mean is None:
            return None
        now = self._now()
        _, d1 = self._day_window()
        next_h = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        hours: List[datetime] = []
        h = next_h
        while h <= d1:
            hours.append(h)
            h += timedelta(hours=1)
        if not hours:
            return None
        rng = rng or np.random.default_rng(0)
        T_now = self._T_mean + rng.standard_normal(n_paths) * math.sqrt(self._T_var)
        paths = np.zeros((n_paths, len(hours)))
        prev, prev_t = T_now, now
        for i, t in enumerate(hours):
            dt_h = (t - prev_t).total_seconds() / 3600.0
            fc = self._forecast_at(t)
            if fc is not None and self.beta_h > 0:
                pull = self.beta_h * dt_h
                drift = (1 - pull) * prev + pull * fc
            else:
                drift = prev
            cur = drift + rng.standard_normal(n_paths) * math.sqrt(self.sigma_process_h ** 2 * dt_h)
            paths[:, i] = cur
            prev, prev_t = cur, t
        return paths

    def _daily_max_distribution(self, M_floor: Optional[int]) -> Dict[int, float]:
        paths = self._sample_remaining_paths()
        if paths is None or paths.size == 0:
            if M_floor is not None:
                return {int(M_floor): 1.0}
            return {int(round(self._T_mean or 0)): 1.0}
        rounded = np.floor(np.max(paths, axis=1) + 0.5).astype(int)
        if M_floor is not None:
            rounded = np.maximum(rounded, M_floor)
        unique, counts = np.unique(rounded, return_counts=True)
        dist = {int(k): float(c / len(rounded)) for k, c in zip(unique, counts)}
        # degeneracy guard: >=98.5% certainty about a FUTURE max above the confirmed
        # one is over-trust (a single noisy reading collapsing sigma); bleed 4pp into
        # the neighbours. Confidence that the max is NOT exceeded is left alone.
        if dist:
            top_k = max(dist, key=dist.get)
            future_call = (M_floor is None) or (top_k > M_floor)
            if future_call and dist[top_k] >= 0.985:
                bleed = 0.04
                dist[top_k] -= bleed
                dist[top_k - 1] = dist.get(top_k - 1, 0.0) + bleed * 0.6
                dist[top_k + 1] = dist.get(top_k + 1, 0.0) + bleed * 0.4
        return dist

    def _next_metar_eta_min(self) -> float:
        local_now = self._local(self._now())
        m_now = local_now.minute + local_now.second / 60.0
        future = [pm for pm in self.publish_minutes if pm > m_now]
        return (future[0] - m_now) if future else (60 - m_now) + self.publish_minutes[0]

    def _next_metar_distribution(self) -> Dict[int, float]:
        """Analytic: advance the posterior to the next publish minute, round-PMF it."""
        if self._T_mean is None:
            return {}
        target = self._now() + timedelta(minutes=self._next_metar_eta_min())
        saved = (self._T_mean, self._T_var, self._last_update_time)
        self._process_advance(target)
        mu, sigma = self._T_mean, math.sqrt(self._T_var)
        self._T_mean, self._T_var, self._last_update_time = saved
        return gaussian_round_pmf(mu, sigma, (int(math.floor(mu - 4 * sigma)),
                                              int(math.ceil(mu + 4 * sigma))))

    def distribution_given(self, m: int) -> Dict[int, float]:
        """P(daily_max = k | next METAR = m): apply the hypothetical observation,
        rerun the Monte Carlo, restore state."""
        if self._T_mean is None:
            return {}
        saved = (self._T_mean, self._T_var, self._last_update_time)
        target = self._now() + timedelta(minutes=self._next_metar_eta_min())
        self._process_advance(target)
        self._observation_update(float(m), self.sigma_metar ** 2, kind="metar_hypothetical")
        d0, d1 = self._day_window()
        today = [v for t, v in self._metars if d0 <= t <= d1 and t <= self._now()]
        M_cur = max(today, default=None)
        M_floor = max(M_cur, m) if M_cur is not None else m
        dist = self._daily_max_distribution(M_floor)
        self._T_mean, self._T_var, self._last_update_time = saved
        return dist

    def consistency_residual(self) -> float:
        """max_k | sum_m Q(m) * D_after(k|m) - P_now(k) | — should be ~MC noise."""
        snap = self.snapshot()
        if not snap.Q_next:
            return 0.0
        mix: Dict[int, float] = {}
        for m, qm in snap.Q_next.items():
            for k, p in self.distribution_given(m).items():
                mix[k] = mix.get(k, 0.0) + qm * p
        keys = set(mix) | set(snap.P_now)
        return max(abs(mix.get(k, 0.0) - snap.P_now.get(k, 0.0)) for k in keys)

    # ---- refresh + snapshot ----
    def refresh(self) -> Snapshot:
        self._ingest_pws()
        self._ingest_metars()
        now = self._now()
        self._process_advance(now)
        d0, d1 = self._day_window()
        today = [v for t, v in self._metars if d0 <= t <= d1 and t <= now]
        M = max(today, default=None)
        self._snap = Snapshot(
            when=now,
            T_mean=self._T_mean if self._T_mean is not None else 0.0,
            T_sigma=math.sqrt(self._T_var) if self._T_mean is not None else 999.0,
            P_now=self._daily_max_distribution(M),
            Q_next=self._next_metar_distribution(),
            confirmed_max=M,
            forecast_peak=self._forecast_peak,
            last_update_kind=self._last_update_kind,
        )
        return self._snap

    def snapshot(self) -> Snapshot:
        if getattr(self, "_snap", None) is None:
            return self.refresh()
        return self._snap


__all__ = ["KalmanWeatherModel", "Snapshot", "gaussian_round_pmf", "kalman_update",
           "solar_regime_adjustment", "interp_pws", "rolling_bias"]
