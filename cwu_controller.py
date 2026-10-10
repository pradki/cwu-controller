# cwu_controller.py
import logging
import time
from datetime import datetime

LOG = logging.getLogger("cwu")

DEFAULT_RULES = [
    (90, 400),
    (80, 1000),
    (70, 2000),
    (60, 3000),
    (50, 4000),
    (40, 5000),
]

# Marzec-wrzesień: jak dotychczas (DEFAULT_RULES + progi histerezy poniżej).
# Październik-luty: priorytet ma naładowanie akumulatora, więc grzejemy tylko
# przy naprawdę dużej nadwyżce - patrz config.py WINTER_* i README.
DEFAULT_WINTER_MONTHS = frozenset({10, 11, 12, 1, 2})


class CWUController:
    """Grzanie CWU nadwyżką z fotowoltaiki.

    Jeden tick (domyślnie co 20 s, czyli w rytmie publikacji falownika):

      1. Dokłada próbki do średnich ruchomych (moc baterii, PV, obciążenia faz).
      2. Bezpieczniki: przegrzanie buforu albo brak danych z falownika = wszystko off.
      3. Zdejmowanie awaryjne (`_shed_reason`): bateria oddaje za dużo albo dom
         ciągnie ponad moc falownika - zdejmujemy grzałkę natychmiast.
      4. Liczy pożądaną liczbę grzałek (`_desired_level`) i realizuje różnicę,
         wybierając grzałkę po obciążeniu faz (`_pick_on` / `_pick_off`).

    Sterowanie jest celowo niesymetryczne:

    - **W górę powoli**: po jednej grzałce, nie częściej niż `ramp_up_hold`, i tylko
      jeśli poziom potwierdza zarówno krótka (1 min) jak i długa (3 min) średnia.
      Chodzi o to, żeby przy 3 kW średniej nadwyżki chodziły stale 1-2 grzałki, a nie
      raz trzy, raz zero, w rytm chmur.
    - **W dół natychmiast**: zdejmowanie awaryjne nie czeka ani na stabilizację
      średniej, ani na `ramp_up_hold`, i może schodzić po grzałce na tick.

    Po każdej zmianie robi `settle_ticks` ticków przerwy w decyzjach z reguł - dopóki
    średnia nie obejmie nowego stanu, bilans dotyczy poprzedniego układu grzałek, a
    liczenie na nim zjechałoby do zera po jednym zdjęciu. Zdejmowanie awaryjne jest
    z tej przerwy wyłączone, bo patrzy na wartość chwilową i samo się koryguje.
    """

    def __init__(self, params, relay, heater_power=2000, rules=None,
                 hysteresis_rule=(94, -1000), soc_hysteresis_on=95,
                 soc_hysteresis_off=94, pv_hysteresis_on=800, soc_full=99,
                 winter_rules=None, winter_hysteresis_rule=None,
                 winter_soc_hysteresis_on=None, winter_soc_hysteresis_off=None,
                 winter_pv_hysteresis_on=None, winter_soc_full=None,
                 winter_months=DEFAULT_WINTER_MONTHS,
                 max_temp=85.0, settle_ticks=6, ramp_up_hold=300.0,
                 full_soc_add_below=200.0, shed_discharge_above=None,
                 max_total_load=None, publish_status=None,
                 status_topic="cave/cwu/status", clock=time.monotonic,
                 month_fn=None):
        self.params = params
        self.relay = relay
        self.heater_power = heater_power

        # Marzec-wrzesień ("lato"): parametry przekazane wprost, jak dotychczas.
        # Październik-luty ("zima"): każdy winter_* bez wartości dziedziczy swój
        # letni odpowiednik, więc podanie tylko części z nich (np. samych reguł)
        # nie zostawia reszty na przypadkowych defaultach klasy.
        self._summer = {
            "rules": list(rules if rules is not None else DEFAULT_RULES),
            "hysteresis_rule": hysteresis_rule,
            "soc_hysteresis_on": soc_hysteresis_on,
            "soc_hysteresis_off": soc_hysteresis_off,
            "pv_hysteresis_on": pv_hysteresis_on,
            "soc_full": soc_full,
        }
        self._winter = {
            "rules": (list(winter_rules) if winter_rules is not None
                      else list(self._summer["rules"])),
            "hysteresis_rule": (winter_hysteresis_rule if winter_hysteresis_rule is not None
                                else self._summer["hysteresis_rule"]),
            "soc_hysteresis_on": (winter_soc_hysteresis_on if winter_soc_hysteresis_on is not None
                                  else self._summer["soc_hysteresis_on"]),
            "soc_hysteresis_off": (winter_soc_hysteresis_off if winter_soc_hysteresis_off is not None
                                   else self._summer["soc_hysteresis_off"]),
            "pv_hysteresis_on": (winter_pv_hysteresis_on if winter_pv_hysteresis_on is not None
                                 else self._summer["pv_hysteresis_on"]),
            "soc_full": (winter_soc_full if winter_soc_full is not None
                        else self._summer["soc_full"]),
        }
        self.winter_months = frozenset(winter_months)
        self._month_fn = month_fn or (lambda: datetime.now().month)
        self.season = None  # ustawione poniżej, i odświeżane co tick
        self._apply_season()

        self.max_temp = max_temp
        self.settle_ticks = settle_ticks
        self.ramp_up_hold = ramp_up_hold
        self.full_soc_add_below = full_soc_add_below
        # Domyślnie "moc jednej grzałki": jeśli bateria oddaje więcej, to znaczy, że
        # grzałki jadą z akumulatora (a przy jego limicie - i z sieci), nie z nadwyżki.
        self.shed_discharge_above = (heater_power if shed_discharge_above is None
                                     else shed_discharge_above)
        self.max_total_load = max_total_load
        self._publish_status = publish_status
        self.status_topic = status_topic
        self._clock = clock

        self.hysteresis_active = False
        self.level = 0            # ostatnio wyliczony poziom (do statusu)
        self.reason = "start"
        self.ticks = 0
        self._settle = 0
        self._last_ramp_up = None   # monotonic ostatniego dołożenia grzałki
        self._last_decision = None  # monotonic ostatniego udanego ticku

    # ================= pora roku =================
    def _apply_season(self):
        """Dobiera aktywny komplet reguł/histerezy wg bieżącego miesiąca.
        Wołane co tick (nie tylko raz przy starcie), żeby przejście
        luty->marzec albo wrzesień->październik zadziałało samo, bez restartu
        usługi w środku sezonu."""
        winter = self._month_fn() in self.winter_months
        season = self._winter if winter else self._summer
        self.season = "winter" if winter else "summer"
        self.rules = list(season["rules"])
        self.hysteresis_rule = season["hysteresis_rule"]
        self.soc_hysteresis_on = season["soc_hysteresis_on"]
        self.soc_hysteresis_off = season["soc_hysteresis_off"]
        self.pv_hysteresis_on = season["pv_hysteresis_on"]
        self.soc_full = season["soc_full"]

    # ================= bezpieczniki =================
    def _overheat(self):
        """(True, powód) gdy nie wolno grzać. Brak pomiaru temperatury liczy się
        jako awaria, nie jako zimny bufor - tak samo jak w cavecwu."""
        top = self.params.buff_top_temp
        upmid = self.params.buff_upmid_temp
        readings = [t.value for t in (top, upmid) if t.valid]
        if not readings:
            return True, "brak pomiaru temperatury buforu"
        hottest = max(readings)
        if hottest >= self.max_temp:
            return True, f"przegrzanie buforu {hottest:.1f}>={self.max_temp:.0f}C"
        return False, ""

    # ================= zdejmowanie awaryjne =================
    def _effective_discharge(self):
        """Chwilowe rozładowanie baterii (W, >0 = oddaje), pomniejszone o grzałki
        już zwolnione, którym dogasa przekaźnik. Ich moc zaraz zniknie z pomiaru,
        więc zdejmowanie za nie kolejnych zjechałoby do zera bez potrzeby."""
        pending = max(0, self.relay.energized_count() - self.relay.requested_count())
        return self.params.battery_power.value - pending * self.heater_power

    def _shed_reason(self):
        """Powód natychmiastowego zdjęcia jednej grzałki, albo None.

        Oba warunki celowo patrzą na wartość chwilową, nie na średnią: to
        zabezpieczenie przed przeciążeniem, a nie regulacja. Chmura nad panelami
        przy pełnej baterii potrafi w jednym kroku przestawić falownik z
        "ładuje" na "oddaje 8 kW i dobiera z sieci" - na to nie ma sensu czekać
        dwie minuty."""
        if not self.relay.requested_count():
            return None

        draw = self._effective_discharge()
        if draw > self.shed_discharge_above:
            return (f"bateria oddaje {draw:.0f}W > {self.shed_discharge_above:.0f}W "
                    f"- zdejmuję grzałkę")

        total = self.params.load_total_now
        if self.max_total_load is not None and total is not None and total > self.max_total_load:
            return (f"obciążenie {total:.0f}W > {self.max_total_load:.0f}W "
                    f"- zdejmuję grzałkę")
        return None

    # ================= poziom =================
    def _update_hysteresis(self, soc, pv_power):
        if soc <= self.soc_hysteresis_off:
            self.hysteresis_active = False
        if soc > self.soc_hysteresis_on and pv_power > self.pv_hysteresis_on:
            self.hysteresis_active = True

    def _level_from_rules(self, soc, pv_power, battery_power_avg, energized, rules):
        """(poziom, opis) z tabeli reguł. Reguły przeglądane od najwyższego SOC:
        pierwsza, która przechodzi (soc > progSOC i PV > progPV), wyznacza poziom
        z bilansu mocy. progPV jest jednocześnie rezerwą - tyle mocy zostawiamy
        bateriom, nie oddajemy jej grzałkom."""
        # Moc, jaka byłaby na bilansie, gdyby grzałki nie pracowały.
        # battery_power > 0 = rozładowanie, < 0 = ładowanie (czyli nadwyżka).
        available_power = battery_power_avg - (energized * self.heater_power)

        for soc_limit, pv_limit in rules:
            if soc > soc_limit and pv_power > pv_limit:
                value = (available_power + pv_limit) / -self.heater_power
                level = max(0, min(self.relay.count, int(value)))
                return level, (f"SOC {soc:.0f}%>{soc_limit} PV {pv_power:.0f}W>{pv_limit}W, "
                               f"bilans {available_power:.0f}W -> {value:.2f}")

        return 0, (f"brak nadwyżki (SOC {soc:.0f}%, PV {pv_power:.0f}W, "
                   f"bilans {available_power:.0f}W)")

    def _level_full_soc(self, base):
        """(poziom, opis) przy pełnej baterii, metodą prób.

        Pełna bateria po prostu przestaje się ładować, a nadwyżkę falownik ucina -
        więc bilans mocy nic już nie mówi i nie ma z czego wyliczyć poziomu (dlatego
        cavecwu w tym miejscu zapalało po prostu wszystkie grzałki). Jedyny sposób
        poznania zapasu to spróbować: dołożyć jedną grzałkę i sprawdzić, czy bateria
        zaczyna oddawać. Jeśli nie - można dołożyć następną.

        Zdejmowanie robi `_shed_reason`, na wartości chwilowej. Między progiem
        dokładania a progiem zdejmowania jest szeroka strefa martwa i to jest celowe:
        dokładanie przy rozładowaniu "trochę mniejszym niż grzałka" oznaczałoby
        dołożyć, przekroczyć próg, zdjąć, i tak w kółko."""
        draw_short = self.params.battery_power.average
        draw_long = self.params.battery_power.average_long
        draw = max(draw_short, draw_long)
        if draw < self.full_soc_add_below:
            return min(self.relay.count, base + 1), (
                f"SOC pełny, bateria nie oddaje ({draw:.0f}W < "
                f"{self.full_soc_add_below:.0f}W) - próbuję dołożyć grzałkę")
        return base, (f"SOC pełny, bateria oddaje {draw:.0f}W - "
                      f"trzymam {base} grzałek")

    def _desired_level(self):
        """Ile grzałek powinno pracować (i ustawia `self.reason`)."""
        soc = self.params.battery_soc.value
        pv_short = self.params.pv_power.average
        # Liczymy po fizycznie załączonych, nie po żądanych: moc grzałki, która
        # jeszcze dogasa, siedzi w pomiarze z falownika i trzeba ją odjąć, żeby
        # dostać bilans "bez naszego grzania".
        energized = self.relay.energized_count()

        self._update_hysteresis(soc, pv_short)
        rules = list(self.rules)
        if self.hysteresis_active:
            rules.insert(0, self.hysteresis_rule)

        if soc > self.soc_full:
            level, reason = self._level_full_soc(self.relay.requested_count())
            self.reason = reason
            return level

        fast, fast_reason = self._level_from_rules(
            soc, pv_short, self.params.battery_power.average, energized, rules)
        slow, slow_reason = self._level_from_rules(
            soc, self.params.pv_power.average_long,
            self.params.battery_power.average_long, energized, rules)

        # Krótka średnia mówi "co jest teraz", długa "ile da się utrzymać" - do
        # góry idziemy tylko na tyle, na ile zgadzają się obie.
        if slow < fast:
            self.reason = f"{slow_reason} [3 min ogranicza z {fast} do {slow}]"
            return slow
        self.reason = fast_reason
        return fast

    # ================= bilans faz =================
    def phase_net_load(self):
        """Średnie obciążenie faz pomniejszone o nasze własne, aktualnie załączone
        grzałki. Bez tego odejmowania faza, na której już grzejemy, zawsze
        wyglądałaby na najbardziej obciążoną - sterownik by ją wyłączał i włączał
        w kółko, zamiast porównywać to, co na fazach robią inni odbiorcy."""
        net = [lp.average for lp in self.params.load_power]
        for i in range(self.relay.count):
            if self.relay.energized(i):
                net[self.relay.phase(i)] -= self.heater_power
        return net

    def _pick_on(self, count):
        """Grzałki do włączenia: najpierw te z najmniej obciążonej fazy."""
        net = self.phase_net_load()
        candidates = [i for i in range(self.relay.count) if not self.relay.requested[i]]
        candidates.sort(key=lambda i: (net[self.relay.phase(i)], i))
        return candidates[:count]

    def _pick_off(self, count):
        """Grzałki do wyłączenia: najpierw te z najbardziej obciążonej fazy."""
        net = self.phase_net_load()
        candidates = [i for i in range(self.relay.count) if self.relay.requested[i]]
        candidates.sort(key=lambda i: (-net[self.relay.phase(i)], i))
        return candidates[:count]

    def _ramp_up_wait(self):
        """Ile sekund do końca odczekania przed kolejnym dołożeniem grzałki."""
        if self._last_ramp_up is None:
            return 0.0
        return max(0.0, round(self.ramp_up_hold - (self._clock() - self._last_ramp_up), 1))

    def _ramp_up_block(self):
        """Powód, dla którego nie wolno teraz dołożyć grzałki, albo None."""
        left = self._ramp_up_wait()
        if left > 0:
            return f"dokładam po jednej, jeszcze {left:.0f}s"

        total = self.params.load_total_avg
        if (self.max_total_load is not None and total is not None
                and total + self.heater_power > self.max_total_load):
            return (f"brak zapasu mocy ({total:.0f}W + grzałka > "
                    f"{self.max_total_load:.0f}W)")
        return None

    def _apply_level(self, target):
        requested = self.relay.requested_count()
        if target == requested:
            return
        if target < requested:
            # W dół schodzimy od razu i o tyle, ile trzeba.
            for i in self._pick_off(requested - target):
                self.relay.request(i, False)
            self._settle = self.settle_ticks
            return

        blocked = self._ramp_up_block()
        if blocked:
            self.reason += f" | {blocked}"
            return
        # W górę zawsze po jednej grzałce, choćby bilans pozwalał na więcej.
        for i in self._pick_on(1):
            self.relay.request(i, True)
        self._last_ramp_up = self._clock()
        self._settle = self.settle_ticks

    # ================= tick =================
    def tick(self):
        self.ticks += 1
        self._apply_season()
        self.params.sample()

        overheat, overheat_reason = self._overheat()
        if overheat:
            self.hysteresis_active = False
            self.level = 0
            self.reason = overheat_reason
            if self.relay.requested_count():
                LOG.warning("STOP: %s", overheat_reason)
            self.relay.release_all()
        elif not self.params.inverter_valid:
            self.level = 0
            self.reason = "brak danych z falownika"
            if self.relay.requested_count():
                LOG.warning("STOP: %s", self.reason)
            self.relay.release_all()
        else:
            shed = self._shed_reason()
            if shed:
                # Zdejmowanie awaryjne omija i stabilizację średniej, i ramp_up_hold.
                LOG.warning("%s", shed)
                for i in self._pick_off(1):
                    self.relay.request(i, False)
                self._settle = self.settle_ticks
                self.level = self.relay.requested_count()
                self.reason = shed
            else:
                self.level = self._desired_level()
                if self._settle > 0:
                    self._settle -= 1
                    self.reason += f" | czekam na stabilizację średniej ({self._settle})"
                elif self.relay.energized_count() > self.relay.requested_count():
                    # Wyłączenie już poszło, ale sprzętowy impuls jeszcze dogasa -
                    # pomiar mocy wciąż zawiera tę grzałkę, nie ma z czego decydować.
                    self.reason += " | czekam na wygaszenie przekaźnika"
                else:
                    self._apply_level(self.level)

        self._last_decision = self._clock()
        self.publish_status()
        return self.level

    def decision_age(self):
        """Wiek ostatniego udanego ticku. Pętla pingująca po tym poznaje, że
        sterowanie stanęło - i przestaje odnawiać impulsy, zamiast trzymać
        grzałki na ostatniej decyzji w nieskończoność."""
        if self._last_decision is None:
            return None
        return self._clock() - self._last_decision

    # ================= status =================
    def status_payload(self):
        p = self.params
        return {
            "level": self.level,
            "reason": self.reason,
            "requested": [self.relay.heaters[i]["name"]
                          for i in range(self.relay.count) if self.relay.requested[i]],
            "energized": [self.relay.heaters[i]["name"]
                          for i in range(self.relay.count) if self.relay.energized(i)],
            "battery_soc": p.battery_soc.value,
            "battery_power_now": p.battery_power.value,
            "battery_power_avg": round(p.battery_power.average, 1),
            "battery_power_avg_long": round(p.battery_power.average_long, 1),
            "pv_power_avg": round(p.pv_power.average, 1),
            "pv_power_avg_long": round(p.pv_power.average_long, 1),
            "load_total_now": p.load_total_now,
            "ramp_up_wait": self._ramp_up_wait(),
            "phase_load_avg": [round(lp.average, 1) for lp in p.load_power],
            "phase_load_net": [round(v, 1) for v in self.phase_net_load()],
            "hysteresis": self.hysteresis_active,
            "season": self.season,
            "samples": p.battery_power.samples,
            "buff_top_temp": p.buff_top_temp.value if p.buff_top_temp.valid else None,
            "buff_upmid_temp": p.buff_upmid_temp.value if p.buff_upmid_temp.valid else None,
            "inverter_data": p.inverter_valid,
            "load_data": p.load_valid,
            "ticks": self.ticks,
            "ts": int(time.time()),
        }

    def publish_status(self):
        if self._publish_status is None:
            return
        self._publish_status(self.status_topic, self.status_payload(), True)
