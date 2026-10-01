//! Active WiFi signal and identity readers with optional helper fallback.

use super::process::run_command;
use super::{CancelToken, MeasureError, Result, corewlan};
use crate::model::Signal;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

pub(super) fn clean_ssid(value: &str) -> Option<String> {
    let value = value.trim().trim_matches(['\'', '"']);
    if value.is_empty() || ["<hidden>", "<redacted>"].contains(&value.to_lowercase().as_str()) {
        None
    } else {
        Some(value.to_string())
    }
}

pub(super) fn clean_bssid(value: &str) -> Option<String> {
    let value = value.trim().replace('-', ":").to_lowercase();
    let parts: Vec<_> = value.split(':').collect();
    if parts.len() == 6
        && value != "00:00:00:00:00:00"
        && value != "ff:ff:ff:ff:ff:ff"
        && parts
            .iter()
            .all(|p| p.len() == 2 && p.bytes().all(|b| b.is_ascii_hexdigit()))
    {
        Some(value)
    } else {
        None
    }
}

fn parse_helper_identity(text: &str) -> Option<(String, Option<String>)> {
    let value: serde_json::Value = serde_json::from_str(text).ok()?;
    if !matches!(value["status"].as_str(), Some("connected" | "ok")) {
        return None;
    }
    let ssid = clean_ssid(value["ssid"].as_str()?)?;
    let bssid = value["bssid"].as_str().and_then(clean_bssid);
    Some((ssid, bssid))
}

fn executable(path: &Path) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        path.is_file()
            && path
                .metadata()
                .is_ok_and(|m| m.permissions().mode() & 0o111 != 0)
    }
    #[cfg(not(unix))]
    path.is_file()
}

fn version_key(version: &str) -> (Vec<u64>, bool, String) {
    let end = version
        .find(|c: char| !c.is_ascii_digit() && c != '.')
        .unwrap_or(version.len());
    let mut numbers: Vec<_> = version[..end]
        .trim_end_matches('.')
        .split('.')
        .filter_map(|v| v.parse().ok())
        .collect();
    numbers.resize(numbers.len().max(4), 0);
    (numbers, end == version.len(), version.to_string())
}

fn find_helper() -> Option<PathBuf> {
    if let Some(explicit) = std::env::var_os("WIFIMAP_WIFIWAND_HELPER") {
        let path = PathBuf::from(explicit);
        let path = if let Ok(suffix) = path.strip_prefix("~") {
            PathBuf::from(std::env::var_os("HOME")?).join(suffix)
        } else {
            path
        };
        return executable(&path).then_some(path);
    }
    if let Some(path) = std::env::var_os("PATH") {
        for directory in std::env::split_paths(&path) {
            let helper = directory.join("wifiwand-helper");
            if executable(&helper) {
                return Some(helper);
            }
        }
    }
    let root =
        PathBuf::from(std::env::var_os("HOME")?).join("Library/Application Support/WifiWand");
    std::fs::read_dir(root)
        .ok()?
        .filter_map(|entry| entry.ok())
        .filter_map(|entry| {
            let helper = entry
                .path()
                .join("wifiwand-helper.app/Contents/MacOS/wifiwand-helper");
            executable(&helper).then(|| (version_key(&entry.file_name().to_string_lossy()), helper))
        })
        .max_by(|a, b| a.0.cmp(&b.0))
        .map(|(_, path)| path)
}

type Identity = (String, Option<String>);

#[derive(Default)]
struct IdentityCache {
    checked_at: Option<Instant>,
    identity: Option<Identity>,
}

fn helper_identity(max_age: Duration, force: bool, cancel: &CancelToken) -> Option<Identity> {
    static CACHE: OnceLock<Mutex<IdentityCache>> = OnceLock::new();
    let cache = CACHE.get_or_init(|| Mutex::new(IdentityCache::default()));
    if let Ok(cached) = cache.lock() {
        let ttl = if cached.identity.is_some() {
            max_age
        } else {
            Duration::from_secs(1)
        };
        if !force && cached.checked_at.is_some_and(|time| time.elapsed() < ttl) {
            return cached.identity.clone();
        }
    }
    let identity = find_helper().and_then(|path| {
        let output = run_command(
            path.to_str()?,
            &["current-network"],
            Duration::from_secs(3),
            cancel,
        )
        .ok()?;
        output
            .status
            .success()
            .then(|| parse_helper_identity(&output.stdout))
            .flatten()
    });
    if !cancel.is_cancelled()
        && let Ok(mut cached) = cache.lock()
    {
        cached.checked_at = Some(Instant::now());
        cached.identity = identity.clone();
    }
    identity
}

