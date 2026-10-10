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
FLASH_DURATION = 60.0     # s, czas impulsu; po tylu sekundach bez pingu kanał gaśnie sam
AVERAGE_SAMPLES = 6       # 6 x CONTROL_INTERVAL = 2 min - "co jest teraz"
AVERAGE_SAMPLES_LONG = 45  # 45 x CONTROL_INTERVAL = 15 min - "ile da się utrzymać"
SETTLE_TICKS = 6          # ile ticków po przełączeniu grzałki nie liczymy poziomu z reguł

# Dokładamy najwyżej jedną grzałkę na tyle sekund. Wchodzenie po jednej i z odczekaniem
# jest po to, żeby przy 3 kW średniej nadwyżki chodziły stale 1-2 grzałki, a nie raz trzy,
# raz zero. Zdejmowanie awaryjne tego nie dotyczy - w dół schodzimy natychmiast.
RAMP_UP_HOLD = 300.0

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
# trzymamy to aż SOC spadnie do SOC_HYSTERESIS_OFF (inaczej grzałki by pulsowały
# wokół 95%).
HYSTERESIS_RULE = (94, -1000)
SOC_HYSTERESIS_ON = 95
SOC_HYSTERESIS_OFF = 94
PV_HYSTERESIS_ON = 800

# Powyżej tego SOC bilans mocy przestaje cokolwiek mówić: pełna bateria po prostu nie
# ładuje się dalej, a nadwyżkę falownik ucina. Sterownik przechodzi wtedy na dokładanie
# metodą prób - patrz FULL_SOC_ADD_BELOW i SHED_DISCHARGE_ABOVE.
SOC_FULL = 99
