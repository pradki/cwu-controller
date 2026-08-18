# tests/fakes.py - wspólne atrapy dla testów (bez brokera i bez sprzętu)
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from params import CWUParams          # noqa: E402
from relay_client import RelayClient  # noqa: E402
from cwu_controller import CWUController  # noqa: E402

HEATERS = [
    {"name": "cwu_l1", "phase": 0},
    {"name": "cwu_l2", "phase": 1},
    {"name": "cwu_l3", "phase": 2},
]

_failures = []


def check(name, cond):
    print(("OK  " if cond else "FAIL"), name)
    if not cond:
        _failures.append(name)


def done():
    if _failures:
        print(f"done - {len(_failures)} FAILED: {', '.join(_failures)}")
        sys.exit(1)
    print("done")


class FakeClock:
    """Zegar monotoniczny pod kontrolą testu."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt


class FakePublisher:
    """Zbiera to, co poszłoby na brokera."""

    def __init__(self):
        self.messages = []

    def __call__(self, topic, payload, retain=False):
        self.messages.append((topic, payload, retain))

    def clear(self):
        self.messages = []

    def topics(self):
        return [m[0] for m in self.messages]

    def for_topic(self, topic):
        return [m for m in self.messages if m[0] == topic]

    def last(self, topic=None):
        msgs = self.messages if topic is None else self.for_topic(topic)
        return msgs[-1] if msgs else None


def build(clock=None, publisher=None, samples=6, long_samples=45, settle_ticks=6,
          flash_duration=60.0, max_total_load=8000, **kw):
    """Params + RelayClient + CWUController na wspólnym, sterowalnym zegarze.
    Domyślne nastawy jak w config.example.py, żeby testy sprawdzały to, co pojedzie
    na Pi; pojedynczy test nadpisuje sobie to, co bada."""
    clock = clock or FakeClock()
    publisher = publisher or FakePublisher()
    params = CWUParams(phases=3, samples=samples, long_samples=long_samples,
                       param_ttl=120.0, sensor_ttl=300.0, clock=clock)
    relay = RelayClient(publisher, HEATERS, "cave/relay", "relay_test", "cwu-controller",
                        priority=10, flash_duration=flash_duration, off_ttl=120.0, clock=clock)
    controller = CWUController(params, relay, heater_power=2000, settle_ticks=settle_ticks,
                               max_total_load=max_total_load,
                               publish_status=publisher, clock=clock, **kw)
    return clock, publisher, params, relay, controller


def run(clock, params, relay, ctrl, seconds, tick=20.0, **feed_kw):
    """Przewija sterowanie tak jak na Pi: co 20 s odświeżenie danych z brokera,
    ping utrzymujący impulsy i jeden tick decyzyjny. Bez pingowania przekaźniki
    gasłyby po 60 s i symulacja kłamałaby o tym, co jest załączone."""
    for _ in range(int(seconds / tick)):
        clock.advance(tick)
        if feed_kw:
            feed(params, samples=0, **feed_kw)   # próbki dokłada sam tick()
        relay.ping()
        ctrl.tick()


def simulate(clock, params, relay, ctrl, seconds, house=(0.0, 0.0, 0.0), pv=0.0, soc=100,
             heater_power=2000, tick=20.0):
    """Prosty model instalacji, żeby testować pętlę zwrotną, a nie tylko reakcję na
    wpisane liczby: obciążenie fazy = dom + nasze załączone grzałki, a to, czego nie
    pokryje PV, dokłada bateria.

    Przy pełnej baterii nadwyżka jest ucinana (battery = 0), a nie zapisywana jako
    ładowanie - i to jest właśnie powód, dla którego przy SOC 100% bilans mocy nic nie
    mówi i sterownik musi dokładać grzałki metodą prób."""
    for _ in range(int(seconds / tick)):
        clock.advance(tick)
        loads = list(house)
        for i in range(relay.count):
            if relay.energized(i):
                loads[relay.phase(i)] += heater_power
        deficit = sum(loads) - pv
        if deficit > 0:
            battery = deficit              # bateria (a przy jej limicie - sieć) dokłada
        else:
            battery = 0.0 if soc >= 100 else deficit   # ładowanie albo nadwyżka ucięta
        feed(params, soc=soc, pv=pv, battery=battery, loads=loads, samples=0)
        relay.ping()
        ctrl.tick()


def feed(params, soc=None, pv=None, battery=None, loads=None, temps=(60.0, 55.0), samples=6):
    """Wypełnia okna średnich stałą wartością, żeby średnia == podana wartość."""
    if soc is not None:
        params.battery_soc.update(soc)
    if battery is not None:
        params.battery_power.update(battery)
    if pv is not None:
        # pv1 + pv2; rozkład nie ma znaczenia, sterownik patrzy na sumę
        params.pv1_power.update(pv)
        params.pv2_power.update(0.0)
    if loads is not None:
        for lp, value in zip(params.load_power, loads):
            lp.update(value)
    if temps is not None:
        params.buff_top_temp.update(temps[0])
        params.buff_upmid_temp.update(temps[1])
    for _ in range(samples):
        params.sample()