/// Read a single instantaneous radio sample. No command interpreters or runtime
/// language helpers are used; WifiWand, when installed, is a signed executable.
pub fn sample_signal() -> Result<Signal> {
    sample_signal_with_cancel(&CancelToken::default())
}

pub fn sample_signal_with_cancel(cancel: &CancelToken) -> Result<Signal> {
    cancel.check()?;
    let mut signal = match corewlan::signal() {
        Ok(signal) => signal,
        Err(error @ MeasureError::NoWifi(_)) => return Err(error),
        Err(_) => {
            let output = run_command(
                "/usr/sbin/system_profiler",
                &["SPAirPortDataType"],
                Duration::from_secs(30),
                cancel,
            )?;
            if !output.status.success() {
                return Err(MeasureError::Backend(format!(
                    "system_profiler failed: {}",
                    output.stderr.trim()
                )));
            }
            parse_profiler(&output.stdout)?
        }
    };
    if (signal.ssid.is_none() || signal.bssid.is_none())
        && let Some(identity) = helper_identity(Duration::from_secs(1), false, cancel)
    {
        apply_identity(&mut signal, identity, false);
    }
    cancel.check()?;
    Ok(signal)
}

/// Average RSSI and noise; retain the last association's other properties.
pub fn sample_signal_for(duration: Duration, cancel: &CancelToken) -> Result<Signal> {
    let start = Instant::now();
    let mut samples = vec![sample_signal_with_cancel(cancel)?];
    while start.elapsed() < duration {
        cancel.wait(Duration::from_millis(500).min(duration - start.elapsed().min(duration)));
        cancel.check()?;
        samples.push(sample_signal_with_cancel(cancel)?);
    }
    let mut signal = average_signal_samples(&samples);
    refresh_signal_identity(&mut signal, cancel)?;
    Ok(signal)
}

fn average_signal_samples(samples: &[Signal]) -> Signal {
    let average = |values: Vec<f64>| -> Option<f64> {
        (!values.is_empty())
            .then(|| (values.iter().sum::<f64>() / values.len() as f64).round_ties_even())
    };
    let rssi = average(samples.iter().filter_map(|s| s.rssi).collect());
    let noise = average(samples.iter().filter_map(|s| s.noise).collect());
    let mut signal = samples.last().cloned().unwrap_or_default();
    signal.rssi = rssi;
    signal.noise = noise;
    signal.snr = rssi.zip(noise).map(|(rssi, noise)| rssi - noise);
    signal
}

fn apply_identity(signal: &mut Signal, (ssid, bssid): Identity, refresh_bssid: bool) {
    if signal.ssid.is_none() || signal.ssid.as_ref() == Some(&ssid) {
        signal.ssid.get_or_insert(ssid);
        if bssid.is_some() && (refresh_bssid || signal.bssid.is_none()) {
            signal.bssid = bssid;
        }
    }
}

/// Force a helper refresh before saving, so live display caching cannot
/// attribute a reading to an access point used before a roam. Run on a worker.
pub fn refresh_signal_identity(signal: &mut Signal, cancel: &CancelToken) -> Result<()> {
    cancel.check()?;
    if let Some(identity) = helper_identity(Duration::ZERO, true, cancel) {
        apply_identity(signal, identity, true);
    }
    cancel.check()
}

pub fn read_network_identity() -> Result<(Option<String>, Option<String>)> {
    read_network_identity_with_cancel(&CancelToken::default())
}

