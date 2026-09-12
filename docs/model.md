# Temperature and outcome model

The engine maintains a scalar temperature posterior. Its process model pulls temperature toward
the available forecast curve, with process uncertainty. PWS and METAR observations have different
noise models; PWS bias is updated from paired observations. Forecast revisions enter when received.

Outputs include a daily-maximum distribution P_now(k), a next-observation distribution Q_next(m),
and conditional maximum distributions D_after(k|m). Remaining-day Monte Carlo paths approximate
the maximum distribution. The model includes warm-up and variance/degeneracy guards.

Temperatures are Celsius in this example, process time is in hours internally, and display time
is UTC+3. `replay.py` records the exact parameter values used for this illustration. Those fitted
values must not be described as universally calibrated or validated for another station or season.

The intended identity is P_now(k) approximately equal to sum_m Q_next(m) D_after(k|m). Sharing a
posterior is necessary for a coherent construction but does not guarantee the numerical
implementation. Conditional paths, observation treatment and Monte Carlo approximation matter.
