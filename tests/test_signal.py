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
                 rssi=-57, noise=-92, channel=None, rate=780.0, phy=5):
        self._ssid = ssid
        self._bssid = bssid
        self._rssi = rssi
        self._noise = noise
        self._channel = channel if channel is not None else FakeChannel()
        self._rate = rate
        self._phy = phy

    def ssid(self):
        return self._ssid

    def bssid(self):
        return self._bssid

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
