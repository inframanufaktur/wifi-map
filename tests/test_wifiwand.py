import json
import subprocess

from wifimap.wifiwand import (
    WifiWandIdentityProvider,
    find_helper,
    parse_identity,
)


def test_parse_connected_identity_normalizes_bssid():
    identity = parse_identity(json.dumps({
        "status": "connected",
        "ssid": "Radio Freies Moskau",
        "bssid": "60-8D-26-8D-CF-3D",
    }))

    assert identity is not None
    assert identity.ssid == "Radio Freies Moskau"
    assert identity.bssid == "60:8d:26:8d:cf:3d"


def test_parse_identity_requires_connected_ssid():
    assert parse_identity('{"status":"not_connected"}') is None
    assert parse_identity('{"status":"connected","ssid":"<redacted>"}') is None
    assert parse_identity("not json") is None


def test_provider_caches_success_for_one_second(tmp_path):
    helper = tmp_path / "wifiwand-helper"
    helper.touch(mode=0o755)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({
                "status": "connected",
                "ssid": "mesh-net",
                "bssid": "aa:bb:cc:dd:ee:ff",
            }),
            stderr="",
        )

    times = iter((10.0, 10.5, 11.1))
    provider = WifiWandIdentityProvider(
        helper_path=str(helper),
        run_fn=run,
        monotonic_fn=lambda: next(times),
        success_ttl=1.0,
    )

    first = provider.read()
    second = provider.read()
    third = provider.read()

    assert first == second == third
    assert calls == [
        [str(helper), "current-network"],
        [str(helper), "current-network"],
    ]


def test_provider_accepts_a_longer_cache_age_for_stable_signal(tmp_path):
    helper = tmp_path / "wifiwand-helper"
    helper.touch(mode=0o755)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 0,
            stdout='{"status":"connected","ssid":"mesh-net"}',
            stderr="",
        )

    times = iter((10.0, 19.9, 20.1))
    provider = WifiWandIdentityProvider(
        helper_path=str(helper), run_fn=run,
        monotonic_fn=lambda: next(times), success_ttl=1.0,
    )

    assert provider.read(max_age=10.0) is not None
    assert provider.read(max_age=10.0) is not None
    assert provider.read(max_age=10.0) is not None
    assert len(calls) == 2


def test_provider_caches_helper_failure(tmp_path):
    helper = tmp_path / "wifiwand-helper"
    helper.touch(mode=0o755)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 1,
            stdout='{"status":"permission_denied"}', stderr="denied",
        )

    provider = WifiWandIdentityProvider(
        helper_path=str(helper),
        run_fn=run,
        monotonic_fn=lambda: 20.0,
        failure_ttl=30.0,
    )

    assert provider.read() is None
    assert provider.read() is None
    assert calls == [[str(helper), "current-network"]]

    assert provider.read(force=True) is None
    assert calls == [
        [str(helper), "current-network"],
        [str(helper), "current-network"],
    ]


def test_provider_ignores_non_executable_helper(tmp_path):
    helper = tmp_path / "wifiwand-helper"
    helper.touch(mode=0o644)
    provider = WifiWandIdentityProvider(helper_path=str(helper))

    assert provider.read() is None


def test_find_helper_prefers_stable_release_over_matching_prerelease(
        tmp_path, monkeypatch):
    monkeypatch.delenv("WIFIMAP_WIFIWAND_HELPER", raising=False)
    monkeypatch.setattr("wifimap.wifiwand.shutil.which", lambda _name: None)
    for version in ("3.0.0-alpha.1", "3.0.0"):
        helper = (tmp_path / "Library" / "Application Support" / "WifiWand"
                  / version / "wifiwand-helper.app" / "Contents" / "MacOS"
                  / "wifiwand-helper")
        helper.parent.mkdir(parents=True)
        helper.touch(mode=0o755)

    selected = find_helper(home=tmp_path)

    assert selected is not None
    assert "/3.0.0/wifiwand-helper.app/" in selected
