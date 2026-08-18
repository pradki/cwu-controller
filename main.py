# main.py
import asyncio
import logging
import signal

import config
from params import CWUParams
from relay_client import RelayClient
from cwu_controller import CWUController
from mqtt_client import MQTTClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
LOG = logging.getLogger("main")


async def control_loop(controller, interval):
    """Pętla decyzyjna. Pierwszy tick dopiero po `interval` - retained wartości z
    brokera przychodzą tuż po połączeniu, ale niech średnie zdążą dostać próbkę."""
    while True:
        await asyncio.sleep(interval)
        try:
            controller.tick()
        except Exception:
            LOG.exception("Błąd w ticku sterowania")


async def ping_loop(controller, relay, interval, max_decision_age):
    """Odnawianie sprzętowych impulsów. Pingujemy tylko dopóki pętla decyzyjna
    żyje: gdyby ping działał w oderwaniu od niej, zawieszone sterowanie
    trzymałoby grzałki na ostatniej decyzji w nieskończoność - a to jest właśnie
    ten scenariusz, przed którym impuls ma chronić."""
    while True:
        await asyncio.sleep(interval)
        age = controller.decision_age()
        if age is None or age > max_decision_age:
            if relay.requested_count():
                LOG.error("Sterowanie nie odpowiada (wiek decyzji: %s s) - "
                          "przestaję odnawiać impulsy, grzałki zgasną", age)
            continue
        try:
            relay.ping()
        except Exception:
            LOG.exception("Błąd przy odnawianiu impulsów")


def build_service(cfg=config):
    """Składa całą usługę z konfiguracji, nie dotykając sieci - `main()` tylko
    startuje wątek MQTT i pętle. Rozdzielone, żeby dało się sprawdzić samo
    spięcie (topici, aliasy, kompletność configu) bez brokera."""
    params = CWUParams(
        phases=3,
        samples=cfg.AVERAGE_SAMPLES,
        long_samples=cfg.AVERAGE_SAMPLES_LONG,
        param_ttl=cfg.PARAM_TTL,
        sensor_ttl=cfg.SENSOR_TTL,
    )

    mqtt_client = MQTTClient(
        host=cfg.MQTT_BROKER_HOST,
        port=cfg.MQTT_BROKER_PORT,
        client_id=cfg.MQTT_CLIENT_ID,
        param_topics=params.topic_map(
            cfg.DEYE_TOPIC_ROOT,
            cfg.SENSOR_TOPIC_ROOT,
            top_sensor=cfg.BUFF_TOP_SENSOR,
            upmid_sensor=cfg.BUFF_UPMID_SENSOR,
        ),
    )

    relay = RelayClient(
        publish=mqtt_client.publish,
        heaters=cfg.HEATERS,
        topic_root=cfg.RELAY_TOPIC_ROOT,
        module=cfg.RELAY_MODULE,
        service=cfg.SERVICE_NAME,
        priority=cfg.LEASE_PRIORITY,
        flash_duration=cfg.FLASH_DURATION,
        off_ttl=cfg.LEASE_OFF_TTL,
    )

    # Retained statusy kanałów z gatewaya - tylko do weryfikacji naszego modelu
    # (patrz RelayClient.note_status), sterowanie z nich nie zależy.
    for topic, index in relay.status_topics().items():
        mqtt_client.json_topics[topic] = lambda payload, i=index: relay.note_status(i, payload)

    controller = CWUController(
        params, relay,
        heater_power=cfg.HEATER_POWER,
        rules=cfg.RULES,
        hysteresis_rule=cfg.HYSTERESIS_RULE,
        soc_hysteresis_on=cfg.SOC_HYSTERESIS_ON,
        soc_hysteresis_off=cfg.SOC_HYSTERESIS_OFF,
        pv_hysteresis_on=cfg.PV_HYSTERESIS_ON,
        soc_full=cfg.SOC_FULL,
        max_temp=cfg.MAX_TEMP,
        settle_ticks=cfg.SETTLE_TICKS,
        ramp_up_hold=cfg.RAMP_UP_HOLD,
        full_soc_add_below=cfg.FULL_SOC_ADD_BELOW,
        shed_discharge_above=cfg.SHED_DISCHARGE_ABOVE,
        max_total_load=cfg.MAX_TOTAL_LOAD,
        publish_status=mqtt_client.publish,
        status_topic=cfg.STATUS_TOPIC,
    )

    return params, mqtt_client, relay, controller


async def main():
    params, mqtt_client, relay, controller = build_service()

    loop = asyncio.get_running_loop()
    mqtt_client.bind_loop(loop)
    mqtt_client.start()

    LOG.info("Start: %d grzałek po %d W, moduł '%s', impuls %.0fs odnawiany co %.0fs",
             relay.count, config.HEATER_POWER, config.RELAY_MODULE,
             config.FLASH_DURATION, config.PING_INTERVAL)

    stop_event = asyncio.Event()

    def _handle_stop():
        LOG.info("Sygnał zatrzymania - zwalniam grzałki (impulsy dogasną do %.0fs)",
                 config.FLASH_DURATION)
        relay.release_all()
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_stop)
        except (NotImplementedError, AttributeError):
            # Windows (dev) nie wspiera add_signal_handler dla wszystkich sygnałów
            pass

    tasks = [
        asyncio.create_task(control_loop(controller, config.CONTROL_INTERVAL)),
        asyncio.create_task(ping_loop(controller, relay, config.PING_INTERVAL,
                                      config.DECISION_MAX_AGE)),
    ]

    try:
        await stop_event.wait()
    finally:
        for t in tasks:
            t.cancel()
        mqtt_client.stop()


if __name__ == "__main__":
    asyncio.run(main())