pub fn read_network_identity_with_cancel(
    cancel: &CancelToken,
) -> Result<(Option<String>, Option<String>)> {
    cancel.check()?;
    let (mut ssid, mut bssid, interface) = corewlan::identity().unwrap_or_default();
    if (ssid.is_none() || bssid.is_none())
        && let Some((helper_ssid, helper_bssid)) =
            helper_identity(Duration::from_secs(1), false, cancel)
        && (ssid.is_none() || ssid.as_ref() == Some(&helper_ssid))
    {
        ssid = Some(helper_ssid);
        bssid = helper_bssid.or(bssid);
    }
    if ssid.is_none() {
        let interface = interface.or_else(|| wifi_interface(cancel));
        if let Some(interface) = interface
            && let Ok(output) = run_command(
                "/usr/sbin/networksetup",
                &["-getairportnetwork", &interface],
                Duration::from_secs(5),
                cancel,
            )
            && output.status.success()
        {
            ssid = parse_networksetup_ssid(&output.stdout);
        }
    }
    cancel.check()?;
    Ok((ssid, bssid))
}

pub(super) fn wifi_interface(cancel: &CancelToken) -> Option<String> {
    if let Ok((_, _, Some(interface))) = corewlan::identity() {
        return Some(interface);
    }
    let output = run_command(
        "/usr/sbin/networksetup",
        &["-listallhardwareports"],
        Duration::from_secs(5),
        cancel,
    )
    .ok()?;
    let mut wifi = false;
    for line in output.stdout.lines() {
        if let Some(port) = line.trim().strip_prefix("Hardware Port:") {
            wifi = matches!(port.trim(), "Wi-Fi" | "AirPort");
        } else if wifi && let Some(device) = line.trim().strip_prefix("Device:") {
            return Some(device.trim().into());
        }
    }
    None
}

fn parse_networksetup_ssid(text: &str) -> Option<String> {
    text.lines().find_map(|line| {
        let (label, value) = line.trim().split_once(':')?;
        matches!(
            label.trim(),
            "Current Wi-Fi Network" | "Current AirPort Network"
        )
        .then(|| clean_ssid(value))
        .flatten()
    })
}

fn parse_profiler(text: &str) -> Result<Signal> {
    let mut sample = Signal::default();
    let current = text
        .find("Current Network Information:")
        .or_else(|| text.find("Current Wireless Network:"));
    if current.is_none() && text.contains("Other Local Wi-Fi Networks:") {
        return Err(MeasureError::NoWifi("WiFi is not associated".into()));
    }
    let text = current.map_or(text, |index| &text[index..]);
    for line in text.lines() {
        let line = line.trim();
        if line.starts_with("Other Local Wi-Fi Networks:") {
            break;
        }
        if let Some(value) = line.strip_prefix("Signal / Noise:") {
            if let Some((rssi, noise)) = value.split_once('/') {
                sample.rssi = rssi.split_whitespace().next().and_then(|v| v.parse().ok());
                sample.noise = noise.split_whitespace().next().and_then(|v| v.parse().ok());
            }
        } else if let Some(value) = line.strip_prefix("Channel:") {
            sample.channel = Some(value.trim().into());
        } else if let Some(value) = line.strip_prefix("PHY Mode:") {
            sample.phy = Some(value.trim().into());
        } else if let Some(value) = line.strip_prefix("Transmit Rate:") {
            sample.tx_rate = value.split_whitespace().next().map(str::to_string);
        }
    }
    if sample
        .rssi
        .is_none_or(|rssi| !rssi.is_finite() || rssi >= 0.)
    {
        return Err(MeasureError::NoWifi(
            "no associated network in system_profiler output".into(),
        ));
    }
    sample.snr = sample
        .rssi
        .zip(sample.noise)
        .map(|(rssi, noise)| rssi - noise);
    Ok(sample)
}

pub fn demo_signal(tick: u64) -> Signal {
    let rssi = -55. - ((tick as f64 / 5.).sin() * 12.).round();
    Signal {
        ssid: Some("DEMO · Survey WiFi".into()),
        bssid: Some(
            if tick % 40 < 20 {
                "02:00:00:00:00:01"
            } else {
                "02:00:00:00:00:02"
            }
            .into(),
        ),
        rssi: Some(rssi),
        noise: Some(-92.),
        snr: Some(rssi + 92.),
        channel: Some("36 (80MHz)".into()),
        phy: Some("802.11ax".into()),
        tx_rate: Some("866.7".into()),
        mcs: Some(9),
        band: Some("5 GHz".into()),
        security: Some("WPA3 Personal".into()),
    }
}

#[cfg(test)]
#[path = "tests/radio.rs"]
mod tests;
