# cwu-controller

Dumps photovoltaic surplus into the hot water tank's three immersion heaters, running as a
standalone service on a Raspberry Pi. It reads the inverter over MQTT, decides how many
heaters the surplus can afford, and switches them through
[waveshare-mqtt-gateway](https://github.com/pradki/waveshare-mqtt-gateway) - never touching the
relay board itself. When it has a choice of which heater to start, it starts the one on the
least loaded phase.

```
 ┌──────────────────┐  battery_soc / battery_power        ┌──────────────────┐
 │ deye-modbus-mqtt │  pv1_power / pv2_power ────────────▶│                  │
 │ (ESP32, 20 s)    │  load_power_l1 / l2 / l3            │                  │
 └──────────────────┘                                     │  cwu-controller  │
 ┌──────────────────┐  buff_top_temp / buff_upmid_temp    │   (this repo)    │
 │ tank sensors     │ ───────────────────────────────────▶│                  │
 └──────────────────┘            MQTT broker              └────────┬─────────┘
                                                                    │ flash_on every 20 s
                                                                    ▼
                                        ┌───────────────────────┐  Modbus  ┌────────────┐
                                        │ waveshare-mqtt-gateway│ ────────▶│ 3 x 2 kW   │
                                        └───────────────────────┘          │ heaters    │
                                                                            └────────────┘
```

It is a rewrite of the `cavecwu` controller from [pradki/cave](https://github.com/pradki/cave),
kept behaviourally the same where the behaviour was right and changed in three places:

| | `cavecwu` | this repo |
|---|---|---|
| Relay access | own TCP connection to the board | MQTT commands to the gateway |
| Which heater starts | the one with the least accumulated runtime | the one on the least loaded phase |
| Full battery (SOC > 99 %) | all three heaters at once | probe upward one at a time, shed on discharge |
| Ramping up | straight to the computed level | one heater per 5 min, capped by a 15-minute average |
| Load ceiling | none | shed when the battery lends too much or the inverter's rating is reached |
| On-time accounting | own CSV writer + runtime counters | the gateway's `counters` topics |

## What it decides, and why that way

### The surplus rules

Every 20 s (the inverter's own publish interval - faster has nothing new to read) the
controller walks a table of `(SOC threshold, PV threshold)` rules from the highest SOC down.
The first rule where **both** `soc > SOC threshold` and averaged `pv_power > PV threshold` wins
and sets the heater count:

```python
level = int((balance + pv_threshold) / -HEATER_POWER)   # clamped to 0..3
balance = avg(battery_power) - energized_heaters * HEATER_POWER
```

`battery_power` is positive when the battery discharges, so `balance` is what the battery would
be doing *without* our heaters - negative means charging, i.e. real surplus. The PV threshold
does double duty: it gates the rule, and it is subtracted as a reserve, so the batteries always
keep a slice of the production.

| SOC above | PV above | Meaning |
|---|---|---|
| 90 % | 400 W | Battery nearly full - almost all surplus may go to water |
| 80 % | 1000 W | |
| 70 % | 2000 W | |
| 60 % | 3000 W | |
| 50 % | 4000 W | |
| 40 % | 5000 W | Low battery - only a big sunny surplus justifies heating |

Worked example: `SOC 85 %`, averaged PV `1.5 kW`, battery charging at `4.2 kW`, no heater
running. Rule `(80, 1000)` matches, so `level = int((-4200 + 1000) / -2000) = int(1.6) = 1` -
one heater, and roughly 2.2 kW of charging left for the battery.

**A full battery has no balance to read.** Above `SOC_FULL` (99 %) the arithmetic above stops
working: a full battery simply stops charging and the inverter curtails the rest, so
`battery_power` sits at zero no matter how much sun is going spare. `cavecwu` handled that by
switching on everything, which is how you end up with 6 kW of heaters on a house that was
already drawing 6 kW. Instead the controller probes: add **one** heater, wait, and look at
whether the battery started lending power.

- Battery drawing less than `FULL_SOC_ADD_BELOW` (200 W) on **both** the 2- and 15-minute
  average - the heaters that run are covered by production, so try one more.
- Battery drawing more than `SHED_DISCHARGE_ABOVE` (2 kW) - drop one, immediately (below).
- Anything in between - hold. The gap between the two thresholds is deliberate: adding a heater
  whenever the draw is "just under one heater's worth" would add, cross the threshold, shed, and
  repeat forever.

**Hysteresis near a full battery.** Above `SOC 95 %` with more than 800 W coming in, a
`(94, -1000)` rule is prepended - a *negative* PV threshold, which permits pulling up to 1 kW
out of the battery to keep the water heating. It stays armed until SOC falls back to 94 %,
because without that latch the heaters would chatter around the 95 % mark.

### Averaging, and why up and down are not symmetric

Battery power, PV production and all three phase loads are fed through a moving average before
any of them is used for a decision. Battery power and PV get **two** windows, because they
answer different questions:

| Window | Default | Question |
|---|---|---|
| Short | 6 samples x 20 s = 2 min | what is happening now |
| Long | 45 samples x 20 s = 15 min | how much of it can be sustained |

A heater is only added if **both** windows agree it fits. With 3 kW of average surplus that
means one or two heaters running steadily, instead of three whenever the sun is out and zero
whenever it is not. Coming down, the short window rules on its own - a real collapse in surplus
must not wait fifteen minutes to be believed.

Adding is deliberately slow, shedding deliberately fast:

- **Up: one heater per `RAMP_UP_HOLD`** (5 min), no matter how much surplus the balance claims.
  A heater changes the measurement it was decided from, so the next step has to be judged on the
  new state, not the old one.
- **Up: nothing at all until the average has caught up.** After every change the controller sits
  out `SETTLE_TICKS` ticks; until the window has absorbed the new lineup, the balance still
  describes the previous one, and deciding on it is how you build an oscillator.
- **Up: nothing while a released heater's relay is still winding down**, because its 2 kW is
  still inside the inverter's measurement.
- **Down: as many heaters as the balance says, in one tick**, once the average is settled.
- **Down: one heater per tick with no waiting at all** when it is the overload guard talking.

### The overload guard

The failure this is built around: house at ~6 kW (oven, pump, the usual), battery charged, so
heaters went on. Then a cloud - production collapsed, the battery went to its 8 kW limit and the
inverter started importing another 4 kW from the grid. Twelve kilowatts of draw to heat water
with power that was not there.

Two conditions, both checked on the **instantaneous** reading rather than an average, because
this is protection and not regulation:

- **Battery lending more than `SHED_DISCHARGE_ABOVE`** (default: one heater's worth) - the
  heaters are running off the battery, not off surplus. Shed one, every tick, until it is not.
- **Phase loads summing above `MAX_TOTAL_LOAD`** (the inverter's rating) - shed one regardless of
  what the battery is doing.

Both bypass the settle window and the ramp hold entirely. The draw figure has any
already-released-but-still-energized heaters subtracted first: their power is about to leave the
measurement anyway, and shedding more on their account would empty the bank for nothing.

The same ceiling gates additions: a heater is only added if the averaged total load plus one
heater still fits under `MAX_TOTAL_LOAD`. With a 3 x 2 kW bank behind an 8 kW inverter, that
means the third heater only ever runs when the rest of the house is drawing under 2 kW - which
is the intended trade-off, not an accident.

### Picking the heater: phase load, not runtime

Each heater sits on its own phase, and each phase's load is published by the inverter as
`load_power_lN`. To start a heater the controller picks the phase with the lowest average load;
to stop one it picks the highest. The load figures have our own energized heaters subtracted
first - otherwise a phase we are already heating on would always look like the most loaded one,
and the controller would just switch it off and on again forever.

The one case where that subtraction changes the answer is a heater that has been released but
whose relay is still winding down: it costs its phase nothing extra to re-request, so it is
preferred over adding 2 kW to a nominally quieter phase.

Wear levelling is not this service's job any more - `cavecwu` tracked per-heater runtime to
spread it evenly, and the gateway now publishes exactly that as retained `counters` topics.

### Safety

- **Overheat.** Above `MAX_TEMP` (85 °C) on either tank sensor, everything is released.
- **No temperature reading is treated as a fault**, not as a cold tank. Same rule as `cavecwu`.
- **No inverter data means no heating.** Every value carries a TTL; when it lapses the value
  reports itself as missing instead of silently reading zero. A dead ESP32 otherwise looks
  exactly like a calm night, and nothing would ever notice.

## How the heaters stay on (and how they let go)

A heater is held on by the relay board's **hardware** `flash_on` pulse, re-sent every 20 s:

```json
{"cmd": "flash_on", "duration": 60, "priority": 10}
```

That is the entire watchdog. If this process dies, loses the broker, or simply stops pinging,
the pulse burns down on its own after `FLASH_DURATION` and the relay drops out - nobody has to
come and clean up, and because the countdown lives in the relay module, even a hung gateway
cannot leave 6 kW running.

The price is that switching off is not instant. A channel with a live pulse is deliberately
"hands-off" for the gateway (it will not fight the hardware timer), so a released heater keeps
running for up to `FLASH_DURATION` seconds after the last ping. `FLASH_DURATION` is the knob on
that trade-off: shorter reacts faster to a collapsing surplus, longer tolerates a longer MQTT
outage. At the default 60 s pulse with 20 s pings, a heater releases 40-60 s after the
decision, and an MQTT outage costs the heaters after one minute.

A released heater also gets an explicit `off` with a short TTL. That cannot shorten the
hardware pulse, but it leaves an unambiguous "this service no longer wants this channel" in the
channel's retained status - otherwise the last thing anyone would see there is our `flash_on`.

Measured on the board (2026-08-18), because both of these look like shortcuts and neither is:

- `off` during a running pulse does nothing at all. The channel drops `duration` seconds after
  the last `flash_on`, and not before.
- **`flash_off` is not a faster off.** It is the *inverse* pulse: it opens the coil for
  `duration` and then **closes it again**. Used to switch a heater off, it leaves the heater
  running - and running without any lease behind it, so nothing is watching it. Stopping the
  pings is the only off there is.

The controller keeps its own model of what the hardware is doing (a pulse lasts
`FLASH_DURATION` from the last ping) and uses *that* for the power balance, rather than waiting
for the gateway's status topic to come back. The retained status is subscribed to anyway and
compared against the model: a mismatch means somebody outranked us on a channel or our commands
are not arriving, and it gets logged as a warning.

**Ping and decision are deliberately coupled.** The ping loop refuses to renew a pulse if the
control loop has not completed a tick within `DECISION_MAX_AGE`. A ping loop that kept running
on its own would hold the heaters on the last decision forever - which is precisely the failure
the pulse exists to prevent.

## Install

Requires Python 3.10+ and a running
[waveshare-mqtt-gateway](https://github.com/pradki/waveshare-mqtt-gateway) with `cwu_l1`,
`cwu_l2`, `cwu_l3` aliases on the module you point this at.

```bash
cd /home/sqna/cave/raspberry
git clone git@github.com:pradki/cwu-controller.git
cd cwu-controller
/home/sqna/venv/cave/bin/pip install -r requirements.txt
cp config.example.py config.py   # then edit config.py, see below
sudo cp cwu-controller.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cwu-controller
```

### Day-to-day

```bash
sudo systemctl status cwu-controller
journalctl -u cwu-controller -f          # every tick logs its level and the reason for it

cd /home/sqna/cave/raspberry/cwu-controller
git pull
sudo systemctl restart cwu-controller    # heaters release within FLASH_DURATION, then resume
```

Restarting is cheap: the service holds no persistent state, and the inverter topics are
retained, so it has a full picture within a second of connecting.

## Configuration

Everything lives in `config.py` (gitignored - `config.example.py` is the tracked template,
since `config.py` normally holds a real LAN address).

- **`HEATERS`** - one entry per heater: `name` is the channel alias in the *gateway's* config,
  `phase` is which `load_power_lN` it draws from. Adding a fourth heater is one more entry;
  nothing else assumes there are three.
- **`RELAY_MODULE`** - the gateway module the aliases live on (`relay_test` by default).
- **`RULES`**, **`HYSTERESIS_RULE`**, **`SOC_*`**, **`PV_HYSTERESIS_ON`** - the surplus table
  described above.
- **`FLASH_DURATION`** / **`PING_INTERVAL`** - the off-latency vs. outage-tolerance trade-off.
  Keep the pulse comfortably longer than the ping interval.
- **`AVERAGE_SAMPLES`** / **`AVERAGE_SAMPLES_LONG`** / **`SETTLE_TICKS`** - the two smoothing
  windows, and how long to sit still after a change. All three count ticks of `CONTROL_INTERVAL`.
- **`RAMP_UP_HOLD`** - minimum seconds between two additions. Lower it if the ramp feels too
  timid on clear mornings; it has no effect on shedding.
- **`MAX_TOTAL_LOAD`** - the inverter's rating, in watts. `None` disables the ceiling (also what
  happens automatically when `load_power_l*` is missing).
- **`SHED_DISCHARGE_ABOVE`** / **`FULL_SOC_ADD_BELOW`** - how much battery discharge is tolerated
  while heating, and how little is required to try another heater. Lowering `SHED_DISCHARGE_ABOVE`
  to ~1 kW makes the controller stingier with the battery, matching what `HYSTERESIS_RULE`
  allows; keep it comfortably above `FULL_SOC_ADD_BELOW` either way.
- **`LEASE_PRIORITY`** - what to claim channels with in the gateway's arbitration. Leave 0 for
  dashboards and manual commands so a human can outrank the automation if needed.

## MQTT

### Subscribed

| Topic | From | Used for |
|---|---|---|
| `cave/deye/params/battery_soc` | deye-modbus-mqtt | rule selection |
| `cave/deye/params/battery_power` | deye-modbus-mqtt | power balance (averaged) |
| `cave/deye/params/pv1_power`, `pv2_power` | deye-modbus-mqtt | production (summed, averaged) |
| `cave/deye/params/load_power_l1`..`l3` | deye-modbus-mqtt | phase balancing (averaged) |
| `cave/sensors/temperature/buff_top_temp` | tank sensor | overheat cut-out |
| `cave/sensors/temperature/buff_upmid_temp` | tank sensor | overheat cut-out |
| `cave/relay/relay_test/cwu_l*/status` | waveshare-mqtt-gateway | verification only |

Payloads are the usual `{"value": ...}` shape; a bare number on the topic is accepted too.

### Published

Commands to the gateway, `cave/relay/<module>/<heater>/cmd/cwu-controller`:

```json
{"cmd": "flash_on", "duration": 60, "priority": 10}
{"cmd": "off", "ttl": 120, "priority": 10}
```

Own state to `cave/cwu/status`, retained, once per tick - everything needed to see why the
controller is doing what it is doing:

```json
{
  "level": 1, "reason": "SOC 85%>80 PV 1500W>1000W, bilans -4200W -> 1.60",
  "requested": ["cwu_l3"], "energized": ["cwu_l3"],
  "battery_soc": 85.0,
  "battery_power_now": -4200.0, "battery_power_avg": -4200.0, "battery_power_avg_long": -4200.0,
  "pv_power_avg": 1500.0, "pv_power_avg_long": 1500.0,
  "load_total_now": 3900.0, "ramp_up_wait": 300.0,
  "phase_load_avg": [2000.0, 1500.0, 400.0], "phase_load_net": [2000.0, 1500.0, -1600.0],
  "hysteresis": false, "samples": 6,
  "buff_top_temp": 60.0, "buff_upmid_temp": 55.0,
  "inverter_data": true, "load_data": true, "ticks": 1, "ts": 1787028801
}
```

`reason` is the one field worth reading first - it says which rule fired, what the balance was,
and which brake (settle window, ramp hold, load ceiling) is currently holding things back.
`ramp_up_wait` is the seconds left before another heater may be added.

On-time counters are *not* published here - the gateway already tracks them per channel at
`cave/relay/<module>/<heater>/counters` (today/month/year/total).

## Tests

Fake clock, fake broker, no hardware and no MQTT connection required. Every test file is a
plain script that prints `OK`/`FAIL` per assertion and exits non-zero if anything failed:

```bash
for t in tests/test_*.py; do python "$t" || exit 1; done
```

| File | Covers |
|---|---|
| `tests/test_params.py` | TTL expiry, both average windows, stale data clearing them |
| `tests/test_levels.py` | the surplus rule table, clamping, hysteresis, the 15-min cap |
| `tests/test_phase_balance.py` | least/most loaded phase selection, own-load subtraction, ties |
| `tests/test_full_soc.py` | the full-battery probe: ladder up, dead band, shedding back down |
| `tests/test_ramp.py` | one heater per hold, fast multi-heater shedding, a steady hour |
| `tests/test_overload.py` | the 6 kW-house incident end to end, both overload conditions |
| `tests/test_relay_client.py` | command payloads, pulse renewal, release latency |
| `tests/test_control_flow.py` | overheat, missing data, settle ticks, decision watchdog, status |
| `tests/test_mqtt_client.py` | payload parsing, topic routing, subscriptions |
| `tests/test_wiring.py` | the whole service assembled from `config.example.py` |

`tests/fakes.py` carries a `simulate()` helper that models the installation - each energized
heater adds its 2 kW to its own phase, and whatever production does not cover comes out of the
battery (or, at a full battery, gets curtailed). The overload and ramp tests run against that
rather than against hand-written numbers, so they exercise the actual feedback loop: the
controller's own switching changes the measurement it decides from.

`tests/test_wiring.py` deliberately builds the service from the tracked template rather than
the local `config.py`, so a key missing from the template fails here instead of on the Pi.

## Files

| File | Role |
|---|---|
| `config.py` / `config.example.py` | Broker, topics, heaters, timings, surplus rules |
| `params.py` | Values from the broker: TTL validity and moving averages |
| `relay_client.py` | Heaters as gateway commands; the hardware-pulse keep-alive model |
| `cwu_controller.py` | The decision: surplus rules, phase balancing, safety cut-outs |
| `mqtt_client.py` | paho thread: payload parsing, routing, publishing |
| `main.py` | Entry point: wiring (`build_service`), control loop, ping loop |
| `cwu-controller.service` | systemd unit |
| `tests/` | Fake-clock/fake-broker tests |

## Credits

- [paho-mqtt](https://pypi.org/project/paho-mqtt/) (Eclipse Public License) for the MQTT client.

---

Part of [pradki/cave](https://github.com/pradki/cave). Code comments are in Polish.
