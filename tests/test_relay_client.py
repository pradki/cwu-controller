# tests/test_relay_client.py - komendy do gatewaya i model sprzętowego impulsu
from fakes import build, check, done

TOPIC_L1 = "cave/relay/relay_test/cwu_l1/cmd/cwu-controller"
TOPIC_L2 = "cave/relay/relay_test/cwu_l2/cmd/cwu-controller"

clock, pub, params, relay, ctrl = build()

check("topic komendy w formacie gatewaya", relay.cmd_topic(0) == TOPIC_L1)
check("topic statusu kanału", relay.status_topic(0) == "cave/relay/relay_test/cwu_l1/status")
check("mapa statusów pokrywa wszystkie grzałki", relay.status_topics() == {
    "cave/relay/relay_test/cwu_l1/status": 0,
    "cave/relay/relay_test/cwu_l2/status": 1,
    "cave/relay/relay_test/cwu_l3/status": 2,
})

# --- włączenie: sprzętowy impuls ---
relay.request(0, True)
check("włączenie wysyła flash_on z duration i priorytetem",
      pub.last(TOPIC_L1)[1] == {"cmd": "flash_on", "duration": 60.0, "priority": 10})
check("flash_on nie jest retained", pub.last(TOPIC_L1)[2] is False)
check("po włączeniu przekaźnik zasilony", relay.energized(0) and relay.energized_count() == 1)

pub.clear()
relay.request(0, True)
check("powtórzone żądanie nie generuje komendy (keep-alive robi ping)", pub.messages == [])

# --- impuls gaśnie sam, jeśli nikt nie pinguje ---
clock.advance(59.9)
check("chwilę przed końcem impulsu wciąż zasilony", relay.energized(0))
clock.advance(0.2)
check("po czasie impulsu przekaźnik odpada sam", relay.energized(0) is False)

# --- ping przedłuża impuls ---
clock, pub, params, relay, ctrl = build()
relay.request(0, True)
clock.advance(40.0)
pub.clear()
relay.ping()
check("ping odnawia tylko żądane grzałki", pub.topics() == [TOPIC_L1])
clock.advance(40.0)
check("80 s od pierwszej komendy, ale 40 s od pingu -> wciąż zasilony", relay.energized(0))
clock.advance(21.0)
check("61 s bez pingu -> zgasło", relay.energized(0) is False)

# --- wyłączenie: jawne off, ale impuls dogasa po swojemu ---
clock, pub, params, relay, ctrl = build()
relay.request(0, True)
relay.request(1, True)
pub.clear()
relay.request(0, False)
check("wyłączenie wysyła off z TTL",
      pub.last(TOPIC_L1)[1] == {"cmd": "off", "ttl": 120.0, "priority": 10})
check("żądanie zdjęte od razu", relay.requested == [False, True, False])
check("ale przekaźnik jeszcze trzyma (sprzętowy odlicznik)", relay.energized(0))
check("bilans mocy liczy dogasającą grzałkę", relay.energized_count() == 2)
pub.clear()
relay.ping()
check("ping pomija zwolnioną grzałkę", pub.topics() == [TOPIC_L2])
clock.advance(60.1)
check("po impulsie zostaje tylko faktycznie żądana", relay.energized_count() == 0)

pub.clear()
relay.release_all()
check("release_all zwalnia to, co jeszcze żądane", pub.topics() == [TOPIC_L2])
check("nic nie jest już żądane", relay.requested_count() == 0)

# --- status z gatewaya tylko weryfikuje nasz model ---
clock, pub, params, relay, ctrl = build()
relay.request(0, True)
relay.note_status(0, {"state": "flash_on", "by": "cwu-controller", "channel": 16})
check("zgodny status zapamiętany", relay.reported_states()[0] == "flash_on")
relay.note_status(0, {"state": "off", "by": None, "channel": 16})
check("rozjazd nie wywraca sterowania", relay.reported_states()[0] == "off" and relay.energized(0))
relay.note_status(1, {"state": "on", "by": "heating", "channel": 17})
check("obcy właściciel kanału też tylko zapisany", relay.reported_states()[1] == "on")

done()
