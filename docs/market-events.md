# Repricing after an outcome becomes impossible

For a daily-maximum market, an accepted official report can make lower maximum buckets impossible.
That changes the set of feasible outcomes immediately, while the order book can adjust through quotes
and trades over time. The research studied both the deterministic elimination rule and the repricing
of surviving contracts.

The [public rule engine](../weather_research/rules.py) exposes the historical state transitions.
The [strategy chapter](strategies.md) separates official-observation rules from an ask-price mass
heuristic; the latter is not a probability estimator. The [decision evaluator](decision-implementation.md)
adds portfolio payoff, risk and action constraints for model-based choices.

## Historical exploratory event study

The research asked how probability mass and neighboring contract prices respond when a daily
maximum-temperature bucket becomes impossible. The original event-study write-up reports 32
detections, approximately 22 independent events and five summer days, using hold-to-resolution
accounting. An inspected scan uses end-of-day book prices as outcome proxies.

The exact estimator/output behind the published summary has not been matched in this release.
Accordingly this package does not promote its return headline as an independently reproduced
market-wide finding. Overlapping events, proxy outcomes, executable depth and entry/exit timing
must be resolved before treating the event count or modeled return as general evidence.
