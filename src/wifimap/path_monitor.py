"""Continuous local-gateway and Internet path-quality probes."""
from __future__ import annotations

import ipaddress
import math
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from statistics import median
from typing import Callable, Deque, Optional, Tuple


PRIMARY_TARGET = "1.1.1.1"
CONFIRMATION_TARGET = "8.8.8.8"
_PING_TIME_RE = re.compile(r"time\s*([=<])\s*([0-9]+(?:\.[0-9]+)?)\s*ms")
_SPARK_CHARS = "▁▂▃▄▅▆▇"


def parse_ping_rtt(output: str) -> Optional[float]:
    """Extract the round-trip time from macOS ``ping`` output."""
    match = _PING_TIME_RE.search(output)
    if match is None:
        return None
    value = float(match.group(2))
    return value if match.group(1) == "=" else max(value, 1.0)


def ping_once(
    target: str,
    timeout: float = 1.0,
    runner: Callable = subprocess.run,
    executable: Optional[str] = None,
    platform: Optional[str] = None,
) -> Optional[float]:
    """Return one ICMP RTT in milliseconds, or ``None`` on no reply."""
    try:
        ipaddress.ip_address(target)
    except ValueError:
        return None
    binary = executable or shutil.which("ping") or "/sbin/ping"
    system = platform or sys.platform
    wait = (str(max(1, int(round(timeout * 1000))))
            if system == "darwin" else str(max(1, int(math.ceil(timeout)))))
    args = [binary, "-n", "-c", "1", "-W", wait, target]
    try:
        result = runner(
            args, capture_output=True, text=True, timeout=timeout + 0.5,
            check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return parse_ping_rtt((result.stdout or "") + (result.stderr or ""))


@dataclass(frozen=True)
class ProbeSummary:
    current_ms: Optional[float]
    median_ms: Optional[float]
    p95_ms: Optional[float]
    loss_pct: Optional[float]
    max_outage_ms: int
    count: int
    samples: Tuple[Optional[float], ...] = ()

    def sparkline(self, width: int) -> str:
        if width <= 0 or not self.samples:
            return ""
        samples = self.samples
        if len(samples) > width:
            buckets = []
            for index in range(width):
                start = index * len(samples) // width
                end = (index + 1) * len(samples) // width
                values = samples[start:max(start + 1, end)]
                buckets.append(
                    None if any(value is None for value in values)
                    else sum(value for value in values if value is not None)
                    / len(values))
            samples = tuple(buckets)
        scale = max(50.0, max(
            (value for value in samples if value is not None), default=1.0))
        def symbol(value: Optional[float]) -> str:
            if value is None:
                return "×"
            index = min(
                len(_SPARK_CHARS) - 1,
                max(0, int(value / scale * len(_SPARK_CHARS))),
            )
            return _SPARK_CHARS[index]

        rendered = "".join(symbol(value) for value in samples)
        return rendered.rjust(width)


class _ProbeWindow:
    def __init__(self, maxlen: int, interval: float) -> None:
        self.samples: Deque[Optional[float]] = deque(maxlen=max(1, maxlen))
        self.observed_at: Deque[float] = deque(maxlen=max(1, maxlen))
        self.interval = interval

    def reset(self) -> None:
        self.samples.clear()
        self.observed_at.clear()

    def append(self, value: Optional[float],
               observed_at: Optional[float] = None) -> None:
        self.samples.append(value)
        self.observed_at.append(
            time.monotonic() if observed_at is None else observed_at)

    def summary(self) -> ProbeSummary:
        values = [value for value in self.samples if value is not None]
        ordered = sorted(values)
        p95 = (ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]
               if ordered else None)
        longest = streak = 0.0
        previous_at: Optional[float] = None
        for value, observed_at in zip(self.samples, self.observed_at):
            elapsed = (observed_at - previous_at
                       if previous_at is not None else self.interval)
            if value is None:
                # Probe timeouts can make a nominal one-second cycle longer.
                # Count wall time without letting fast test/custom probes make
                # one confirmed miss shorter than one configured interval.
                streak += max(self.interval, elapsed)
            else:
                streak = 0.0
            longest = max(longest, streak)
            previous_at = observed_at
        count = len(self.samples)
        return ProbeSummary(
            current_ms=self.samples[-1] if self.samples else None,
            median_ms=float(median(values)) if values else None,
            p95_ms=p95,
            loss_pct=(100.0 * (count - len(values)) / count
                      if count else None),
            max_outage_ms=round(longest * 1000),
            count=count,
            samples=tuple(self.samples),
        )


