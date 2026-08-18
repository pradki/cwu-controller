# tests/test_ramp.py - w górę po jednej i z odczekaniem, w dół od razu
from fakes import build, feed, run, simulate, check, done

BIG_SURPLUS = dict(soc=95, pv=1500, battery=-6500)   # bilans na 3 grzałki

# --- nawet gdy bilans pozwala na trzy, wchodzimy po jednej ---
clock, pub, params, relay, ctrl = build()
feed(params, **BIG_SURPLUS)
ctrl.tick()
check("bilans mówi 3 grzałki", ctrl.level == 3)
check("ale włączamy jedną", relay.requested_count() == 1)

run(clock, params, relay, ctrl, 100, **BIG_SURPLUS)
check("po 100 s czekamy na stabilizację średniej", relay.requested_count() == 1)
run(clock, params, relay, ctrl, 140, **BIG_SURPLUS)
check("po 240 s czekamy już tylko na odczekanie", relay.requested_count() == 1
      and "dokładam po jednej" in ctrl.reason)
run(clock, params, relay, ctrl, 80, **BIG_SURPLUS)
check("po ~5 min druga grzałka", relay.requested_count() == 2)
run(clock, params, relay, ctrl, 320, **BIG_SURPLUS)
check("po kolejnych ~5 min trzecia", relay.requested_count() == 3)
run(clock, params, relay, ctrl, 320, **BIG_SURPLUS)
check("i na tym koniec - tyle grzałek jest", relay.requested_count() == 3)

# --- w dół o tyle, ile trzeba, w jednym ticku ---
feed(params, soc=95, pv=1500, battery=2000, samples=6)   # bilans już tylko na jedną
ctrl.tick()
check("spadek nadwyżki zdejmuje od razu dwie grzałki", relay.requested_count() == 1)

# --- odczekanie na dokładanie nie blokuje zdejmowania ---
clock, pub, params, relay, ctrl = build()
feed(params, **BIG_SURPLUS)
ctrl.tick()                                            # dołożona pierwsza grzałka
run(clock, params, relay, ctrl, 140, **BIG_SURPLUS)     # przeczekujemy stabilizację średniej
check("odczekanie na kolejne dołożenie wciąż biegnie", ctrl._ramp_up_wait() > 0)
feed(params, soc=95, pv=1500, battery=0, samples=6)     # nadwyżka znikła
ctrl.tick()
check("mimo biegnącego odczekania grzałka zeszła", relay.requested_count() == 0)

# --- odczekanie liczy się od ostatniego dołożenia, nie od ostatniej zmiany ---
clock, pub, params, relay, ctrl = build()
feed(params, **BIG_SURPLUS)
ctrl.tick()
check("pierwsza grzałka bez czekania (nic wcześniej nie dokładaliśmy)",
      relay.requested_count() == 1 and ctrl._ramp_up_wait() == 300.0)
clock.advance(150.0)
check("po 150 s zostało 150 s", ctrl._ramp_up_wait() == 150.0)
clock.advance(150.0)
check("po 300 s odczekanie minęło", ctrl._ramp_up_wait() == 0.0)

# --- pełna doba w miniaturze: stabilne 3 kW nadwyżki to stale 1 grzałka ---
# Dokładnie to, co ma nie migotać: przy 3 kW z dachu i lekkim domu chodzi jedna
# grzałka, a nie raz trzy, raz zero.
clock, pub, params, relay, ctrl = build()
simulate(clock, params, relay, ctrl, 3600, house=(200, 200, 200), pv=3000, soc=95)
check("3 kW słońca, 0,6 kW domu -> jedna grzałka", relay.requested_count() == 1)
switches = [m for m in pub.messages if m[1].get("cmd") == "off"]
check("i żadnego zdejmowania przez godzinę", switches == [])

done()
