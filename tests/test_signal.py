"""Tests for wifimap.signal — CoreWLAN reader with mocked pyobjc module."""
import sys
import types

import pytest

from wifimap.signal import (
    NoWiFiError,
    Signal,
    SignalUnavailableError,
    read_signal,
)


class FakeChannel:
    def __init__(self, number=36, width=4):
        self._number = number
        self._width = width

    def channelNumber(self):
        return self._number

    def channelWidth(self):
        return self._width


class FakeInterface:
    def __init__(self, ssid="HomeNet", bssid="aa:bb:cc:dd:ee:ff",
                 rssi=-57, noise=-92, channel=None, rate=780.0, phy=5,
                 name=None):
        self._ssid = ssid
        self._bssid = bssid
        self._rssi = rssi
        self._noise = noise
        self._channel = channel if channel is not None else FakeChannel()
        self._rate = rate
        self._phy = phy
        self._name = name

    def ssid(self):
        return self._ssid

    def bssid(self):
        return self._bssid

    def interfaceName(self):
        return self._name

    def rssiValue(self):
        return self._rssi

    def noiseMeasurement(self):
        return self._noise

    def wlanChannel(self):
        return self._channel

    def transmitRate(self):
        return self._rate

    def activePHYMode(self):
        return self._phy


class FakeClient:
    iface = None

    @classmethod
    def sharedWiFiClient(cls):
        return cls()

    def interface(self):
        return self.iface


@pytest.fixture
def fake_wlan(monkeypatch):
    """Default associated-network fake; tests may override .iface."""
    iface = FakeInterface()
    monkeypatch.setitem(sys.modules, "CoreWLAN",
                        _make_mod(iface))
    return iface


def _make_mod(iface):
    FakeClient.iface = iface
    mod = types.ModuleType("CoreWLAN")
    mod.CWWiFiClient = FakeClient
    return mod


def test_read_signal_full_fields(fake_wlan):
    sig = read_signal()
    assert isinstance(sig, Signal)
    assert sig.ssid == "HomeNet"
    assert sig.bssid == "aa:bb:cc:dd:ee:ff"
    assert sig.rssi == -57
    assert sig.noise == -92
    assert sig.snr == 35
    assert sig.channel is not None and "36" in sig.channel
    assert sig.phy == "802.11ac"
    assert sig.tx_rate is not None and "780" in sig.tx_rate


def test_read_signal_none_ssid_bssid_ok(monkeypatch):
    """macOS 26 redacts SSID/BSSID without Location permission — normal."""
    monkeypatch.setitem(sys.modules, "CoreWLAN",
                        _make_mod(FakeInterface(ssid=None, bssid=None)))
    sig = read_signal()
    assert sig.ssid is None
    assert sig.bssid is None
    assert sig.rssi == -57
    assert sig.snr == 35


def test_read_signal_no_interface_raises(monkeypatch):
    monkeypatch.setitem(sys.modules, "CoreWLAN", _make_mod(None))
    with pytest.raises(NoWiFiError):
        read_signal()


def test_read_signal_snr_math(monkeypatch):
    monkeypatch.setitem(sys.modules, "CoreWLAN",
                        _make_mod(FakeInterface(rssi=-60, noise=-90)))
    sig = read_signal()
    assert sig.snr == 30


def test_read_signal_missing_pyobjc_raises_unavailable(monkeypatch):
    monkeypatch.setitem(sys.modules, "CoreWLAN", None)
    with pytest.raises(SignalUnavailableError):
        read_signal()


# --- sample_signal (5s averaging over injected read_fn) ---

def _sleep_counter():
    calls = {"n": 0}

    def _sleep(seconds):
        calls["n"] += 1

    return calls, _sleep


def test_sample_signal_averages_rssi_noise_and_snr():
    from wifimap import signal as sig_mod
    seq = [
        sig_mod.Signal(rssi=-60, noise=-90, snr=30),
        sig_mod.Signal(rssi=-64, noise=-92, snr=28),
        sig_mod.Signal(rssi=-61, noise=-94, snr=33),
    ]
    calls, _sleep = _sleep_counter()

    def _read():
        return seq[min(len(seq) - 1, calls["n"])]

    sig = sig_mod.sample_signal(seconds=1.0, read_fn=_read, sleep_fn=_sleep)
    assert sig.rssi == round((-60 + -64 + -61) / 3)
    assert sig.noise == round((-90 + -92 + -94) / 3)
    assert sig.snr == sig.rssi - sig.noise
    assert calls["n"] >= 2  # sampled more than once