@dataclass(frozen=True)
class PathSnapshot:
    gateway: ProbeSummary
    internet: ProbeSummary

    def reading_fields(self) -> dict:
        count = max(self.gateway.count, self.internet.count)
        return {
            "path_probe_count": count or None,
            "gateway_rtt_ms": self.gateway.median_ms,
            "gateway_p95_ms": self.gateway.p95_ms,
            "gateway_loss_pct": self.gateway.loss_pct,
            "gateway_max_outage_ms": (
                self.gateway.max_outage_ms if self.gateway.count else None),
            "internet_rtt_ms": self.internet.median_ms,
            "internet_p95_ms": self.internet.p95_ms,
            "internet_loss_pct": self.internet.loss_pct,
            "internet_max_outage_ms": (
                self.internet.max_outage_ms if self.internet.count else None),
        }


class PathTracker:
    """Spot-local rolling probe statistics."""

    def __init__(self, window_size: int = 60, interval: float = 1.0) -> None:
        self.gateway = _ProbeWindow(window_size, interval)
        self.internet = _ProbeWindow(window_size, interval)

    def reset(self) -> None:
        self.gateway.reset()
        self.internet.reset()

    def record_values(self, gateway_ms: Optional[float],
                      internet_ms: Optional[float],
                      observed_at: Optional[float] = None) -> None:
        now = time.monotonic() if observed_at is None else observed_at
        self.gateway.append(gateway_ms, now)
        self.internet.append(internet_ms, now)

    def record(self, sample: "PathSample") -> None:
        now = time.monotonic()
        if sample.gateway_target is not None:
            self.gateway.append(sample.gateway_ms, now)
        self.internet.append(sample.internet_ms, now)

    def snapshot(self) -> PathSnapshot:
        return PathSnapshot(self.gateway.summary(), self.internet.summary())


@dataclass(frozen=True)
class PathSample:
    gateway_target: Optional[str]
    gateway_ms: Optional[float]
    internet_target: str
    internet_ms: Optional[float]
    primary_failed: bool = False


class PathMonitor:
    """Run path probes in a stoppable daemon worker."""

    def __init__(
        self,
        gateway: Optional[str],
        on_sample: Callable[[PathSample], None],
        probe: Callable[[str], Optional[float]] = ping_once,
        interval: float = 1.0,
        primary: str = PRIMARY_TARGET,
        confirmation: str = CONFIRMATION_TARGET,
    ) -> None:
        self.gateway = gateway
        self.on_sample = on_sample
        self.probe = probe
        self.interval = max(0.1, interval)
        self.primary = primary
        self.confirmation = confirmation
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def sample_once(self, executor: Optional[Executor] = None) -> PathSample:
        owned = executor is None
        pool = executor or ThreadPoolExecutor(max_workers=2)
        try:
            gateway_future = (pool.submit(self.probe, self.gateway)
                              if self.gateway else None)
            internet_future = pool.submit(self.probe, self.primary)
            gateway_ms = (gateway_future.result()
                          if gateway_future is not None else None)
            internet_ms = internet_future.result()
            target = self.primary
            failed = internet_ms is None
            if failed:
                internet_ms = pool.submit(
                    self.probe, self.confirmation).result()
                if internet_ms is not None:
                    target = self.confirmation
            return PathSample(
                self.gateway, gateway_ms, target, internet_ms, failed)
        finally:
            if owned:
                pool.shutdown(wait=True)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        with ThreadPoolExecutor(max_workers=2) as pool:
            while not self._stop.is_set():
                started = time.monotonic()
                sample = self.sample_once(pool)
                try:
                    self.on_sample(sample)
                except Exception:  # noqa: BLE001 - monitor must survive UI state
                    pass
                remaining = self.interval - (time.monotonic() - started)
                if remaining > 0:
                    self._stop.wait(remaining)
