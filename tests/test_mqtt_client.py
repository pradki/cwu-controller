# tests/test_mqtt_client.py - parsowanie ładunków i trasowanie wiadomości (bez brokera)
from fakes import FakeClock, check, done

from params import CWUParams
from mqtt_client import MQTTClient, parse_value


class FakeMessage:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload if isinstance(payload, bytes) else payload.encode("utf-8")


# --- parse_value ---
check("ładunek deye-modbus-mqtt",
      parse_value(b'{"value": -400, "unit": "W", "reg": 590, "uptime": 8461}') == -400.0)
check("ładunek czujnika", parse_value(b'{"value": 64.12, "timestamp": 2765931}') == 64.12)
check("goła liczba też przechodzi", parse_value(b"123.4") == 123.4)
check("brak klucza value -> None", parse_value(b'{"unit": "W"}') is None)
check("pusty ładunek -> None", parse_value(b"   ") is None)

# --- trasowanie ---
clock = FakeClock()
params = CWUParams(phases=3, samples=6, param_ttl=120.0, sensor_ttl=300.0, clock=clock)
statuses = []
client = MQTTClient("localhost", 1883, "cwu-controller-test",
                    param_topics=params.topic_map("cave/deye/params", "cave/sensors/temperature"),
                    json_topics={"cave/relay/relay_test/cwu_l1/status": statuses.append})

client._on_message(None, None, FakeMessage("cave/deye/params/battery_soc", '{"value": 88}'))
check("battery_soc trafia do Parama", params.battery_soc.value == 88.0)

client._on_message(None, None, FakeMessage("cave/deye/params/load_power_l2",
                                           '{"value": 1234, "unit": "W"}'))
check("load_power_l2 trafia na właściwą fazę",
      params.load_power[1].value == 1234.0 and params.load_power[0].valid is False)

client._on_message(None, None, FakeMessage("cave/sensors/temperature/buff_top_temp",
                                           '{"value": 61.5}'))
check("temperatura buforu trafia do Parama", params.buff_top_temp.value == 61.5)

client._on_message(None, None, FakeMessage("cave/relay/relay_test/cwu_l1/status",
                                           '{"state": "flash_on", "by": "cwu-controller"}'))
check("status kanału idzie do handlera jako cały JSON",
      statuses == [{"state": "flash_on", "by": "cwu-controller"}])

# Śmieci nie mogą wywalić wątku MQTT ani podmienić dobrej wartości na złą.
client._on_message(None, None, FakeMessage("cave/deye/params/battery_soc", "nie-liczba"))
check("nieparsowalny ładunek nie zmienia wartości", params.battery_soc.value == 88.0)
client._on_message(None, None, FakeMessage("cave/relay/relay_test/cwu_l1/status", "{nie-json"))
check("zepsuty JSON statusu nie woła handlera", len(statuses) == 1)
client._on_message(None, None, FakeMessage("cave/cos/zupelnie/innego", '{"value": 1}'))
check("obcy topic ignorowany", params.battery_soc.value == 88.0)

# --- subskrypcje ---
subscribed = []


class FakeClient:
    def subscribe(self, topic):
        subscribed.append(topic)


client._on_connect(FakeClient(), None, None, 0)
check("po połączeniu subskrybujemy wszystkie topici wejściowe", len(subscribed) == 10)
check("wśród nich status kanału z gatewaya",
      "cave/relay/relay_test/cwu_l1/status" in subscribed)
check("policzony connect", client.get_stats()["mqtt_connects"] == 1)

done()
