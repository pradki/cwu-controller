# tests/test_levels.py - reguły nadwyżki: ile grzałek przy danym SOC/PV/bilansie
from fakes import build, feed, check, done

# --- noc: brak SOC i brak produkcji ---
clock, pub, params, relay, ctrl = build()
feed(params, soc=40, pv=0, battery=300)
check("noc (SOC 40%, PV 0) -> 0 grzałek", ctrl._desired_level() == 0)

# --- typowy słoneczny dzień, bateria się ładuje 4,2 kW ---
clock, pub, params, relay, ctrl = build()
feed(params, soc=85, pv=1500, battery=-4200)
# reguła (80, 1000): (-4200 + 1000) / -2000 = 1.6 -> 1
check("SOC 85%, PV 1,5 kW, ładowanie 4,2 kW -> 1 grzałka", ctrl._desired_level() == 1)

# --- ta sama produkcja, dużo większa nadwyżka ---
clock, pub, params, relay, ctrl = build()
feed(params, soc=95, pv=1500, battery=-6500)
# reguła (90, 400): (-6500 + 400) / -2000 = 3.05 -> 3
check("SOC 95%, ładowanie 6,5 kW -> 3 grzałki", ctrl._desired_level() == 3)

# --- ogromna nadwyżka nie może dać więcej grzałek niż mamy ---
clock, pub, params, relay, ctrl = build()
feed(params, soc=85, pv=1500, battery=-20000)
check("bilans 20 kW -> obcięte do liczby grzałek", ctrl._desired_level() == 3)

# (pełny SOC ma własną ścieżkę - patrz test_full_soc.py)

# --- produkcja za mała dla tego SOC: żadna reguła nie przechodzi ---
clock, pub, params, relay, ctrl = build()
feed(params, soc=85, pv=500, battery=-6000)
check("SOC 85%, PV tylko 0,5 kW -> 0 grzałek", ctrl._desired_level() == 0)
check("powód mówi o braku nadwyżki", "brak nadwyżki" in ctrl.reason)

# --- moc pracujących grzałek musi być odjęta od bilansu ---
clock, pub, params, relay, ctrl = build()
feed(params, soc=85, pv=1500, battery=-200)
check("bilans -200 W bez grzałek -> 0", ctrl._desired_level() == 0)
relay.request(0, True)
relay.request(1, True)
# available = -200 - 2*2000 = -4200 -> (-4200 + 1000) / -2000 = 1.6 -> 1
check("ten sam pomiar przy 2 pracujących grzałkach -> 1 (jedna do wyłączenia)",
      ctrl._desired_level() == 1)

# --- histereza przy prawie pełnej baterii ---
clock, pub, params, relay, ctrl = build()
for i in range(3):
    relay.request(i, True)          # 3 grzałki pracują, bateria oddaje 900 W
feed(params, soc=96, pv=700, battery=900)
# available = 900 - 6000 = -5100; reguła (90, 400): (-5100 + 400) / -2000 = 2.35 -> 2
check("SOC 96% ale PV 0,7 kW -> histereza nieuzbrojona, poziom 2",
      ctrl._desired_level() == 2 and ctrl.hysteresis_active is False)

feed(params, soc=96, pv=1000, battery=900)
# PV > 800 przy SOC > 95 -> histereza; reguła (94, -1000): (-5100 - 1000) / -2000 = 3.05 -> 3
check("SOC 96% i PV 1 kW -> histereza uzbrojona, poziom 3",
      ctrl._desired_level() == 3 and ctrl.hysteresis_active is True)

feed(params, soc=95, pv=500, battery=900)
check("SOC spadł do 95%, PV do 0,5 kW -> histereza trzyma, wciąż 3",
      ctrl._desired_level() == 3 and ctrl.hysteresis_active is True)

feed(params, soc=94, pv=500, battery=900)
check("SOC 94% -> histereza gaśnie, poziom wraca do 2",
      ctrl._desired_level() == 2 and ctrl.hysteresis_active is False)

# --- długa średnia (3 min) ogranicza to, co pokazuje krótka ---
clock, pub, params, relay, ctrl = build()
feed(params, soc=85, pv=1500, battery=-1000, samples=9)   # 3 min: nadwyżka mała
feed(params, soc=85, pv=1500, battery=-6500, samples=3)   # ostatnia minuta: chwilowo duża
check("krótka średnia sama dałaby 2 grzałki", ctrl._level_from_rules(
    85, 1500, params.battery_power.average, 0, ctrl.rules)[0] == 2)
check("długa średnia ścina poziom do 0", ctrl._desired_level() == 0)
check("powód mówi, co ograniczyło", "3 min ogranicza" in ctrl.reason)

# W drugą stronę ograniczenie nie działa: gdy długa średnia jest bogatsza niż
# krótka, obowiązuje krótka - schodzenie ma być szybkie.
clock, pub, params, relay, ctrl = build()
feed(params, soc=85, pv=1500, battery=-6500, samples=9)
feed(params, soc=85, pv=1500, battery=-1000, samples=3)
check("spadek nadwyżki widać od razu, długa średnia go nie maskuje",
      ctrl._desired_level() == 0)

done()
