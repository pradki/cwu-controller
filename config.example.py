# config.example.py - skopiuj do config.py i popraw adresy pod własną sieć.
import platform

# === MQTT ===
if platform.system() == "Linux":
    MQTT_BROKER_HOST = "localhost"
else:
    MQTT_BROKER_HOST = "192.168.1.10"

MQTT_BROKER_PORT = 1883
MQTT_CLIENT_ID = "cwu-controller"

# Nazwa usługi w topicach komend gatewaya - widoczna jako "by" w statusie kanału,
# i to po niej gateway rozpoznaje, że kolejny flash_on to nasz keep-alive, a nie
# nowe żądanie od kogoś innego.
SERVICE_NAME = "cwu-controller"

# === Wejścia: deye-modbus-mqtt (retained, publikacja co 20 s) ===
DEYE_TOPIC_ROOT = "cave/deye/params"

# === Wejścia: czujniki temperatury buforu ===
SENSOR_TOPIC_ROOT = "cave/sensors/temperature"
BUFF_TOP_SENSOR = "buff_top_temp"
BUFF_UPMID_SENSOR = "buff_upmid_temp"

# === Wyjście: waveshare-mqtt-gateway ===
RELAY_TOPIC_ROOT = "cave/relay"
RELAY_MODULE = "relay_heating"

# === Wyjście: własny status sterownika (retained) ===
STATUS_TOPIC = "cave/cwu/status"

# === Grzałki ===
# name  - alias kanału z config.py gatewaya (w module RELAY_MODULE)
# phase - indeks fazy 0/1/2 (L1/L2/L3), z której grzałka pobiera moc; decyduje,
#         które load_power_lN sterownik bierze do bilansu obciążenia faz
HEATERS = [
    {"name": "cwu_l1", "phase": 0},
    {"name": "cwu_l2", "phase": 1},
    {"name": "cwu_l3", "phase": 2},
]
HEATER_POWER = 2000  # W, moc jednej grzałki

# === Czasy ===
CONTROL_INTERVAL = 20.0   # s, deye publikuje co 20 s - częściej nie ma czego liczyć
PING_INTERVAL = 20.0      # s, odnawianie sprzętowego impulsu flash_on
FLASH_DURATION = 120.0    # s, czas impulsu; po tylu sekundach bez pingu kanał gaśnie sam.
                          # 2 min, żeby nawet błyskawiczne zdjęcie żądania (patrz MIN_ON_TIME)
                          # nie było krótsze niż sprzętowy impuls potrafi utrzymać przekaźnik.
AVERAGE_SAMPLES = 3       # 3 x CONTROL_INTERVAL = 1 min - "co jest teraz"
AVERAGE_SAMPLES_LONG = 9  # 9 x CONTROL_INTERVAL = 3 min - "ile da się utrzymać"
SETTLE_TICKS = 3          # ile ticków po przełączeniu grzałki nie liczymy poziomu z reguł -
                          # dopasowane do AVERAGE_SAMPLES (1 min), żeby nie czekać dłużej niż
                          # krótka średnia potrzebuje na pełne obrócenie się po zmianie

# Dokładamy najwyżej jedną grzałkę na tyle sekund. Wchodzenie po jednej i z odczekaniem
# jest po to, żeby przy 3 kW średniej nadwyżki chodziły stale 1-2 grzałki, a nie raz trzy,
# raz zero. Zdejmowanie awaryjne tego nie dotyczy - w dół schodzimy natychmiast.
RAMP_UP_HOLD = 300.0

# Raz załączona grzałka pracuje co najmniej tyle sekund, zanim zwykłe (regułowe)
# zdjęcie może ją zgasić - inaczej PV balansujące tuż przy progu reguły (np. zimowe
# 6000 W) włącza i gasi grzałkę co tick, a przekaźnik "strzela". Zdejmowanie awaryjne
# (przeciążenie baterii/faz) tego nie dotyczy - to bezpiecznik, nie regulacja.
MIN_ON_TIME = 150.0

# Po tylu sekundach bez udanego ticku sterowania pętla pingująca przestaje odnawiać
# impulsy - zawieszone sterowanie ma zgasić grzałki, a nie zamrozić ostatnią decyzję.
DECISION_MAX_AGE = 3 * CONTROL_INTERVAL

PARAM_TTL = 120.0         # s bez danych z falownika = brak danych (nie: "zero")
SENSOR_TTL = 300.0        # s bez pomiaru temperatury = brak danych

# === Leasing kanałów w gatewayu ===
LEASE_PRIORITY = 10       # wyższy wygrywa arbitraż; 0 zostawiamy panelom/ręcznym komendom
LEASE_OFF_TTL = 120.0     # s - jak długo trzymamy jawne "off" po zwolnieniu grzałki

# === Bezpieczeństwo ===
MAX_TEMP = 85.0           # °C - powyżej (albo przy braku pomiaru) wszystkie grzałki off

# Bateria oddaje więcej niż tyle watów -> natychmiast zdejmujemy jedną grzałkę (co tick,
# aż zejdzie poniżej). Domyślnie moc jednej grzałki: powyżej tego grzałki jadą już z
# akumulatora, a przy jego limicie mocy falownik zaczyna dobierać z sieci.
SHED_DISCHARGE_ABOVE = 2000

