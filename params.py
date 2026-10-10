# params.py
import time
from collections import deque


class Param:
    """Jedna wartość odbierana z brokera, z TTL.

    Po przekroczeniu TTL `valid` robi się False, a `value` zwraca `default`.
    Rozróżnienie "brak danych" / "wartość 0" jest tu istotne: w regułach mocy
    cicho podstawione zero wygląda dokładnie tak samo jak "nie ma słońca" albo
    "bateria nic nie oddaje", więc martwy falownik wyglądałby jak pogodna noc i
    sterownik nie miałby czego zauważyć.
    """

    def __init__(self, name, ttl=120.0, default=0.0, clock=time.monotonic):
        self.name = name
        self.ttl = ttl
        self.default = default
        self._clock = clock
        self._value = default
        self._updated = None  # None = nigdy nic nie przyszło

    def update(self, value):
        self._value = value
        self._updated = self._clock()

    @property
    def valid(self):
        if self._updated is None:
            return False
        return (self._clock() - self._updated) <= self.ttl

    @property
    def value(self):
        return self._value if self.valid else self.default

    @property
    def age(self):
        """Wiek ostatniej wartości w sekundach, albo None jeśli nic nie przyszło."""
        if self._updated is None:
            return None
        return self._clock() - self._updated


class Averaged(Param):
    """Param z dwiema średnimi ruchomymi: krótką i długą.

    Próbki dokłada pętla sterowania (raz na tick) przez `sample()`, a nie każda
    wiadomość MQTT - inaczej długość okna zależałaby od tego, jak często publikuje
    falownik, a nie od tego, jak długie wygładzanie chcemy mieć. Sens jest ten
    sam co w cavecwu: chwilowe zachmurzenie albo przejaśnienie nie ma prawa
    przestawić grzałek.

    Dwa okna, bo odpowiadają na różne pytania: krótkie "co jest teraz",
    długie "ile da się utrzymać" (w cwu-controller domyślnie 1 i 3 min - patrz
    config.py AVERAGE_SAMPLES/AVERAGE_SAMPLES_LONG). Grzałkę dokładamy dopiero, gdy
    zgadzają się oba - bez tego przy przelotnych chmurach sterownik włączałby trzy
    grzałki i zaraz zero, w rytm słońca.
    """

    def __init__(self, name, samples=6, long_samples=None, ttl=120.0, default=0.0,
                 clock=time.monotonic):
        super().__init__(name, ttl=ttl, default=default, clock=clock)
        self.window = samples
        self.long_window = long_samples
        self._samples = deque(maxlen=samples)
        self._long = deque(maxlen=long_samples) if long_samples else None

    def sample(self):
        """Dokłada aktualną wartość do okien. Dane wygasłe czyszczą je - stara
        średnia po przerwie w danych jest gorsza niż jawny brak średniej."""
        if self.valid:
            self._samples.append(self._value)
            if self._long is not None:
                self._long.append(self._value)
        else:
            self._samples.clear()
            if self._long is not None:
                self._long.clear()

    @staticmethod
    def _mean(samples, default):
        if not samples:
            return default
        return sum(samples) / len(samples)

    @property
    def average(self):
        return self._mean(self._samples, self.default)

    @property
    def average_long(self):
        """Średnia z długiego okna. Bez skonfigurowanego długiego okna zwraca
        krótką, żeby wołający nie musiał tego rozgałęziać. Okno niepełne (np. świeżo
        po restarcie usługi) liczy się z tego, co już jest - długa średnia wtedy
        po prostu jeszcze niczego nie ogranicza."""
        if self._long is None:
            return self.average
        return self._mean(self._long, self.default)

    @property
    def samples(self):
        return len(self._samples)

    @property
    def samples_long(self):
        return len(self._long) if self._long is not None else len(self._samples)


