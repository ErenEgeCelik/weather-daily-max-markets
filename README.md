# Weather Prediction Markets

**Observations, forecasts and outcome probabilities.**

How should a daily maximum-temperature distribution change when a new station reading or forecast
arrives? This project collects my weather-market research, with an offline Kalman replay as its
first runnable example. The wider programme studied source timing, sensor information, calibration
and repricing when temperature outcomes became impossible.

## Run the recorded example

Python 3.9 or newer:

```bash
python -m pip install -r requirements.txt
python -B replay.py --out output/replay.png
```

The Istanbul example covers 11 June 2026 and processes 1,165 recorded input events: 1,097 PWS
readings, 26 METAR observations and 42 forecast records. The driver orders records by their arrival
timestamps and updates the temperature posterior and daily-maximum distribution.

![Temperature posterior and daily-maximum probabilities](output/replay.png)

This one-day example demonstrates the implementation. It is not an out-of-sample forecasting score
or a trading return. The confirmed maximum in the replay is an observation-derived quantity;
contractual settlement must be checked against the individual market rule.

## Research components

- [Model](docs/model.md): posterior, forecast pull and outcome distributions.
- [Observations](docs/observations.md): arrival clocks and component boundaries.
- [Calibration](docs/calibration.md): feature studies and same-data parameter fitting.
- [Market events](docs/market-events.md): bucket repricing and evidence limits.
- [Limitations](docs/limitations.md): consistency metrics, deployment and generalization.

| File | Purpose |
|---|---|
| `kalman_engine.py` | Offline model implementation |
| `replay.py` | Arrival-ordered replay and figure generation |
| `data/` | Recorded example and provenance manifest |
| `docs/` | Methods and research scope |
| `REPRODUCIBILITY.md` | Commands and what they establish |

The collection/execution system and inference research were parts of the same programme, but their
versions and deployment states differed. In particular, the final calibration report described
an offline configuration that had not been deployed.

[Eren Ege Çelik](https://www.erenege.dev) ·
[Related crypto research](https://github.com/ErenEgeCelik/btc-5m-market-microstructure)

## License

Authored code and documentation: [MIT](LICENSE). Third-party data provenance is described in [data/README.md](data/README.md).