def test_sample_signal_last_sample_passthrough_fields():
    from wifimap import signal as sig_mod
    last = sig_mod.Signal(ssid="net", bssid="aa:bb", rssi=-50, noise=-90,
                          snr=40, channel="36 (80MHz)", phy="802.11ax",
                          tx_rate="866", mcs=9, band="5 GHz",
                          security="WPA3 Personal")

    def _read():
        return last

    sig = sig_mod.sample_signal(seconds=1.0, read_fn=_read,
                                sleep_fn=lambda s: None)
    assert sig.ssid == "net"
    assert sig.bssid == "aa:bb"
    assert sig.channel == "36 (80MHz)"
    assert sig.phy == "802.11ax"
    assert sig.tx_rate == "866"
    assert sig.mcs == 9
    assert sig.band == "5 GHz"
    assert sig.security == "WPA3 Personal"


def test_sample_signal_nowifi_propagates_no_partial(monkeypatch):
    from wifimap import signal as sig_mod
    calls = {"n": 0}

    def _read():
        calls["n"] += 1
        if calls["n"] >= 2:
            raise sig_mod.NoWiFiError("off mid-sample")
        return sig_mod.Signal(rssi=-60, noise=-90, snr=30)

    with pytest.raises(sig_mod.NoWiFiError):
        sig_mod.sample_signal(seconds=2.0, read_fn=_read,
                              sleep_fn=lambda s: None)
    assert calls["n"] == 2  # early abort, no partial average


def test_sample_signal_unavailable_propagates():
    from wifimap import signal as sig_mod

    def _read():
        raise sig_mod.SignalUnavailableError("no backend")

    with pytest.raises(sig_mod.SignalUnavailableError):
        sig_mod.sample_signal(seconds=1.0, read_fn=_read,
                              sleep_fn=lambda s: None)


def test_sample_signal_default_read_fn_resolved_late(monkeypatch):
    """Body resolves read_signal at call time so module monkeypatch works."""
    from wifimap import signal as sig_mod
    monkeypatch.setattr(
        sig_mod, "read_signal",
        lambda timeout=2.0: sig_mod.Signal(rssi=-55, noise=-95, snr=40))
    sig = sig_mod.sample_signal(seconds=1.0, sleep_fn=lambda s: None)
    assert sig.rssi == -55
    assert sig.snr == 40


# --- read_network_identity (networksetup/wdutil, session-start only) ---

def _run_ok(stdout):
    import subprocess

    return subprocess.CompletedProcess(
        args=["sudo", "wdutil", "info"], returncode=0,
        stdout=stdout, stderr="")


def test_identity_prefers_corewlan_when_present(monkeypatch):
    """Location-granted case: CoreWLAN ssid/bssid wins, no sudo needed."""
    import subprocess

    from wifimap import signal as sig_mod
    monkeypatch.setitem(sys.modules, "CoreWLAN",
                        _make_mod(FakeInterface(ssid="Home", bssid="aa:bb:cc:dd:ee:ff")))
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append((a, k)) or _run_ok("SSID: X\nBSSID: Y\n"))
    assert sig_mod.read_network_identity() == ("Home", "aa:bb:cc:dd:ee:ff")
    assert calls == []