class CWUParams:
    """Wszystko, co sterownik dostaje z brokera, w jednym miejscu.

    Uśredniane są moc baterii, produkcja PV i obciążenia faz - czyli wszystkie
    wielkości, którymi machają chmury. SOC i temperatury zmieniają się wolno,
    więc idą bez wygładzania.
    """

    def __init__(self, phases=3, samples=6, long_samples=None, param_ttl=120.0,
                 sensor_ttl=300.0, clock=time.monotonic):
        self.battery_soc = Param("battery_soc", ttl=param_ttl, clock=clock)
        # Moc baterii i produkcja PV mają też długie okno - to na nich sterownik
        # ocenia, ile nadwyżki da się utrzymać, a nie tylko ile jest w tej chwili.
        self.battery_power = Averaged("battery_power", samples=samples,
                                      long_samples=long_samples, ttl=param_ttl, clock=clock)
        self.pv1_power = Param("pv1_power", ttl=param_ttl, clock=clock)
        self.pv2_power = Param("pv2_power", ttl=param_ttl, clock=clock)
        # pv_power to naczynie na średnią z sumy pv1+pv2; o świeżości danych
        # decydują pv1/pv2 (patrz `pv_valid`), nie ono samo.
        self.pv_power = Averaged("pv_power", samples=samples, long_samples=long_samples,
                                 ttl=param_ttl, clock=clock)
        self.load_power = [
            Averaged(f"load_power_l{i + 1}", samples=samples, ttl=param_ttl, clock=clock)
            for i in range(phases)
        ]
        self.buff_top_temp = Param("buff_top_temp", ttl=sensor_ttl, clock=clock)
        self.buff_upmid_temp = Param("buff_upmid_temp", ttl=sensor_ttl, clock=clock)

    @property
    def pv_valid(self):
        return self.pv1_power.valid and self.pv2_power.valid

    @property
    def inverter_valid(self):
        """Czy mamy pełny obraz z falownika. Bez tego nie ma jak policzyć bilansu,
        więc sterownik gasi grzałki zamiast zgadywać."""
        return self.battery_soc.valid and self.battery_power.valid and self.pv_valid

    @property
    def load_valid(self):
        """Obciążenia faz są potrzebne do wyboru fazy i do limitu obciążenia -
        bez nich grzejemy dalej, tylko bez bilansowania faz i bez tego limitu."""
        return all(lp.valid for lp in self.load_power)

    @property
    def load_total_now(self):
        """Chwilowe obciążenie wszystkich faz, albo None gdy brak danych. Do
        twardego limitu mocy - tu liczy się "co jest teraz", nie średnia."""
        if not self.load_valid:
            return None
        return sum(lp.value for lp in self.load_power)

    @property
    def load_total_avg(self):
        """Uśrednione obciążenie wszystkich faz, albo None gdy brak danych. Do
        oceny, czy jest zapas mocy na kolejną grzałkę."""
        if not self.load_valid:
            return None
        return sum(lp.average for lp in self.load_power)

    def sample(self):
        """Jeden tick sterowania: dokłada próbkę do każdej średniej ruchomej."""
        if self.pv_valid:
            self.pv_power.update(self.pv1_power.value + self.pv2_power.value)
        self.battery_power.sample()
        self.pv_power.sample()
        for lp in self.load_power:
            lp.sample()

    def topic_map(self, deye_root, sensor_root, top_sensor="buff_top_temp",
                  upmid_sensor="buff_upmid_temp"):
        """Topic -> Param. Ładunek to JSON `{"value": ...}` (tak publikuje
        deye-modbus-mqtt i czujniki), obsługę parsowania ma warstwa MQTT."""
        topics = {
            f"{deye_root}/battery_soc": self.battery_soc,
            f"{deye_root}/battery_power": self.battery_power,
            f"{deye_root}/pv1_power": self.pv1_power,
            f"{deye_root}/pv2_power": self.pv2_power,
            f"{sensor_root}/{top_sensor}": self.buff_top_temp,
            f"{sensor_root}/{upmid_sensor}": self.buff_upmid_temp,
        }
        for i, lp in enumerate(self.load_power):
            topics[f"{deye_root}/load_power_l{i + 1}"] = lp
        return topics
