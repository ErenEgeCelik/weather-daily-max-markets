# Calibration and sensor research

Two studies must be distinguished. The sensor-feature study considered three stations over roughly
92 days, using conditional association and leave-one-day-out evaluation. The separate parameter
search covered 28 markets and compared fitted parameters with defaults.

That parameter search reported approximately 24% average Brier improvement on the same days used
to choose the parameters. It is a fitted replay comparison, not an out-of-sample improvement.
The inspected fitter scores candidates and defaults against the same day list. The report says
the final configuration had not been deployed. Archived Open-Meteo inputs also differ from the
live Wunderground forecast source, introducing a provider-transfer limitation.

A future held-out evaluation would freeze parameter choice before later dates, use forecasts
available at each decision time, preserve station/date clusters, and compare both model calibration
and baseline calibration. This is an evaluation requirement, not a result already achieved here.

The original feature and tuning pipelines are not included in this first runnable package.
