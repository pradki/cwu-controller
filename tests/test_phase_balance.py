# tests/test_phase_balance.py - wybór grzałki po obciążeniu faz
from fakes import build, feed, check, done

# --- włączamy najmniej obciążoną fazę ---
clock, pub, params, relay, ctrl = build()
feed(params, soc=85, pv=1500, battery=-4200, loads=(3000, 1000, 2000))
check("net = surowe średnie, gdy nic nie grzeje", ctrl.phase_net_load() == [3000.0, 1000.0, 2000.0])
check("jedna grzałka -> faza najmniej obciążona (L2)", ctrl._pick_on(1) == [1])
check("dwie grzałki -> L2, potem L3", ctrl._pick_on(2) == [1, 2])

# --- wyłączamy najbardziej obciążoną fazę ---
clock, pub, params, relay, ctrl = build()
relay.request(0, True)
relay.request(1, True)
feed(params, soc=85, pv=1500, battery=-1000, loads=(2500, 3500, 900))
check("net odejmuje własne grzałki z L1 i L2", ctrl.phase_net_load() == [500.0, 1500.0, 900.0])
check("wyłączamy z najbardziej obciążonej fazy (L2)", ctrl._pick_off(1) == [1])

# --- remis rozstrzyga niższy indeks (stabilnie, bez migotania) ---
clock, pub, params, relay, ctrl = build()
feed(params, soc=85, pv=1500, battery=-4200, loads=(1000, 1000, 1000))
check("remis -> najniższy indeks", ctrl._pick_on(1) == [0])

# --- brak danych o fazach nie blokuje grzania, tylko bilansowanie ---
clock, pub, params, relay, ctrl = build()
feed(params, soc=85, pv=1500, battery=-4200, loads=None)
check("bez load_power net jest zerowy", ctrl.phase_net_load() == [0.0, 0.0, 0.0])
check("bez load_power wybieramy po kolei", ctrl._pick_on(2) == [0, 1])
check("load_valid mówi, że bilansu faz nie ma", params.load_valid is False)

# --- dogasająca grzałka: jej moc siedzi jeszcze w pomiarze fazy ---
# To jedyny moment, w którym odejmowanie własnych grzałek zmienia wynik: kandydat
# do włączenia (już zwolniony, ale przekaźnik jeszcze trzyma) nie dołoży fazie
# ani wata, więc jest tańszy niż faza formalnie mniej obciążona.
clock, pub, params, relay, ctrl = build()
relay.request(0, True)
relay.request(0, False)   # zwolniona, ale impuls jeszcze odlicza
feed(params, soc=85, pv=1500, battery=-4200, loads=(2600, 800, 900))
check("grzałka zwolniona wciąż jest zasilona", relay.energized(0) and not relay.requested[0])
check("net dyskontuje dogasającą grzałkę", ctrl.phase_net_load() == [600.0, 800.0, 900.0])
check("wolimy wrócić na L1 niż dokładać L2", ctrl._pick_on(1) == [0])

# --- pełny tick: sterownik faktycznie zapala grzałkę z najlżejszej fazy ---
clock, pub, params, relay, ctrl = build()
# Dom lekki, żeby limit obciążenia nie wchodził w drogę - tu badamy wybór fazy.
feed(params, soc=85, pv=1500, battery=-4200, loads=(2000, 1500, 400))
pub.clear()
ctrl.tick()
check("tick -> poziom 1", ctrl.level == 1)
check("tick zapalił cwu_l3 (najlżejsza faza)", relay.requested == [False, False, True])
cmd = pub.last("cave/relay/relay_test/cwu_l3/cmd/cwu-controller")
check("komenda to sprzętowy flash_on z priorytetem",
      cmd is not None and cmd[1] == {"cmd": "flash_on", "duration": 60.0, "priority": 10})

done()
