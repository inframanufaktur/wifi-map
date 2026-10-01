//! Native CoreWLAN bridge and platform fallback.

#[cfg(target_os = "macos")]
use super::radio::{clean_bssid, clean_ssid};
use super::{MeasureError, Result};
use crate::model::Signal;

pub(super) use backend::{identity, signal};

#[cfg(not(target_os = "macos"))]
mod backend {
    use super::*;
    pub fn signal() -> Result<Signal> {
        Err(MeasureError::Backend(
            "native WiFi sampling requires macOS".into(),
        ))
    }
    pub fn identity() -> Result<(Option<String>, Option<String>, Option<String>)> {
        Err(MeasureError::Backend("CoreWLAN requires macOS".into()))
    }
}

#[cfg(target_os = "macos")]
mod backend {
    use super::*;
    use std::ffi::{CStr, c_char, c_void};

    type Object = *mut c_void;
    type Selector = *mut c_void;

    // Sources: Apple's installed SDK CWInterface.h, CWWiFiClient.h and
    // CoreWLANTypes.h; https://developer.apple.com/documentation/corewlan.
    #[link(name = "CoreWLAN", kind = "framework")]
    unsafe extern "C" {}

    #[link(name = "Foundation", kind = "framework")]
    unsafe extern "C" {}

    #[link(name = "objc")]
    unsafe extern "C" {
        fn objc_getClass(name: *const c_char) -> Object;
        fn sel_registerName(name: *const c_char) -> Selector;
        fn objc_msgSend();
        fn objc_autoreleasePoolPush() -> Object;
        fn objc_autoreleasePoolPop(pool: Object);
    }

    struct Pool(Object);
    impl Pool {
        fn new() -> Self {
            // SAFETY: every thread may own an autorelease pool.
            Self(unsafe { objc_autoreleasePoolPush() })
        }
    }
    impl Drop for Pool {
        fn drop(&mut self) {
            // SAFETY: this pool is popped on its creating thread exactly once.
            unsafe { objc_autoreleasePoolPop(self.0) }
        }
    }

    unsafe fn object(receiver: Object, method: &CStr) -> Object {
        // SAFETY: callers use SDK methods with object return types, no args.
        let send: unsafe extern "C" fn(Object, Selector) -> Object =
            unsafe { std::mem::transmute(objc_msgSend as *const ()) };
        unsafe { send(receiver, sel_registerName(method.as_ptr())) }
    }

    unsafe fn integer(receiver: Object, method: &CStr) -> isize {
        // SAFETY: SDK NSInteger and enum returns have the isize ABI.
        let send: unsafe extern "C" fn(Object, Selector) -> isize =
            unsafe { std::mem::transmute(objc_msgSend as *const ()) };
        unsafe { send(receiver, sel_registerName(method.as_ptr())) }
    }

    unsafe fn number(receiver: Object, method: &CStr) -> f64 {
        // SAFETY: CWInterface.transmitRate returns double. Modern macOS supports
        // x86_64 and arm64, where objc_msgSend has the double-return ABI.
        let send: unsafe extern "C" fn(Object, Selector) -> f64 =
            unsafe { std::mem::transmute(objc_msgSend as *const ()) };
        unsafe { send(receiver, sel_registerName(method.as_ptr())) }
    }

    unsafe fn string(receiver: Object, method: &CStr) -> Option<String> {
        // SAFETY: the requested getter returns NSString or nil; UTF8String is
        // copied while the owning autorelease pool is still alive.
        let value = unsafe { object(receiver, method) };
        if value.is_null() {
            return None;
        }
        let utf8 = unsafe { object(value, c"UTF8String") } as *const c_char;
        if utf8.is_null() {
            return None;
        }
        Some(
            unsafe { CStr::from_ptr(utf8) }
                .to_string_lossy()
                .into_owned(),
        )
    }