# Przy pełnej baterii dokładamy kolejną grzałkę tylko, gdy bateria praktycznie nie oddaje
# (średnia krótka i długa poniżej tej wartości). Szeroka strefa martwa między tym progiem
# a SHED_DISCHARGE_ABOVE jest celowa - inaczej sterownik dokładałby i zdejmował w kółko.
FULL_SOC_ADD_BELOW = 200

# Twardy limit obciążenia: suma faz powyżej tej wartości zdejmuje grzałkę, a dołożyć
# kolejną można tylko, gdy średnie obciążenie + moc grzałki się pod nim mieszczą.
# Ustaw na moc znamionową falownika. None wyłącza limit (np. gdy nie ma load_power).
MAX_TOTAL_LOAD = 8000

# === Reguły nadwyżki energii ===
# (progSOC, progPV): pierwsza reguła, dla której soc > progSOC i pv > progPV, wyznacza
# poziom: level = int((moc_bilansowa + progPV) / -HEATER_POWER), obcięty do 0..len(HEATERS).
# progPV pełni podwójną rolę: próg wejścia i rezerwa mocy pozostawiona bateriom.
RULES = [
    (90, 400),
    (80, 1000),
    (70, 2000),
    (60, 3000),
    (50, 4000),
    (40, 5000),
]

# Histereza dla prawie pełnej baterii: przy SOC > SOC_HYSTERESIS_ON i produkcji
# > PV_HYSTERESIS_ON dopuszczamy dogrzewanie kosztem MAX_DISCHARGE z baterii, i
# trzymamy to aż SOC spadnie do SOC_HYSTERESIS_OFF. Podniesione z 95/94 na 99/98 -
# priorytetem jest nierozładowywanie akumulatora (ma służyć jak najdłużej), nie
# wyciśnięcie ostatniego wata z PV tuż pod pełnym naładowaniem. OFF musi być
# niższe niż ON (pasmo histerezy) - samo podniesienie OFF bez ON zostawiłoby
# wyłączanie efektywnie przy ON, bo _update_hysteresis sprawdza ON jako drugi i
# nadpisuje OFF z powrotem na True, dopóki SOC > ON. Zostaje przy ścisłym ">" -
# w odróżnieniu od SOC_FULL niżej, tutaj to bez znaczenia (patrz tam), więc nie
# ma powodu zmieniać generycznego, konstruktorowego zachowania tego progu.
HYSTERESIS_RULE = (98, -1000)
SOC_HYSTERESIS_ON = 99
SOC_HYSTERESIS_OFF = 98
PV_HYSTERESIS_ON = 800

# Od tego SOC (włącznie) bilans mocy przestaje cokolwiek mówić: bateria zaczyna
# ograniczać przyjmowaną moc, więc nie da się już policzyć poziomu z nadwyżki -
# sterownik przechodzi na dokładanie metodą prób (patrz FULL_SOC_ADD_BELOW i
# SHED_DISCHARGE_ABOVE). >=, nie > - SOC z falownika to pełne procenty (99, 100,
# bez wartości pośrednich), więc samo ">" czekałoby na pewne 100% i przez cały
# ostatni punkt procentowy (99->100) nie próbowałoby w ogóle dogrzewać, mimo że
# PV mogło mieć spory zapas ponad to, co bateria jeszcze przyjmowała - metoda
# prób i tak cofa się sama (FULL_SOC_ADD_BELOW/SHED_DISCHARGE_ABOVE), jeśli przy
# 99% okaże się, że zapasu jednak nie ma.
SOC_FULL = 99

# === Sezon grzewczy (październik-luty): inny priorytet niż latem ===
# Marzec-wrzesień używa wyłącznie powyższych RULES/SOC_*/PV_* (HYSTERESIS_RULE
# też, patrz niżej - te same wartości co zima od czasu, gdy SOC_HYSTERESIS_ON
# zrównał się z SOC_FULL). Październik-luty priorytetem jest naładowanie
# akumulatora - grzejemy tylko przy naprawdę dużej nadwyżce.
#
# SOC_HYSTERESIS_ON == SOC_FULL (99) w obu sezonach, ale SOC_FULL sprawdzane jest
# przez >= (patrz wyżej), a SOC_HYSTERESIS_ON wciąż przez ścisłe > - więc gałąź
# "bateria pełna" (CWUController._desired_level) przejmuje kontrolę już przy SOC
# == 99, zanim histereza w ogóle zdążyłaby się włączyć (jej własny próg wymaga
# SOC > 99, czyli 100). To, którym operatorem porównuje się sama histereza, nie
# ma więc żadnego znaczenia przy tej konkretnej konfiguracji - `_level_from_rules`
# (i razem z nim HYSTERESIS_RULE) nie jest przy SOC_FULL == SOC_HYSTERESIS_ON
# osiągalne z żadnej strony. To nie czyni SOC_HYSTERESIS_ON/OFF/HYSTERESIS_RULE
# martwym kodem w ogóle - `CWUController` wspiera różne wartości obu progów
# (patrz domyślne `soc_hysteresis_on=95` w konstruktorze, niżej od `soc_full=99`),
# tylko akurat w tym configu, z oboma na 99, histereza nie ma już własnego
# zakresu do obsłużenia.
WINTER_MONTHS = {10, 11, 12, 1, 2}

WINTER_RULES = [
    (40, 6000),
]
WINTER_HYSTERESIS_RULE = HYSTERESIS_RULE
WINTER_SOC_HYSTERESIS_ON = 99
WINTER_SOC_HYSTERESIS_OFF = 98
WINTER_PV_HYSTERESIS_ON = 200
WINTER_SOC_FULL = 99