def test_identity_passwordless_sudo_parses_wdutil(monkeypatch):
    from wifimap import signal as sig_mod
    import subprocess
    monkeypatch.setitem(sys.modules, "CoreWLAN",
                        _make_mod(FakeInterface(ssid=None, bssid=None)))

    def _fake(cmd, **kw):
        if cmd == ["sudo", "-n", "true"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["sudo", "-n", "wdutil"]:
            return _run_ok("SSID : MyNet\nBSSID : 11:22:33:44:55:66\n")
        raise AssertionError("unexpected cmd %r" % (cmd,))

    monkeypatch.setattr(subprocess, "run", _fake)
    assert sig_mod.read_network_identity() == ("MyNet", "11:22:33:44:55:66")


def test_identity_parses_variant_labels(monkeypatch):
    from wifimap import signal as sig_mod
    import subprocess
    monkeypatch.setitem(sys.modules, "CoreWLAN",
                        _make_mod(FakeInterface(ssid=None, bssid=None)))

    def _fake(cmd, **kw):
        if cmd == ["sudo", "-n", "true"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return _run_ok("ssid=VariantNet\nbss: AA-BB-CC-DD-EE-FF\n")

    monkeypatch.setattr(subprocess, "run", _fake)
    assert sig_mod.read_network_identity() == ("VariantNet", "AA-BB-CC-DD-EE-FF")


def test_identity_accepts_corewlan_ssid_without_bssid(monkeypatch):
    """macOS may expose the network name while redacting the AP address."""
    from wifimap import signal as sig_mod
    import subprocess
    monkeypatch.setitem(sys.modules, "CoreWLAN",
                        _make_mod(FakeInterface(ssid="Home", bssid=None)))
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: calls.append((a, k)))

    assert sig_mod.read_network_identity() == ("Home", None)
    assert calls == []


def test_identity_uses_networksetup_before_sudo(monkeypatch):
    """A redacted CoreWLAN SSID falls back without a password prompt."""
    from wifimap import signal as sig_mod
    import subprocess
    monkeypatch.setitem(
        sys.modules, "CoreWLAN",
        _make_mod(FakeInterface(ssid=None, bssid=None, name="en0")),
    )
    seen = []

    def _fake(cmd, **kw):
        seen.append(cmd)
        if cmd == ["/usr/sbin/networksetup", "-getairportnetwork", "en0"]:
            return subprocess.CompletedProcess(
                cmd, 0, "Current Wi-Fi Network: GardenNet\n", "")
        raise AssertionError("sudo must not run after networksetup succeeds")

    monkeypatch.setattr(subprocess, "run", _fake)
    assert sig_mod.read_network_identity() == ("GardenNet", None)
    assert seen == [["/usr/sbin/networksetup", "-getairportnetwork", "en0"]]


def test_identity_accepts_wdutil_ssid_without_bssid(monkeypatch):
    """SSID detection must not depend on wdutil exposing a BSSID."""
    from wifimap import signal as sig_mod
    import subprocess
    monkeypatch.setitem(sys.modules, "CoreWLAN",
                        _make_mod(FakeInterface(ssid=None, bssid=None)))

    def _fake(cmd, **kw):
        if cmd == ["sudo", "-n", "true"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return _run_ok("SSID: Home\n")

    monkeypatch.setattr(subprocess, "run", _fake)
    assert sig_mod.read_network_identity() == ("Home", None)


def test_identity_rejects_redacted_ssid(monkeypatch):
    from wifimap import signal as sig_mod
    import subprocess
    monkeypatch.setitem(sys.modules, "CoreWLAN",
                        _make_mod(FakeInterface(ssid=None, bssid=None)))

    def _fake(cmd, **kw):
        if cmd == ["sudo", "-n", "true"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return _run_ok("SSID: <redacted>\n")

    monkeypatch.setattr(subprocess, "run", _fake)
    assert sig_mod.read_network_identity() is None


def test_identity_prompts_once_then_reads(monkeypatch):
    """sudo needs password: one `sudo -v` prompt, then `sudo wdutil info`."""
    from wifimap import signal as sig_mod
    import subprocess
    monkeypatch.setitem(sys.modules, "CoreWLAN",
                        _make_mod(FakeInterface(ssid=None, bssid=None)))
    seen = []

    def _fake(cmd, **kw):
        seen.append(cmd)
        if cmd == ["sudo", "-n", "true"]:
            return subprocess.CompletedProcess(cmd, 1, "", "password required")
        if cmd == ["sudo", "-v"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        if cmd[:3] == ["sudo", "-n", "wdutil"]:
            return _run_ok("SSID: PromptNet\nBSSID: 00:11:22:33:44:55\n")
        raise AssertionError("unexpected cmd %r" % (cmd,))

    monkeypatch.setattr(subprocess, "run", _fake)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert sig_mod.read_network_identity() == ("PromptNet", "00:11:22:33:44:55")
    assert seen.count(["sudo", "-v"]) == 1


def test_identity_abort_returns_none(monkeypatch):
    from wifimap import signal as sig_mod
    import subprocess
    monkeypatch.setitem(sys.modules, "CoreWLAN",
                        _make_mod(FakeInterface(ssid=None, bssid=None)))

    def _fake(cmd, **kw):
        if cmd == ["sudo", "-n", "true"]:
            return subprocess.CompletedProcess(cmd, 1, "", "password required")
        if cmd == ["sudo", "-v"]:
            return subprocess.CompletedProcess(cmd, 1, "", "aborted")
        raise AssertionError("must not reach wdutil after abort: %r" % (cmd,))

    monkeypatch.setattr(subprocess, "run", _fake)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    assert sig_mod.read_network_identity() is None


def test_identity_no_sudo_no_tty_returns_none(monkeypatch):
    """Non-interactive (tests/daemons): no prompt, never hang."""
    from wifimap import signal as sig_mod
    import subprocess
    monkeypatch.setitem(sys.modules, "CoreWLAN", None)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)

    def _fake(cmd, **kw):
        if cmd == ["sudo", "-n", "true"]:
            return subprocess.CompletedProcess(cmd, 1, "", "password required")
        raise AssertionError("must not prompt without tty: %r" % (cmd,))

    monkeypatch.setattr(subprocess, "run", _fake)
    assert sig_mod.read_network_identity() is None


def test_identity_parse_failure_returns_none(monkeypatch):
    from wifimap import signal as sig_mod
    import subprocess
    monkeypatch.setitem(sys.modules, "CoreWLAN",
                        _make_mod(FakeInterface(ssid=None, bssid=None)))

    def _fake(cmd, **kw):
        if cmd == ["sudo", "-n", "true"]:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return _run_ok("usage: sudo wdutil info ...\n")

    monkeypatch.setattr(subprocess, "run", _fake)
    assert sig_mod.read_network_identity() is None


def test_walk_backfills_poll_with_session_identity(tmp_path):
    from wifimap import signal as sig_mod
    from wifimap import tui as tui_mod
    st = tui_mod.WalkState(str(tmp_path / "w.db"))
    assert st.ensure_identity(identity_fn=lambda: ("SessNet", "aa:bb:cc:00:11:22")) == (
        "SessNet", "aa:bb:cc:00:11:22")
    st.poll(read_fn=lambda: sig_mod.Signal(ssid=None, bssid=None, rssi=-60))
    assert st.sig.ssid == "SessNet"
    assert st.sig.bssid == "aa:bb:cc:00:11:22"
    # poll values win when present
    st.poll(read_fn=lambda: sig_mod.Signal(ssid="Real", bssid="ff", rssi=-50))
    assert st.sig.ssid == "Real"
    assert tui_mod.format_net_line(st.net_ssid) == "Net: SessNet"
    assert tui_mod.format_net_line(None) == "Net: unknown"


def test_walk_identity_abort_toasts_and_continues(tmp_path):
    from wifimap import tui as tui_mod
    st = tui_mod.WalkState(str(tmp_path / "w.db"))
    assert st.ensure_identity(identity_fn=lambda: None) is None
    assert "unknown" in st.toast.lower() or "skipped" in st.toast.lower()
    assert tui_mod.format_net_line(st.net_ssid) == "Net: unknown"


def test_scan_uses_selected_ssid_and_backfills_bssid(monkeypatch, tmp_path, capsys):
    from wifimap import signal as sig_mod
    from wifimap import ssid as ssid_mod
    from wifimap import store as store_mod
    from wifimap.cli import main
    db = str(tmp_path / "scan.db")
    monkeypatch.setattr(sig_mod, "sample_signal",
                        lambda *a, **k: sig_mod.Signal(ssid=None, bssid=None,
                                                       rssi=-60))
    monkeypatch.setattr(sig_mod, "read_network_identity",
                        lambda: ("ScanNet", "11:22:33:44:55:66"))
    original_selector = ssid_mod.select_ssid_line
    def _select(conn, location_id, detected_name=None, requested=None):
        assert detected_name == "ScanNet"
        assert requested is None
        selected_id = store_mod.resolve_ssid(conn, location_id, detected_name)
        return store_mod.get_ssid(conn, selected_id)
    monkeypatch.setattr(ssid_mod, "select_ssid_line", _select)
    rc = main(["--db", db, "scan", "--location", "lab", "--room", "R1",
               "--spot", "S1", "--no-speedtest"])
    assert rc == 0
    conn = store_mod.get_db(db)
    try:
        rows = store_mod.list_readings(conn)
        assert rows[0]["ssid"] == "ScanNet"
        assert rows[0]["bssid"] == "11:22:33:44:55:66"
    finally:
        conn.close()
    # Detection can fail without losing the explicit location SSID.
    db2 = str(tmp_path / "scan2.db")
    monkeypatch.setattr(
        sig_mod, "read_network_identity",
        lambda: (_ for _ in ()).throw(
            AssertionError("--ssid must skip automatic detection")),
    )
    monkeypatch.setattr(ssid_mod, "select_ssid_line", original_selector)
    rc = main(["--db", db2, "scan", "--location", "lab", "--room", "R1",
               "--spot", "S1", "--ssid", "ManualNet", "--no-speedtest"])
    capsys.readouterr()
    assert rc == 0
    conn = store_mod.get_db(db2)
    try:
        assert store_mod.list_readings(conn)[0]["ssid"] == "ManualNet"
    finally:
        conn.close()


# --- radio detail fields (mcs/band/security), display-only ---

def test_signal_radio_defaults_none():
    sig = Signal()
    assert sig.mcs is None and sig.band is None and sig.security is None


def test_security_map_and_fallback():
    from wifimap import signal as sig_mod
    assert sig_mod._security_name(0) == "open"
    assert sig_mod._security_name(1) == "WEP"
    assert sig_mod._security_name(2) == "WPA Personal"
    assert sig_mod._security_name(3) == "WPA/WPA2 Personal"
    assert sig_mod._security_name(4) == "WPA2 Personal"
    assert sig_mod._security_name(8) == "WPA3 Personal"
    assert sig_mod._security_name(99) == "sec(99)"
    assert sig_mod._security_name(None) is None


def test_band_map_and_unknown():
    from wifimap import signal as sig_mod

    class _Ch:
        def __init__(self, code):
            self._code = code

        def channelBand(self):
            return self._code

    assert sig_mod._band_name(_Ch(1)) == "2.4 GHz"
    assert sig_mod._band_name(_Ch(2)) == "5 GHz"
    assert sig_mod._band_name(_Ch(9)) is None
    assert sig_mod._band_name(None) is None
    assert sig_mod._band_name(object()) is None


def test_mcs_missing_api_gives_none():
    from wifimap import signal as sig_mod
    assert sig_mod._mcs_value(object()) is None

    class _M:
        def mcsIndex(self):
            return 9

    assert sig_mod._mcs_value(_M()) == 9

    class _Bad:
        def mcsIndex(self):
            raise RuntimeError("gone")

    assert sig_mod._mcs_value(_Bad()) is None


def test_read_signal_radio_fields(monkeypatch):
    from wifimap import signal as sig_mod

    class _Ch(FakeChannel):
        def channelBand(self):
            return 2

    class _Iface(FakeInterface):
        def mcsIndex(self):
            return 11

        def securityMode(self):
            return 8

        def wlanChannel(self):
            return _Ch()

    monkeypatch.setitem(sys.modules, "CoreWLAN", _make_mod(_Iface()))
    sig = read_signal()
    assert sig.mcs == 11
    assert sig.band == "5 GHz"
    assert sig.security == "WPA3 Personal"


def test_read_signal_radio_absent_stays_none(fake_wlan):
    sig = read_signal()
    assert sig.mcs is None and sig.band is None and sig.security is None


def test_profiler_fallback_radio_none(monkeypatch):
    from wifimap import signal as sig_mod
    import subprocess
    out = ("Current Wireless Network: X\nSignal / Noise: -60 dBm / -90 dBm\n"
           "Transmit Rate: 100\nPHY Mode: 802.11ax\nChannel: 36\n")
    monkeypatch.setattr(subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, out, ""))
    sig = sig_mod.read_signal_profiler()
    assert sig.mcs is None and sig.band is None and sig.security is None


# --- read_local_addrs (mocked subprocess, no live calls) ---

def test_read_local_addrs_full(monkeypatch):
    from wifimap import signal as sig_mod
    import subprocess

    def _fake(cmd, **kw):
        if cmd == ["ipconfig", "getifaddr", "en0"]:
            return subprocess.CompletedProcess(cmd, 0, "192.168.178.50\n", "")
        if cmd == ["ipconfig", "getoption", "en0", "router"]:
            return subprocess.CompletedProcess(cmd, 0, "192.168.178.1\n", "")
        raise AssertionError("unexpected cmd %r" % (cmd,))

    monkeypatch.setattr(subprocess, "run", _fake)
    monkeypatch.setattr(sig_mod, "_read_hw_address", lambda: "aa:bb:cc:dd:ee:ff")
    assert sig_mod.read_local_addrs() == (
        "192.168.178.50", "192.168.178.1", "aa:bb:cc:dd:ee:ff")


def test_read_local_addrs_partial_and_all_missing(monkeypatch):
    from wifimap import signal as sig_mod
    import subprocess

    def _fake(cmd, **kw):
        if cmd == ["ipconfig", "getifaddr", "en0"]:
            raise subprocess.TimeoutExpired(cmd, 5)
        if cmd == ["ipconfig", "getoption", "en0", "router"]:
            return subprocess.CompletedProcess(cmd, 0, "192.168.178.1\n", "")
        raise AssertionError("unexpected cmd %r" % (cmd,))

    monkeypatch.setattr(subprocess, "run", _fake)
    monkeypatch.setattr(sig_mod, "_read_hw_address", lambda: None)
    assert sig_mod.read_local_addrs() == (None, "192.168.178.1", None)
    monkeypatch.setattr(sig_mod, "_read_hw_address", lambda: None)

    def _fail(cmd, **kw):
        raise OSError("no ipconfig")

    monkeypatch.setattr(subprocess, "run", _fail)
    assert sig_mod.read_local_addrs() is None
