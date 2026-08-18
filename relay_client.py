# relay_client.py
import logging
import time

LOG = logging.getLogger("relay")


class RelayClient:
    """Grzałki widziane przez waveshare-mqtt-gateway.

    Grzałka jest trzymana włączona sprzętowym impulsem `flash_on`, odnawianym co
    PING_INTERVAL. To jest cały watchdog tej usługi: jeśli proces padnie, straci
    brokera albo po prostu przestanie pingować, impuls dogasa sam po
    `flash_duration` i przekaźnik odpada - nikt nie musi po nas sprzątać, a
    odliczanie robi sam moduł, więc nawet zawieszony gateway tego nie zepsuje.

    Ceną jest to, że wyłączenie nie jest natychmiastowe: kanał z trwającym
    impulsem jest dla gatewaya "hands-off" (nie miesza się w sprzętowy odlicznik),
    więc gaśnie do `flash_duration` sekund po ostatnim pingu. `flash_duration` to
    pokrętło tego kompromisu - krócej znaczy szybsza reakcja na spadek nadwyżki,
    dłużej znaczy większą tolerancję na zerwanie MQTT.

    Zwolniona grzałka dostaje jeszcze jawne `off` z krótkim TTL. Sprzętowego
    impulsu to nie skróci, ale zostawia w retained statusie kanału jednoznaczny
    ślad "ta usługa już tego nie chce" - inaczej ostatnią rzeczą widoczną na
    topicu byłby nasz flash_on.
    """

    def __init__(self, publish, heaters, topic_root, module, service,
                 priority=10, flash_duration=60.0, off_ttl=120.0,
                 clock=time.monotonic):
        self.publish = publish
        self.heaters = list(heaters)
        self.topic_root = topic_root
        self.module = module
        self.service = service
        self.priority = priority
        self.flash_duration = flash_duration
        self.off_ttl = off_ttl
        self._clock = clock

        self.count = len(self.heaters)
        self.requested = [False] * self.count
        # monotonic ostatniego wysłanego flash_on - z tego wyliczamy, czy sprzęt
        # jeszcze odlicza impuls (patrz `energized`)
        self._last_ping = [None] * self.count
        # ostatni retained status kanału z gatewaya, tylko do weryfikacji
        self._reported = [None] * self.count

    # ---------------- topici ----------------
    def cmd_topic(self, index):
        return f"{self.topic_root}/{self.module}/{self.heaters[index]['name']}/cmd/{self.service}"

    def status_topic(self, index):
        return f"{self.topic_root}/{self.module}/{self.heaters[index]['name']}/status"

    def status_topics(self):
        return {self.status_topic(i): i for i in range(self.count)}

    def phase(self, index):
        return self.heaters[index]["phase"]

    # ---------------- żądania ----------------
    def request(self, index, state):
        """Ustawia żądanie dla grzałki. Włączenie zaczyna działać przy najbliższym
        `ping()`, wyłączenie wysyła `off` od razu."""
        if self.requested[index] == state:
            return
        self.requested[index] = state
        name = self.heaters[index]["name"]
        if state:
            LOG.info("Grzałka %s -> ON (faza L%d)", name, self.phase(index) + 1)
            self._flash_on(index)
        else:
            LOG.info("Grzałka %s -> OFF (impuls dogasa do %.0fs)", name, self.flash_duration)
            self.publish(self.cmd_topic(index),
                         {"cmd": "off", "ttl": self.off_ttl, "priority": self.priority})

    def release_all(self):
        for i in range(self.count):
            self.request(i, False)

    def requested_count(self):
        return sum(self.requested)

    # ---------------- keep-alive ----------------
    def ping(self):
        """Odnawia impuls dla wszystkich żądanych grzałek."""
        for i in range(self.count):
            if self.requested[i]:
                self._flash_on(i)

    def _flash_on(self, index):
        self.publish(self.cmd_topic(index), {
            "cmd": "flash_on",
            "duration": self.flash_duration,
            "priority": self.priority,
        })
        self._last_ping[index] = self._clock()

    # ---------------- stan sprzętu ----------------
    def energized(self, index):
        """Czy przekaźnik jest fizycznie załączony, licząc po naszych własnych
        impulsach. Model jest lokalny i deterministyczny (impuls trwa
        `flash_duration` od ostatniego pingu), więc decyzje nie zależą od tego,
        czy retained status z gatewaya właśnie doszedł. Bilans mocy musi
        korzystać właśnie z tego, a nie z `requested`: dogasająca grzałka nadal
        siedzi w pomiarze mocy z falownika."""
        last = self._last_ping[index]
        if last is None:
            return False
        return (self._clock() - last) < self.flash_duration

    def energized_count(self):
        return sum(self.energized(i) for i in range(self.count))

    def energized_mask(self):
        return [self.energized(i) for i in range(self.count)]

    def note_status(self, index, payload):
        """Retained status kanału z gatewaya. Nie sterujemy z niego (patrz
        `energized`), ale rozjazd z naszym modelem znaczy, że ktoś nam kanał
        przejął albo komendy nie docierają - i to warto zobaczyć w logu."""
        self._reported[index] = payload
        state = payload.get("state")
        by = payload.get("by")
        expected = self.energized(index)
        reported = state in ("on", "flash_on")
        if expected != reported:
            LOG.warning("Kanał %s: gateway mówi %r (by=%s), my liczymy %s",
                        self.heaters[index]["name"], state, by,
                        "ON" if expected else "OFF")
        elif reported and by not in (None, self.service):
            LOG.warning("Kanał %s trzyma teraz %s, nie my", self.heaters[index]["name"], by)

    def reported_states(self):
        return [None if r is None else r.get("state") for r in self._reported]
