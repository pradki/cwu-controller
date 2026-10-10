# tests/test_seasonal.py - przełączanie reguł zima/lato (CWUController._apply_season)
from fakes import build, feed, check, done

WINTER_RULES = [(40, 6000)]


class FakeMonth:
    """Miesiąc pod kontrolą testu, zamiast prawdziwego zegara kalendarzowego."""
    def __init__(self, month):
        self.month = month

    def __call__(self):
        return self.month


# --- domyślnie (bez podania winter_*) zima == lato, nikt nie dostaje niespodzianki ---
month = FakeMonth(12)  # grudzień
clock, pub, params, relay, ctrl = build(month_fn=month)
check("bez winter_* grudzień ma te same reguły co domyślne lato",
      ctrl.rules == ctrl._summer["rules"])
check("i jest oznaczony jako zima (miesiąc się zgadza)", ctrl.season == "winter")

# --- z podanymi winter_rules: lato i zima różnią się ---
month = FakeMonth(6)  # czerwiec - lato
clock, pub, params, relay, ctrl = build(
    month_fn=month,
    winter_rules=WINTER_RULES,
    winter_soc_hysteresis_on=99,
    winter_soc_hysteresis_off=98,
    winter_pv_hysteresis_on=200,
    winter_soc_full=99,
)
check("czerwiec -> lato", ctrl.season == "summer")
check("czerwiec -> reguły letnie (domyślne)", ctrl.rules == ctrl._summer["rules"])
check("czerwiec -> histereza letnia", ctrl.soc_hysteresis_on == 95
      and ctrl.pv_hysteresis_on == 800)

month.month = 1  # styczeń - zima
ctrl.tick()
check("styczeń -> zima", ctrl.season == "winter")
check("styczeń -> reguły zimowe", ctrl.rules == WINTER_RULES)
check("styczeń -> histereza zimowa podniesiona", ctrl.soc_hysteresis_on == 99
      and ctrl.soc_hysteresis_off == 98 and ctrl.pv_hysteresis_on == 200)

# --- przejście w drugą stronę, w trakcie działania, bez restartu usługi ---
month.month = 3  # marzec - znów lato
ctrl.tick()
check("marzec -> z powrotem lato, bez restartu", ctrl.season == "summer"
      and ctrl.rules == ctrl._summer["rules"])

# --- granice miesięcy zgodnie z "marzec-wrzesień lato, październik-luty zima" ---
clock, pub, params, relay, ctrl = build(month_fn=FakeMonth(9), winter_rules=WINTER_RULES)
check("wrzesień -> jeszcze lato", ctrl.season == "summer")
clock, pub, params, relay, ctrl = build(month_fn=FakeMonth(10), winter_rules=WINTER_RULES)
check("październik -> już zima", ctrl.season == "winter")
clock, pub, params, relay, ctrl = build(month_fn=FakeMonth(2), winter_rules=WINTER_RULES)
check("luty -> wciąż zima", ctrl.season == "winter")
clock, pub, params, relay, ctrl = build(month_fn=FakeMonth(3), winter_rules=WINTER_RULES)
check("marzec -> znów lato", ctrl.season == "summer")

# --- zimowa reguła (40, 6000) rzeczywiście wymaga dużej nadwyżki ---
# Każdy przypadek na świeżym build() - inaczej poprzednia próbka zostaje w
# długim oknie (maxlen=9 > domyślne feed(samples=6)) i zafałszuje średnią.
clock, pub, params, relay, ctrl = build(month_fn=FakeMonth(12), winter_rules=WINTER_RULES)
feed(params, soc=50, pv=4000, battery=-3000)
check("zima: SOC 50%, nadwyżka 3 kW (< progu 6 kW) -> 0 grzałek",
      ctrl._desired_level() == 0)

clock, pub, params, relay, ctrl = build(month_fn=FakeMonth(12), winter_rules=WINTER_RULES)
feed(params, soc=50, pv=7000, battery=-9000)
check("zima: nadwyżka > 6 kW -> grzejemy", ctrl._desired_level() > 0)

clock, pub, params, relay, ctrl = build(month_fn=FakeMonth(12), winter_rules=WINTER_RULES)
feed(params, soc=35, pv=7000, battery=-9000)
check("zima: SOC poniżej progu 40% -> wciąż 0, mimo dużej nadwyżki",
      ctrl._desired_level() == 0)

# --- histereza "prawie pełnej baterii" podniesiona do SOC_FULL w zimie: w
# praktyce nieosiągalna, bo gałąź "bateria pełna" przejmuje pierwsza ---
clock, pub, params, relay, ctrl = build(
    month_fn=FakeMonth(12), winter_rules=WINTER_RULES,
    winter_soc_hysteresis_on=99, winter_soc_full=99,
)
feed(params, soc=99.5, pv=1000, battery=0)
check("zima: SOC > SOC_FULL trafia do ścieżki 'bateria pełna', nie do histerezy",
      ctrl._desired_level() >= 0 and "pełn" in ctrl.reason)

done()
