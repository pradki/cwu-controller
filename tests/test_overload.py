# tests/test_overload.py - zabezpieczenie przed przeciążeniem
# `simulate` liczy obciążenie faz i moc baterii z tego, co sterownik faktycznie
# załączył, więc testowana jest cała pętla zwrotna: grzałka dokłada 2 kW swojej fazie,
# bateria pokrywa to, czego nie pokryje słońce, a sterownik na to reaguje.
from fakes import build, feed, run, simulate, check, done

# ================= scenariusz z życia =================
# Piekarnik, hydrofor i reszta domu ciągną 6 kW. Bateria zdążyła się naładować, więc
# sterownik dokłada grzałki. Przychodzi chmura: produkcja siada, bateria wchodzi na swoje
# maksimum, falownik zaczyna dobierać z sieci. Grzałki muszą zejść od razu, nie po dwóch
# minutach uśredniania - i nie mogą wrócić po pierwszym przejaśnieniu.
clock, pub, params, relay, ctrl = build()
simulate(clock, params, relay, ctrl, 1800, house=(2000, 2000, 2000), pv=7000, soc=100)

check("6 kW domu + 7 kW słońca -> jedna grzałka, nie trzy", relay.requested_count() == 1)
check("obciążenie zostało pod mocą falownika",
      params.load_total_now <= ctrl.max_total_load)

# Chmura.
simulate(clock, params, relay, ctrl, 20, house=(2000, 2000, 2000), pv=500, soc=100)
check("chmura -> grzałka zdjęta w tym samym ticku", relay.requested_count() == 0)
check("powód mówi o oddawaniu baterii", "bateria oddaje" in ctrl.reason)

simulate(clock, params, relay, ctrl, 40, house=(2000, 2000, 2000), pv=7000, soc=100)
check("przejaśnienie nie wraca grzałką natychmiast", relay.requested_count() == 0)

# ================= pełna drabinka przy lekkim domu =================
clock, pub, params, relay, ctrl = build()
simulate(clock, params, relay, ctrl, 700, house=(300, 300, 300), pv=8000, soc=95)
check("mały dom i 8 kW słońca -> dochodzimy do trzech grzałek",
      relay.requested_count() == 3)

# Chmura przy trzech grzałkach: schodzimy po jednej na tick, bez czekania na średnią.
feed(params, soc=95, pv=500, battery=8000, loads=(2300, 2300, 2300), samples=0)
for expected in (2, 1, 0):
    ctrl.tick()
    check(f"zdejmujemy po jednej -> {expected}", relay.requested_count() == expected)
check("zdejmowanie nie czekało na stabilizację średniej", ctrl._settle > 0)

# ================= twardy limit obciążenia =================
# Sam dom przekracza moc falownika: nie ma z czego dokładać, a co jest - zdejmujemy.
clock, pub, params, relay, ctrl = build()
simulate(clock, params, relay, ctrl, 400, house=(300, 300, 300), pv=8000, soc=95)
przed = relay.requested_count()
check("na start jest co zdejmować", przed >= 1)
feed(params, soc=95, pv=8000, battery=-6500, loads=(4000, 3000, 2000), samples=0)
ctrl.tick()
check("suma faz 9 kW > 8 kW -> zdejmujemy grzałkę mimo ładowania baterii",
      relay.requested_count() == przed - 1)
check("powód mówi o obciążeniu", "obciążenie" in ctrl.reason)

# --- brak zapasu mocy blokuje dokładanie, choć nadwyżka by na nie pozwoliła ---
# Panele przewymiarowane względem falownika (12 kW z dachu), więc bilans mocy mówi
# "dwie grzałki", ale dom sam ciągnie 6,9 kW i nie ma ich gdzie wcisnąć.
clock, pub, params, relay, ctrl = build()
simulate(clock, params, relay, ctrl, 600, house=(3000, 2000, 1900), pv=12000, soc=95)
check("bilans mocy sam dałby 2 grzałki", ctrl.level == 2)
check("6,9 kW domu + grzałka nie zmieści się w 8 kW -> nie dokładamy",
      relay.requested_count() == 0)
check("powód mówi o braku zapasu", "brak zapasu mocy" in ctrl.reason)

# --- ten sam bilans, ale lżejszy dom: grzałki wchodzą ---
clock, pub, params, relay, ctrl = build()
simulate(clock, params, relay, ctrl, 600, house=(1000, 900, 800), pv=9000, soc=95)
check("przy 2,7 kW domu grzałki wchodzą", relay.requested_count() >= 1)

# --- bez danych o fazach limit się nie włącza, ale bateria pilnuje dalej ---
clock, pub, params, relay, ctrl = build()
run(clock, params, relay, ctrl, 600, soc=95, pv=7000, battery=-6500)
przed = relay.requested_count()
check("brak load_power nie blokuje grzania", przed >= 1)
check("i nie ma z czego liczyć limitu", params.load_total_now is None)
feed(params, soc=95, pv=500, battery=8000, samples=0)
ctrl.tick()
check("bateria nadal pilnuje", relay.requested_count() == przed - 1)

done()
