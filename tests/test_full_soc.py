# tests/test_full_soc.py - pełna bateria: dokładanie metodą prób, po jednej grzałce
from fakes import build, feed, run, check, done

# Pełna bateria przestaje się ładować, a nadwyżkę falownik ucina - bilans mocy nic
# już nie mówi, więc jedyne wyjście to próbować: dołóż grzałkę, sprawdź, czy bateria
# zaczyna oddawać, i tylko jeśli nie - dołóż następną.

SUN = dict(soc=100, pv=6000, battery=0)

clock, pub, params, relay, ctrl = build()
feed(params, **SUN, loads=(500, 400, 300))

ctrl.tick()
check("pełny SOC, bateria nie oddaje -> pierwsza grzałka", relay.requested_count() == 1)
check("powód mówi, że to próba", "próbuję dołożyć" in ctrl.reason)

# Grzałka pracuje, bateria wciąż nie oddaje (PV ją pokrywa) - ale dokładamy dopiero
# po odczekaniu, nie w następnym ticku.
run(clock, params, relay, ctrl, 100, **dict(SUN, loads=(2500, 400, 300)))
check("po 100 s wciąż jedna grzałka", relay.requested_count() == 1)
check("najpierw czekamy, aż średnia obejmie nową grzałkę", "stabilizacj" in ctrl.reason)

run(clock, params, relay, ctrl, 140, **dict(SUN, loads=(2500, 400, 300)))
check("po 240 s wciąż jedna grzałka", relay.requested_count() == 1)
check("teraz trzyma nas już tylko odczekanie", "dokładam po jednej" in ctrl.reason)

run(clock, params, relay, ctrl, 80, **dict(SUN, loads=(2500, 400, 300)))
check("po ~5 min i sprawdzeniu -> druga grzałka", relay.requested_count() == 2)

run(clock, params, relay, ctrl, 320, **dict(SUN, loads=(2500, 2400, 300)))
check("i tak dalej -> trzecia grzałka", relay.requested_count() == 3)

run(clock, params, relay, ctrl, 320, **dict(SUN, loads=(2500, 2400, 2300)))
check("więcej grzałek nie ma, poziom stoi", relay.requested_count() == 3)

# --- bateria zaczyna oddawać: schodzimy po jednej, bez czekania ---
feed(params, soc=100, pv=1000, battery=5000, loads=(2500, 2400, 2300), samples=0)
ctrl.tick()
check("bateria oddaje 5 kW -> zdejmujemy grzałkę", relay.requested_count() == 2)
check("powód mówi o oddawaniu", "bateria oddaje" in ctrl.reason)

ctrl.tick()
check("5 kW - 2 kW dogasające = 3 kW, wciąż za dużo -> zdejmujemy",
      relay.requested_count() == 1)
ctrl.tick()
check("5 kW - 4 kW dogasające = 1 kW, poniżej progu -> zostawiamy",
      relay.requested_count() == 1)

# --- strefa martwa: nie dokładamy, ale i nie zdejmujemy ---
clock, pub, params, relay, ctrl = build()
feed(params, **SUN)
ctrl.tick()
check("start: jedna grzałka", relay.requested_count() == 1)
run(clock, params, relay, ctrl, 320, soc=100, pv=3000, battery=1000)  # między 200 W a 2000 W
check("bateria oddaje 1 kW - trzymamy to, co jest", relay.requested_count() == 1)
check("powód mówi, ile trzymamy", "trzymam" in ctrl.reason)

# --- długa średnia też musi się zgodzić, nie tylko krótka ---
clock, pub, params, relay, ctrl = build()
feed(params, soc=100, pv=6000, battery=1500, samples=45)  # 15 min: bateria oddawała
feed(params, soc=100, pv=6000, battery=0, samples=6)      # ostatnie 2 min: przestała
check("długa średnia jeszcze pamięta rozładowanie -> nie dokładamy",
      ctrl._desired_level() == 0)

done()
