# Limitations and version boundaries

One day's replay does not validate predictive performance. The model's final probability concentration
cannot by itself establish calibration, market outperformance or realized profitability.

The replay prints maximum absolute per-bucket consistency error. A separate stored Munich harness
uses L1 error summed across buckets. Their thresholds are not interchangeable. The original technical
report also documents a placeholder live residual, so the replay check is not evidence of a working
production alarm. Residuals may reflect sampling error and construction differences; do not attribute
all discrepancies to Monte Carlo noise without testing that explanation.

The weather programme used different collection, inference, calibration and decision versions.
The final calibration was offline. The station and season coverage of feature research is limited.
Sensor-derived maxima and contractual settlement outcomes must remain distinct.

The [experiment index](experiments.md) keeps leave-one-day-out feature comparisons, same-data tuning,
source-substituted replay and production-journal diagnostics separate. A score audited from archived
predictions is not an independently regenerated prediction history. Sample filters and denominators
are stated per experiment.

The public decision component evaluates one-step alternatives with an explicit payoff and risk
objective. It does not solve a full Bellman equation or model an observed execution path for every
action. Historical model/decision defects and publication corrections are identified in the
[model derivation](model-derivation.md) and [decision notes](decision-implementation.md).

No account-growth number or strategy-specific return is claimed by this package.
