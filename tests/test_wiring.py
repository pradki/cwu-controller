# tests/test_wiring.py - spięcie całej usługi na config.example.py, bez brokera.
# Testujemy szablon, a nie lokalny config.py (ten jest gitignorowany), więc brak
# nowego klucza w szablonie wychodzi tutaj, a nie na Pi po deployu.
import importlib.util
import sys
from pathlib import Path

from fakes import check, done

ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("config", ROOT / "config.example.py")
cfg = importlib.util.module_from_spec(spec)
sys.modules["config"] = cfg
spec.loader.exec_module(cfg)

import main  # noqa: E402  (dopiero po podstawieniu configu z szablonu)
from datetime import datetime  # noqa: E402

params, mqtt_client, relay, controller = main.build_service(cfg)

# Reguły zależą od pory roku (patrz cwu_controller.CWUController._apply_season) -
# sprawdzamy więc względem tego, co faktycznie powinno być aktywne teraz, a nie
# zawsze względem letniego RULES.
_expected_rules = (cfg.WINTER_RULES if datetime.now().month in cfg.WINTER_MONTHS
                   else cfg.RULES)

check("trzy grzałki z configu", relay.count == 3)
check("grzałki na osobnych fazach", [relay.phase(i) for i in range(3)] == [0, 1, 2])

check("komendy idą na topici gatewaya", [relay.cmd_topic(i) for i in range(3)] == [
    f"cave/relay/{cfg.RELAY_MODULE}/cwu_l1/cmd/cwu-controller",
    f"cave/relay/{cfg.RELAY_MODULE}/cwu_l2/cmd/cwu-controller",
    f"cave/relay/{cfg.RELAY_MODULE}/cwu_l3/cmd/cwu-controller",
])

check("subskrybujemy parametry falownika i czujniki", set(mqtt_client.param_topics) == {
    "cave/deye/params/battery_soc",
    "cave/deye/params/battery_power",
    "cave/deye/params/pv1_power",
    "cave/deye/params/pv2_power",
    "cave/deye/params/load_power_l1",
    "cave/deye/params/load_power_l2",
    "cave/deye/params/load_power_l3",
    "cave/sensors/temperature/buff_top_temp",
    "cave/sensors/temperature/buff_upmid_temp",
})
check("subskrybujemy statusy naszych kanałów",
      set(mqtt_client.json_topics) == set(relay.status_topics()))

# Handlery statusów muszą trafiać w swoją grzałkę - pętla domykająca `index`
# to klasyczne miejsce na pomyłkę (wszystkie lambdy z tym samym i).
for topic, index in relay.status_topics().items():
    mqtt_client.json_topics[topic]({"state": "on", "by": "test", "channel": index})
check("każdy handler statusu pisze do swojej grzałki",
      [r["channel"] for r in relay._reported] == [0, 1, 2])

check("reguły i progi wzięte z configu",
      controller.rules == _expected_rules and controller.max_temp == cfg.MAX_TEMP
      and controller.heater_power == cfg.HEATER_POWER)
check("okna średnich z configu", params.battery_power.window == cfg.AVERAGE_SAMPLES
      and params.battery_power.long_window == cfg.AVERAGE_SAMPLES_LONG)
check("nastawy ostrożnego dokładania z configu",
      controller.ramp_up_hold == cfg.RAMP_UP_HOLD
      and controller.full_soc_add_below == cfg.FULL_SOC_ADD_BELOW
      and controller.shed_discharge_above == cfg.SHED_DISCHARGE_ABOVE
      and controller.max_total_load == cfg.MAX_TOTAL_LOAD)
check("długie okno dłuższe od krótkiego", cfg.AVERAGE_SAMPLES_LONG > cfg.AVERAGE_SAMPLES)
check("między progiem dokładania i zdejmowania jest strefa martwa",
      cfg.FULL_SOC_ADD_BELOW < cfg.SHED_DISCHARGE_ABOVE)
check("limit obciążenia zostawia miejsce na choć jedną grzałkę",
      cfg.MAX_TOTAL_LOAD is None or cfg.MAX_TOTAL_LOAD > cfg.HEATER_POWER)
check("status sterownika na topicu z configu", controller.status_topic == cfg.STATUS_TOPIC)
check("watchdog decyzji dłuższy niż tick sterowania",
      cfg.DECISION_MAX_AGE > cfg.CONTROL_INTERVAL)
check("impuls dłuższy niż odstęp między pingami",
      cfg.FLASH_DURATION > cfg.PING_INTERVAL)

# Tick bez żadnych danych z brokera nie może nic włączyć.
controller.tick()
check("na starcie, bez danych, nie grzejemy",
      relay.requested_count() == 0 and controller.level == 0)

done()
