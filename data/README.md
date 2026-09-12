# Recorded example

The three JSONL files are the existing Istanbul example. `manifest.json` identifies their exact
bytes and row counts. `ts` is arrival time; observation time is `obs_time_utc` for PWS and `valid_utc`
for METAR. Temperature fields are Celsius. Forecast curves contain time/temperature pairs and
the available peak estimate. Preserve arrival ordering when reconstructing the available information.

These records are an illustrative one-day sample, not the original calibration or event-study
dataset. Code licensing does not imply ownership of upstream weather observations or forecast products.
No general redistribution rights for third-party data are asserted by this document.
