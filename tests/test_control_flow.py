# tests/test_control_flow.py - bezpieczniki, stabilizacja średniej, watchdog decyzji
from fakes import build, feed, check, done

SURPLUS = dict(soc=85, pv=1500, battery=-4200)      # -> poziom 1
BIG_SURPLUS = dict(soc=95, pv=1500, battery=-6500)  # -> poziom 3

# --- przegrzanie buforu gasi wszystko ---
clock, pub, params, relay, ctrl = build()
feed(params, **SURPLUS)
ctrl.tick()
check("nadwyżka -> grzejemy", relay.requested_count() == 1)
feed(params, **SURPLUS, temps=(86.0, 70.0))
ctrl.tick()
check("przegrzanie -> wszystko zwolnione", relay.requested_count() == 0 and ctrl.level == 0)
check("powód wskazuje przegrzanie", "przegrzanie" in ctrl.reason)

# --- brak pomiaru temperatury liczy się jak awaria, nie jak zimny bufor ---
clock, pub, params, relay, ctrl = build()
feed(params, **SURPLUS)
ctrl.tick()
check("start od jednej grzałki", relay.requested_count() == 1)
clock.advance(301.0)  # sensor_ttl = 300 s (dane falownika też wygasną, ale temperatura pierwsza)
ctrl.tick()
check("brak pomiaru temperatury -> stop", relay.requested_count() == 0)
check("powód mówi o braku pomiaru", "brak pomiaru temperatury" in ctrl.reason)

# --- milczący falownik: nie wolno zgadywać zerami ---
clock, pub, params, relay, ctrl = build()
feed(params, **SURPLUS)
ctrl.tick()
check("grzejemy przy świeżych danych", relay.requested_count() == 1)
clock.advance(121.0)  # param_ttl = 120 s, sensory jeszcze żyją (300 s)
ctrl.tick()
check("brak danych z falownika -> stop", relay.requested_count() == 0 and ctrl.level == 0)
check("powód mówi o falowniku", ctrl.reason == "brak danych z falownika")

# --- po zmianie poziomu czekamy, aż średnia obejmie nowy stan ---
clock, pub, params, relay, ctrl = build(settle_ticks=3)
feed(params, **SURPLUS)
ctrl.tick()
check("pierwsza grzałka włączona", relay.requested_count() == 1)
feed(params, **BIG_SURPLUS)
for i in range(3):
    ctrl.tick()
    check(f"tick {i + 1} po zmianie: poziom widzi 3, ale nie ruszamy grzałek",
          ctrl.level == 3 and relay.requested_count() == 1)
    check(f"tick {i + 1} tłumaczy, na co czeka", "stabilizacj" in ctrl.reason)
ctrl.tick()
check("po stabilizacji trzyma nas jeszcze odczekanie", relay.requested_count() == 1
      and "dokładam po jednej" in ctrl.reason)
clock.advance(300.0)
feed(params, **BIG_SURPLUS)
ctrl.tick()
check("po odczekaniu dochodzi dokładnie jedna grzałka", relay.requested_count() == 2)

# --- dogasający przekaźnik wstrzymuje decyzje ---
clock, pub, params, relay, ctrl = build(settle_ticks=0)
feed(params, **SURPLUS)
ctrl.tick()
check("grzałka włączona", relay.requested_count() == 1)
feed(params, soc=85, pv=1500, battery=200)   # nadwyżka znikła -> poziom 0
ctrl.tick()
check("nadwyżka znikła -> grzałka zwolniona", relay.requested_count() == 0)
check("ale przekaźnik jeszcze dogasa", relay.energized_count() == 1)
feed(params, **BIG_SURPLUS)
ctrl.tick()
check("w trakcie gaszenia nie podejmujemy nowych decyzji", relay.requested_count() == 0)
check("powód mówi o gaszeniu przekaźnika", "wygaszenie" in ctrl.reason)
clock.advance(61.0)
feed(params, **BIG_SURPLUS)
ctrl.tick()
check("po wygaszeniu wraca decydowanie, ale odczekanie jeszcze trzyma",
      relay.requested_count() == 0 and "dokładam po jednej" in ctrl.reason)
clock.advance(300.0)
feed(params, **BIG_SURPLUS)
ctrl.tick()
check("po odczekaniu grzałka wraca - po jednej", relay.requested_count() == 1)

# --- watchdog: wiek ostatniej decyzji ---
clock, pub, params, relay, ctrl = build()
check("przed pierwszym tickiem brak decyzji", ctrl.decision_age() is None)
feed(params, **SURPLUS)
ctrl.tick()
check("po ticku decyzja świeża", ctrl.decision_age() == 0.0)
clock.advance(75.0)
check("wiek decyzji rośnie z zegarem", ctrl.decision_age() == 75.0)

# --- status na brokerze ---
clock, pub, params, relay, ctrl = build()
feed(params, **SURPLUS, loads=(2000, 1500, 400))
pub.clear()
ctrl.tick()
status = pub.last("cave/cwu/status")
check("status publikowany jako retained", status is not None and status[2] is True)
payload = status[1]
check("status ma poziom i powód", payload["level"] == 1 and isinstance(payload["reason"], str))
check("status wymienia grzałki po nazwach", payload["requested"] == ["cwu_l3"]
      and payload["energized"] == ["cwu_l3"])
check("status podaje średnie, na których decydował",
      payload["battery_power_avg"] == -4200.0 and payload["pv_power_avg"] == 1500.0
      and payload["phase_load_avg"] == [2000.0, 1500.0, 400.0])
check("status podaje też długie okna i wartość chwilową",
      payload["battery_power_avg_long"] == -4200.0 and payload["pv_power_avg_long"] == 1500.0
      and payload["battery_power_now"] == -4200.0)
check("status pokazuje bilans faz po odjęciu naszych grzałek",
      payload["phase_load_net"] == [2000.0, 1500.0, -1600.0])
check("status podaje sumę obciążenia i odczekanie",
      payload["load_total_now"] == 3900.0 and payload["ramp_up_wait"] == 300.0)
check("status raportuje świeżość danych",
      payload["inverter_data"] is True and payload["load_data"] is True)

done()
