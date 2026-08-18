# tests/test_params.py
from fakes import FakeClock, check, done

from params import Param, Averaged, CWUParams

clock = FakeClock()

# --- Param: TTL ---
p = Param("x", ttl=100.0, default=0.0, clock=clock)
check("nic nie przyszło -> invalid", p.valid is False and p.age is None)
p.update(42.0)
check("po update -> valid, wartość widoczna", p.valid and p.value == 42.0)
clock.advance(100.0)
check("dokładnie na granicy TTL -> jeszcze valid", p.valid)
clock.advance(0.1)
check("po TTL -> invalid, wartość podmieniona na default", (not p.valid) and p.value == 0.0)
check("age liczy czas od ostatniej wartości", abs(p.age - 100.1) < 1e-6)

# --- Averaged: okno ---
clock = FakeClock()
a = Averaged("y", samples=3, ttl=100.0, clock=clock)
check("brak próbek -> średnia = default", a.average == 0.0 and a.samples == 0)
for v in (100.0, 200.0, 300.0):
    a.update(v)
    a.sample()
check("średnia z pełnego okna", a.average == 200.0 and a.samples == 3)
a.update(600.0)
a.sample()
check("okno przesuwa się (najstarsza próbka wypada)", a.average == (200 + 300 + 600) / 3)

# Wygasłe dane nie mogą podtrzymywać starej średniej.
clock.advance(200.0)
a.sample()
check("próbka po wygaśnięciu TTL czyści okno", a.samples == 0 and a.average == 0.0)
a.update(50.0)
a.sample()
check("po powrocie danych średnia liczy się od nowa", a.samples == 1 and a.average == 50.0)

# --- Averaged: dwa okna ---
clock = FakeClock()
a = Averaged("z", samples=2, long_samples=6, ttl=100.0, clock=clock)
for v in (1000.0, 1000.0, 1000.0, 1000.0, 0.0, 0.0):
    a.update(v)
    a.sample()
check("krótkie okno widzi tylko ostatnie 2 próbki", a.average == 0.0)
check("długie okno widzi całą historię", a.average_long == 4000.0 / 6)
check("oba okna raportują swoją długość", a.samples == 2 and a.samples_long == 6)

b = Averaged("bez-dlugiego", samples=2, ttl=100.0, clock=clock)
b.update(500.0)
b.sample()
check("bez długiego okna average_long zwraca krótką", b.average_long == b.average == 500.0)

# --- CWUParams ---
clock = FakeClock()
cp = CWUParams(phases=3, samples=2, param_ttl=100.0, sensor_ttl=300.0, clock=clock)
check("na starcie brak danych z falownika", cp.inverter_valid is False)
cp.battery_soc.update(80.0)
cp.battery_power.update(-3000.0)
cp.pv1_power.update(2000.0)
cp.pv2_power.update(1500.0)
cp.sample()
check("pv_power to średnia z sumy pv1+pv2", cp.pv_power.average == 3500.0)
check("pełny obraz falownika -> inverter_valid", cp.inverter_valid is True)
check("bez load_power -> load_valid False", cp.load_valid is False)
for i, lp in enumerate(cp.load_power):
    lp.update(1000.0 * (i + 1))
cp.sample()
check("load_valid po wszystkich trzech fazach", cp.load_valid is True)
check("średnie faz osobno", [lp.average for lp in cp.load_power] == [1000.0, 2000.0, 3000.0])

# Falownik milknie: pv przestaje być valid, więc pv_power nie dostaje nowych próbek.
clock.advance(101.0)
check("po TTL brak obrazu falownika", cp.inverter_valid is False and cp.pv_valid is False)
cp.sample()
check("sample() na wygasłych danych czyści średnie", cp.pv_power.samples == 0
      and cp.battery_power.samples == 0)

# --- suma obciążenia faz (do limitu mocy) ---
clock = FakeClock()
lp = CWUParams(phases=3, samples=2, param_ttl=100.0, sensor_ttl=300.0, clock=clock)
check("bez danych o fazach nie ma sumy",
      lp.load_total_now is None and lp.load_total_avg is None)
for i, sensor in enumerate(lp.load_power):
    sensor.update(1000.0 * (i + 1))
lp.sample()
check("suma chwilowa", lp.load_total_now == 6000.0)
check("suma średnich", lp.load_total_avg == 6000.0)
for sensor in lp.load_power:
    sensor.update(0.0)
check("suma chwilowa idzie za wartościami, średnia jeszcze nie",
      lp.load_total_now == 0.0 and lp.load_total_avg == 6000.0)

# --- topic_map ---
topics = cp.topic_map("cave/deye/params", "cave/sensors/temperature")
expected = {
    "cave/deye/params/battery_soc",
    "cave/deye/params/battery_power",
    "cave/deye/params/pv1_power",
    "cave/deye/params/pv2_power",
    "cave/deye/params/load_power_l1",
    "cave/deye/params/load_power_l2",
    "cave/deye/params/load_power_l3",
    "cave/sensors/temperature/buff_top_temp",
    "cave/sensors/temperature/buff_upmid_temp",
}
check("topic_map pokrywa dokładnie oczekiwane topici", set(topics) == expected)
check("topic_map wskazuje na te same obiekty Param",
      topics["cave/deye/params/load_power_l2"] is cp.load_power[1])

done()