    unsafe fn interface() -> Result<Object> {
        // SAFETY: class and method names are valid NUL-terminated constants;
        // returned pointers are borrowed within the caller's autorelease pool.
        let class = unsafe { objc_getClass(c"CWWiFiClient".as_ptr()) };
        if class.is_null() {
            return Err(MeasureError::Backend("CWWiFiClient is unavailable".into()));
        }
        let client = unsafe { object(class, c"sharedWiFiClient") };
        let interface = unsafe { object(client, c"interface") };
        if interface.is_null() {
            return Err(MeasureError::NoWifi("no WiFi interface (WiFi off?)".into()));
        }
        Ok(interface)
    }

    pub fn identity() -> Result<(Option<String>, Option<String>, Option<String>)> {
        let _pool = Pool::new();
        // SAFETY: all getters are documented CWInterface NSString methods.
        unsafe {
            let interface = interface()?;
            Ok((
                string(interface, c"ssid").as_deref().and_then(clean_ssid),
                string(interface, c"bssid").as_deref().and_then(clean_bssid),
                string(interface, c"interfaceName"),
            ))
        }
    }

    pub fn signal() -> Result<Signal> {
        let _pool = Pool::new();
        // SAFETY: CoreWLAN getters below match the installed SDK signatures.
        // Objects remain inside this thread's pool; only Rust values escape.
        unsafe {
            let interface = interface()?;
            let rssi = integer(interface, c"rssiValue") as f64;
            if rssi == 0. {
                return Err(MeasureError::NoWifi("WiFi is not associated".into()));
            }
            let noise = integer(interface, c"noiseMeasurement") as f64;
            let noise = (noise != 0.).then_some(noise);
            let rate = number(interface, c"transmitRate");
            let channel = object(interface, c"wlanChannel");
            let (channel_label, band) = if channel.is_null() {
                (None, None)
            } else {
                let number = integer(channel, c"channelNumber");
                let width = match integer(channel, c"channelWidth") {
                    1 => Some(20),
                    2 => Some(40),
                    3 => Some(80),
                    4 => Some(160),
                    _ => None,
                };
                let label = width.map_or_else(
                    || number.to_string(),
                    |width| format!("{number} ({width}MHz)"),
                );
                let band = match integer(channel, c"channelBand") {
                    1 => Some("2.4 GHz"),
                    2 => Some("5 GHz"),
                    3 => Some("6 GHz"),
                    _ => None,
                };
                (Some(label), band.map(str::to_string))
            };
            let phy = match integer(interface, c"activePHYMode") {
                0 => "none".into(),
                1 => "802.11a".into(),
                2 => "802.11b".into(),
                3 => "802.11g".into(),
                4 => "802.11n".into(),
                5 => "802.11ac".into(),
                6 => "802.11ax".into(),
                7 => "802.11be".into(),
                value => format!("unknown({value})"),
            };
            let security = match integer(interface, c"security") {
                0 => Some("open"),
                1 => Some("WEP"),
                2 => Some("WPA Personal"),
                3 => Some("WPA/WPA2 Personal"),
                4 => Some("WPA2 Personal"),
                5 => Some("Personal"),
                6 => Some("Dynamic WEP"),
                7 => Some("WPA Enterprise"),
                8 => Some("WPA/WPA2 Enterprise"),
                9 => Some("WPA2 Enterprise"),
                10 => Some("Enterprise"),
                11 => Some("WPA3 Personal"),
                12 => Some("WPA3 Enterprise"),
                13 => Some("WPA3 Transition"),
                14 => Some("OWE"),
                15 => Some("OWE Transition"),
                _ => None,
            };
            Ok(Signal {
                ssid: string(interface, c"ssid").as_deref().and_then(clean_ssid),
                bssid: string(interface, c"bssid").as_deref().and_then(clean_bssid),
                rssi: Some(rssi),
                noise,
                snr: noise.map(|noise| rssi - noise),
                channel: channel_label,
                phy: Some(phy),
                tx_rate: (rate.is_finite() && rate > 0.).then(|| rate.to_string()),
                // CoreWLAN's public API does not expose MCS index.
                mcs: None,
                band,
                security: security.map(str::to_string),
            })
        }
    }
}
