# tests/test_min_on_time.py - minimalny czas pracy grzałki po załączeniu
#
# Bez tego PV balansujące tuż przy progu reguły (np. zimowe 6000 W) włączało i
# gasiło grzałkę co tick - przekaźnik "strzelał". Zwykłe (regułowe) zdjęcie
# teraz czeka na min_on_time; zdejmowanie awaryjne (przeciążenie, przegrzanie,
# brak danych) go pomija - patrz docstring CWUController i config.py.
from fakes import build, feed, check, done

SURPLUS = dict(soc=95, pv=1500, battery=-4200)   # reguła (80, 1000) -> 1 grzałka
NO_SURPLUS = dict(soc=95, pv=1500, battery=0)    # bilans -> 0 grzałek

# --- świeżo załączona grzałka jest trzymana, dopóki nie minie min_on_time ---
# settle_ticks=0: inaczej pierwsze kilka ticków po zmianie i tak nie wywołałoby
# _apply_level wcale (patrz test_control_flow.py "stabilizacja średniej") - tu
# badamy wyłącznie min_on_time, nie settle.
clock, pub, params, relay, ctrl = build(min_on_time=150.0, settle_ticks=0)
feed(params, **SURPLUS)
ctrl.tick()
check("nadwyżka -> jedna grzałka", relay.requested_count() == 1)

clock.advance(60.0)
feed(params, **NO_SURPLUS, samples=3)
ctrl.tick()
check("po 60 s (< 150 s) nadwyżka znika, ale grzałka trzymana",
      relay.requested_count() == 1 and "min. czas pracy" in ctrl.reason)

clock.advance(60.0)
ctrl.tick()
check("po 120 s wciąż trzymana", relay.requested_count() == 1)

clock.advance(31.0)   # łącznie 151 s od załączenia
ctrl.tick()
check("po 151 s minimum minęło - grzałka wreszcie schodzi", relay.requested_count() == 0)

# --- zdejmowanie awaryjne (przeciążenie baterii) nie czeka na min_on_time ---
clock, pub, params, relay, ctrl = build(min_on_time=150.0)
feed(params, **SURPLUS)
ctrl.tick()
check("jedna grzałka pracuje", relay.requested_count() == 1)
feed(params, soc=95, pv=0, battery=5000, samples=0)   # bateria oddaje 5 kW > 2 kW progu
ctrl.tick()
check("mimo świeżego załączenia, przeciążenie zdejmuje natychmiast",
      relay.requested_count() == 0)
check("powód mówi o oddawaniu baterii", "bateria oddaje" in ctrl.reason)

# --- przegrzanie bufora gasi natychmiast, nawet świeżo załączoną grzałkę ---
clock, pub, params, relay, ctrl = build(min_on_time=150.0)
feed(params, **SURPLUS)
ctrl.tick()
check("jedna grzałka pracuje", relay.requested_count() == 1)
feed(params, temps=(90.0, 80.0), samples=0)   # przegrzanie bufora
ctrl.tick()
check("przegrzanie zdejmuje natychmiast, mimo świeżego załączenia",
      relay.requested_count() == 0)

# --- brak danych z falownika gasi natychmiast, nawet świeżo załączoną grzałkę ---
clock, pub, params, relay, ctrl = build(min_on_time=150.0)
feed(params, **SURPLUS)
ctrl.tick()
check("jedna grzałka pracuje", relay.requested_count() == 1)
clock.advance(130.0)   # > PARAM_TTL (120 s domyślnie w build()? patrz fakes.py) - dane wygasają
ctrl.tick()
check("brak świeżych danych z falownika -> gasimy natychmiast",
      relay.requested_count() == 0 and "brak danych z falownika" in ctrl.reason)

# --- przy zdejmowaniu kilku na raz trzymamy tylko te, które nie wyrobiły minimum ---
clock, pub, params, relay, ctrl = build(min_on_time=150.0)
relay.request(0, True)
relay.request(1, True)
ctrl._on_since[0] = clock.t - 200.0   # 200 s - wyrobiła minimum
ctrl._on_since[1] = clock.t - 50.0    # 50 s - jeszcze nie
ctrl._apply_level(0)
check("zdejmujemy tylko grzałkę, która wyrobiła minimum",
      relay.requested == [False, True, False])
check("powód mówi o trzymaniu minimum", "min. czas pracy" in ctrl.reason)

# --- grzałka trzymana ponad minimum i tak schodzi, gdy kolejny tick to potwierdzi ---
clock, pub, params, relay, ctrl = build(min_on_time=150.0)
relay.request(0, True)
ctrl._on_since[0] = clock.t - 200.0
ctrl._apply_level(0)
check("grzałka po minimum schodzi od razu", relay.requested_count() == 0)
check("on_since wyczyszczony po zejściu", ctrl._on_since[0] is None)

done()
