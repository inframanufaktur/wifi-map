//! Native WiFi, throughput, and path-quality probes.

mod corewlan;
mod path;
mod process;
mod radio;
mod throughput;
mod traffic;

pub use path::{
    PathSample, PathTracker, PathWorker, gateway, gateway_with_cancel, parse_ping_rtt, ping_once,
};
pub use process::{CancelToken, InterruptGuard, install_interrupt_handler};
pub use radio::{
    demo_signal, read_network_identity, read_network_identity_with_cancel, refresh_signal_identity,
    sample_signal, sample_signal_for, sample_signal_with_cancel,
};
pub use throughput::{run_speedtest, run_speedtest_diagnostics, run_speedtest_with_cancel};
pub use traffic::{TrafficSampler, parse_netstat_ib};

pub type Result<T> = std::result::Result<T, MeasureError>;

#[derive(Debug, thiserror::Error)]
pub enum MeasureError {
    #[error("{0}")]
    NoWifi(String),
    #[error("measurement backend unavailable: {0}")]
    Backend(String),
    #[error("Ookla speedtest is unavailable; install speedtest or use --no-speedtest")]
    SpeedtestUnavailable,
    #[error("speedtest failed: {0}")]
    SpeedtestFailed(String),
    #[error("measurement cancelled")]
    Cancelled,
}
