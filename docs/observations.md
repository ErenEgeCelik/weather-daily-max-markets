# Observations, clocks and system scope

`ts` is the arrival timestamp used to order replay events. PWS `obs_time_utc` and METAR `valid_utc`
are observation timestamps; they are not interchangeable with arrival time. Forecast records contain
curve timestamps and temperatures known at their arrival. Replaying final revised forecasts earlier
than they were available would introduce lookahead.

The broader programme included observation collection, execution instrumentation and model research.
This package contains the offline inference example, not the order router. Publication of a model
component does not imply every subsequent calibration or decision module ran in production.

Historical source-timing prose contains inconsistent headline lead figures. No numeric latency
advantage is claimed here without paired source timestamps. METAR serves as a model observation;
the contractual settlement source is market-specific.
