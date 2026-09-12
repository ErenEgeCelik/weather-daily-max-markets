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

No account-growth number or strategy-specific return is claimed by this package.
