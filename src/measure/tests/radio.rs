use super::*;

#[test]
fn profiler_parses_signal_and_preserves_redacted_identity() {
    let sample = parse_profiler("Current Network Information:\n <redacted>:\n PHY Mode: 802.11ax\n Channel: 36 (5GHz, 80MHz)\n Signal / Noise: -60 dBm / -90 dBm\n Transmit Rate: 1200\n").unwrap();
    assert_eq!(sample.rssi, Some(-60.));
    assert_eq!(sample.noise, Some(-90.));
    assert_eq!(sample.snr, Some(30.));
    assert_eq!(sample.ssid, None);
    assert_eq!(sample.bssid, None);
    assert_eq!(sample.channel.as_deref(), Some("36 (5GHz, 80MHz)"));
}

#[test]
fn profiler_disconnected_is_typed_no_wifi() {
    assert!(matches!(
        parse_profiler("Wi-Fi: Off"),
        Err(MeasureError::NoWifi(_))
    ));
}

#[test]
fn signal_average_rounds_ties_even_and_keeps_last_association() {
    let first = Signal {
        rssi: Some(-59.),
        noise: Some(-89.),
        bssid: Some("aa:bb:cc:dd:ee:01".into()),
        ..Signal::default()
    };
    let last = Signal {
        rssi: Some(-60.),
        noise: None,
        bssid: Some("aa:bb:cc:dd:ee:02".into()),
        ..Signal::default()
    };
    let sample = average_signal_samples(&[first, last]);
    assert_eq!(sample.rssi, Some(-60.));
    assert_eq!(sample.noise, Some(-89.));
    assert_eq!(sample.snr, Some(29.));
    assert_eq!(sample.bssid.as_deref(), Some("aa:bb:cc:dd:ee:02"));
    assert_eq!(
        average_signal_samples(&[Signal::default()]),
        Signal::default()
    );
}

#[test]
fn identity_normalizes_and_rejects_redaction() {
    assert_eq!(
        parse_helper_identity(
            r#"{"status":"connected","ssid":"mesh-net","bssid":"60-8D-26-8D-CF-3D"}"#
        ),
        Some(("mesh-net".into(), Some("60:8d:26:8d:cf:3d".into())))
    );
    assert_eq!(
        parse_helper_identity(r#"{"status":"connected","ssid":"<redacted>"}"#),
        None
    );
    assert_eq!(
        parse_helper_identity(r#"{"status":"not_connected","ssid":"mesh-net"}"#),
        None
    );
}

#[test]
fn forced_identity_refresh_updates_roamed_ap_but_never_mixes_networks() {
    let mut sample = Signal {
        ssid: Some("mesh-net".into()),
        bssid: Some("aa:bb:cc:dd:ee:01".into()),
        ..Signal::default()
    };
    let current = ("mesh-net".into(), Some("aa:bb:cc:dd:ee:02".into()));
    apply_identity(&mut sample, current.clone(), false);
    assert_eq!(sample.bssid.as_deref(), Some("aa:bb:cc:dd:ee:01"));
    apply_identity(&mut sample, current, true);
    assert_eq!(sample.bssid.as_deref(), Some("aa:bb:cc:dd:ee:02"));
    apply_identity(
        &mut sample,
        ("other-net".into(), Some("aa:bb:cc:dd:ee:03".into())),
        true,
    );
    assert_eq!(sample.ssid.as_deref(), Some("mesh-net"));
    assert_eq!(sample.bssid.as_deref(), Some("aa:bb:cc:dd:ee:02"));
    apply_identity(&mut sample, ("mesh-net".into(), None), true);
    assert_eq!(sample.bssid.as_deref(), Some("aa:bb:cc:dd:ee:02"));
}

#[test]
fn invalid_ap_addresses_are_not_roaming_identities() {
    assert_eq!(clean_bssid("00:00:00:00:00:00"), None);
    assert_eq!(clean_bssid("FF-FF-FF-FF-FF-FF"), None);
}

#[test]
fn profiler_does_not_mistake_a_nearby_network_for_an_association() {
    assert!(matches!(
        parse_profiler(
            "Other Local Wi-Fi Networks:\n Neighbor:\n Signal / Noise: -40 dBm / -90 dBm"
        ),
        Err(MeasureError::NoWifi(_))
    ));
}

#[test]
fn networksetup_ssid_ignores_redacted_and_disconnected_outputs() {
    assert_eq!(
        parse_networksetup_ssid("Current Wi-Fi Network: mesh:floor 2"),
        Some("mesh:floor 2".into())
    );
    assert_eq!(
        parse_networksetup_ssid("Current AirPort Network: <redacted>"),
        None
    );
    assert_eq!(
        parse_networksetup_ssid("You are not associated with an AirPort network."),
        None
    );
}

#[test]
fn helper_version_selection_prefers_stable_over_matching_prerelease() {
    assert!(version_key("3.0.0") > version_key("3.0.0-alpha.1"));
    assert!(version_key("3.10.0") > version_key("3.9.0"));
}
