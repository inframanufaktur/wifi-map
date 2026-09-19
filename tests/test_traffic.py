"""Traffic sampler tests (fixtures only, no subprocess/network)."""
from __future__ import annotations

from wifimap import traffic as traffic_mod


_NETSTAT = """Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
en0        1500  <Link#14>   6a:be:e4:fa:13:50 19168190     0 25850300535  8653588     0 3852569809     0
en0        1500  owlbook.loc fe80:e::8a8:312f: 19168190     - 25850300535  8653588     - 3852569809     -
utun6      1420  <Link#27>                      6967068     0 8524156520  4629042     0 2180756050     0
"""


def test_parse_netstat_ib_link_row():
    assert traffic_mod.parse_netstat_ib(_NETSTAT, "en0") == (
        25850300535, 3852569809)


def test_parse_netstat_ib_utun_row():
    assert traffic_mod.parse_netstat_ib(_NETSTAT, "utun6") == (
        8524156520, 2180756050)


def test_parse_netstat_ib_missing_iface():
    assert traffic_mod.parse_netstat_ib(_NETSTAT, "en9") is None


def test_parse_netstat_ib_malformed():
    assert traffic_mod.parse_netstat_ib("garbage", "en0") is None
    assert traffic_mod.parse_netstat_ib("", "en0") is None


def _sampler(counters_seq, iface="en0", dt=1.0):
    """TrafficSampler over a canned counter sequence + fake clock."""
    calls = {"n": 0}

    def counters(_iface):
        n = min(calls["n"], len(counters_seq) - 1)
        return counters_seq[n]

    def clock():
        return calls["n"] * dt

    def step():
        calls["n"] += 1

    return traffic_mod.TrafficSampler(
        counters_fn=counters, iface_fn=lambda: iface, clock=clock), step


def test_sampler_first_sample_none():
    s, step = _sampler([(1000, 500)])
    assert s.sample() is None
    step()
    assert s.sample() == (0.0, 0.0)


def test_sampler_rates():
    seq = [(1000, 500), (1000 + 125_000, 500 + 25_000)]
    s, step = _sampler(seq, dt=1.0)
    s.sample()  # baseline
    step()
    # 125000 bytes in 1s = 1 Mbps down; 25000 = 0.2 Mbps up
    down, up = s.sample()
    assert round(down, 6) == 1.0
    assert round(up, 6) == 0.2


def test_sampler_iface_none():
    s = traffic_mod.TrafficSampler(
        counters_fn=lambda _: (1, 1), iface_fn=lambda: None,
        clock=lambda: 0.0)
    assert s.sample() is None


def test_sampler_counters_missing_rebaselines():
    seq = [(1000, 500), None, (2000, 1000)]
    calls = {"n": 0}

    def counters(_):
        n = calls["n"]
        calls["n"] += 1
        return seq[n] if n < len(seq) else seq[-1]

    s = traffic_mod.TrafficSampler(
        counters_fn=counters, iface_fn=lambda: "en0",
        clock=lambda: calls["n"] * 1.0)
    assert s.sample() is None       # baseline
    assert s.sample() is None       # counters gone → None, rebaseline
    assert s.sample() is None       # new baseline after recovery
    assert s.sample() == (0.0, 0.0)


def test_sampler_counter_reset():
    seq = [(1000, 500), (500, 100)]  # counters went backwards
    s, step = _sampler(seq, dt=1.0)
    s.sample()
    step()
    assert s.sample() is None


def test_sampler_iface_recheck_on_failure():
    # counters_fn fails once → iface re-resolved next sample
    state = {"fail": True}

    def counters(iface):
        if state["fail"]:
            return None
        return (1000, 500)

    ifaces = []

    def iface_fn():
        ifaces.append(1)
        return "en0"

    s = traffic_mod.TrafficSampler(
        counters_fn=counters, iface_fn=iface_fn, clock=lambda: 0.0)
    assert s.sample() is None       # counters fail → iface cleared
    state["fail"] = False
    s.sample()
    assert len(ifaces) == 2         # iface re-resolved
