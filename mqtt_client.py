# mqtt_client.py
import json
import logging
import threading

import paho.mqtt.client as mqtt

LOG = logging.getLogger("mqtt")


def parse_value(payload):
    """Wyciąga liczbę z ładunku. deye-modbus-mqtt i czujniki publikują
    `{"value": ..., "unit": ...}`, ale goły `123.4` na topicu też przyjmujemy -
    kosztuje trzy linijki, a oszczędza debugowania przy ręcznym mosquitto_pub."""
    text = payload.decode("utf-8", errors="ignore").strip() if isinstance(payload, (bytes, bytearray)) else str(payload).strip()
    if not text:
        return None
    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        if isinstance(data, dict):
            value = data.get("value")
            return None if value is None else float(value)
        return None
    return float(text)


class MQTTClient:
    """Wątek paho (wzorzec jak w waveshare-mqtt-gateway/mqtt_gateway.py).

    Wartości z brokera wędrują do Paramów przez `call_soon_threadsafe`, więc
    pętla sterowania nigdy nie czyta parametru w połowie aktualizacji i nie
    trzeba do tego żadnych blokad.
    """

    def __init__(self, host, port, client_id, param_topics=None, json_topics=None):
        self.host = host
        self.port = port
        self.loop = None
        # topic -> Param (wartość liczbowa)
        self.param_topics = dict(param_topics or {})
        # topic -> callable(dict) (cały ładunek, np. status kanału z gatewaya)
        self.json_topics = dict(json_topics or {})

        self.cnt_connects = 0
        self.cnt_disconnects = 0

        self.client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv311,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        self.client.on_disconnect = self._on_disconnect

        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)

    def bind_loop(self, loop):
        self.loop = loop

    def get_stats(self):
        return {
            "mqtt_connects": self.cnt_connects,
            "mqtt_disconnects": self.cnt_disconnects,
            "connected": self.client.is_connected(),
        }

    # ---------------- callbacks (wątek MQTT) ----------------
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.cnt_connects += 1
            for topic in list(self.param_topics) + list(self.json_topics):
                client.subscribe(topic)
            LOG.info("Connected (#%d), subscribed %d topics",
                     self.cnt_connects, len(self.param_topics) + len(self.json_topics))
        else:
            LOG.warning("Connect failed rc=%s", rc)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties=None):
        self.cnt_disconnects += 1
        LOG.warning("Disconnected (#%d) rc=%s", self.cnt_disconnects, reason_code)

    def _on_message(self, client, userdata, message, properties=None):
        topic = message.topic
        param = self.param_topics.get(topic)
        if param is not None:
            try:
                value = parse_value(message.payload)
            except Exception:
                LOG.warning("Nieparsowalny ładunek na %s: %r", topic, message.payload)
                return
            if value is None:
                return
            self._dispatch(param.update, value)
            return

        handler = self.json_topics.get(topic)
        if handler is not None:
            try:
                data = json.loads(message.payload.decode("utf-8", errors="ignore"))
            except Exception:
                LOG.warning("Niepoprawny JSON na %s: %r", topic, message.payload)
                return
            if isinstance(data, dict):
                self._dispatch(handler, data)
            return

        LOG.debug("Nieoczekiwany topic: %s", topic)

    def _dispatch(self, fn, arg):
        if self.loop is None:
            fn(arg)  # przed startem event loopa (albo w testach) - wprost
            return
        self.loop.call_soon_threadsafe(fn, arg)

    # ---------------- publikacja ----------------
    def publish(self, topic, payload, retain=False):
        try:
            self.client.publish(topic, json.dumps(payload), retain=retain)
        except Exception:
            LOG.exception("Publish error na %s", topic)

    # ---------------- lifecycle ----------------
    def start(self):
        if not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        try:
            self.client.disconnect()
            self.client.loop_stop()
        except Exception:
            pass

    def _run_loop(self):
        LOG.info("Connecting to MQTT %s:%s", self.host, self.port)
        self.client.connect(self.host, self.port, keepalive=30)
        try:
            self.client.loop_forever(retry_first_connection=True)
        except Exception:
            LOG.exception("MQTT loop error")
