# Named walk comparison design

## Context

WiFi infrastructure work needs two related workflows: compare a complete
before/after survey after installation, and get immediate feedback while
placing or tuning equipment. Readings previously had place and timestamp data
but no reliable survey boundary, so inferring walks from time gaps would be
ambiguous.

## Decision

A `walk` is a first-class, location-scoped database entity with a human name,
start time, and end time. Saved readings carry an optional walk ID. Historical
readings remain readable and unassigned; they are never grouped by timestamp
implicitly.

Offline evaluation compares two walks at matching spots. Repeated readings are
reduced to a median per walk and spot. The UI shows the union of both walks so
new and not-revisited spots remain visible. Changes are always after minus
before; whether that is better depends on the metric (lower is better for
noise and ping, higher for the others).

Live comparison caches the selected prior walk's per-spot medians. Signal and
TX rate use a short rolling live window. Throughput uses an explicit Ookla
probe, either transient (`t`) or saved (`s`). Passive interface byte rates are
labelled traffic and are not compared with stored speed-test capacity.

## Consequences

- Before/after comparisons are reproducible and do not depend on timestamp
  heuristics.
- Walk names are convenient but not unique; ambiguous CLI names require an ID.
- A walk belongs to one location, even when evaluation began from an SSID.
- Existing databases migrate additively and preserve all readings.
- Speed tests remain variable; comparison detail exposes server, BSSID, and
  channel so operators can interpret changes.
