#!/usr/bin/env python3
"""Serialize wifimap's live documentation contracts as deterministic JSON."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from importlib import metadata
import json
from pathlib import Path
import re
import sys
from typing import Dict, Optional, Tuple


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = str(PROJECT_ROOT / "src")
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from wifimap import cli, eval_state, evaluation, walk_keys, walk_ui  # noqa: E402


@dataclass(frozen=True)
class PackageDoc:
    name: str
    version: str
    python_requires: str
    optional_dependencies: Dict[str, Tuple[str, ...]]


@dataclass(frozen=True)
class OptionDoc:
    flags: Tuple[str, ...]
    destination: str
    required: bool
    kind: str
    choices: Optional[Tuple[object, ...]]
    default: object
    metavar: object
    help: str


@dataclass(frozen=True)
class CommandDoc:
    path: Tuple[str, ...]
    summary: str
    options: Tuple[OptionDoc, ...]


@dataclass(frozen=True)
class CliDoc:
    program: str
    summary: str
    epilog: str
    commands: Tuple[CommandDoc, ...]


@dataclass(frozen=True)
class ControlDoc:
    name: str
    key: str


@dataclass(frozen=True)
class StatusThresholdDoc:
    metric: str
    unit: str
    great_min: int
    ok_min: int


@dataclass(frozen=True)
class WalkDoc:
    footer: str
    controls: Tuple[ControlDoc, ...]
    statuses: Tuple[str, ...]
    status_thresholds: Tuple[StatusThresholdDoc, ...]
    chart_glyphs: Tuple[str, ...]


@dataclass(frozen=True)
class EvaluationKindDoc:
    key: str
    label: str


@dataclass(frozen=True)
class MetricDoc:
    key: str
    label: str
    unit: str
    higher_is_better: bool
    decimals: int


@dataclass(frozen=True)
class EvaluationDoc:
    kinds: Tuple[EvaluationKindDoc, ...]
    metrics: Tuple[MetricDoc, ...]
    comparison_metrics: Tuple[MetricDoc, ...]


@dataclass(frozen=True)
class CapabilityDoc:
    package: PackageDoc
    cli: CliDoc
    walk: WalkDoc
    evaluation: EvaluationDoc


_EXTRA_MARKER = re.compile(r"\bextra\s*==\s*(['\"])([^'\"]+)\1")


def package_doc(distribution_name: str = "wifimap") -> PackageDoc:
    """Read the installed distribution contract used by package consumers."""
    package_metadata = metadata.metadata(distribution_name)
    optional: Dict[str, list[str]] = {}
    for requirement in metadata.requires(distribution_name) or ():
        marker = _EXTRA_MARKER.search(requirement)
        if marker is None:
            continue
        extra = marker.group(2)
        dependency = requirement.partition(";")[0].strip()
        optional.setdefault(extra, []).append(dependency)
    return PackageDoc(
        name=package_metadata["Name"],
        version=package_metadata["Version"],
        python_requires=package_metadata["Requires-Python"],
        optional_dependencies={
            extra: tuple(sorted(requirements))
            for extra, requirements in sorted(optional.items())
        },
    )


def _normalized_value(value: object) -> object:
    if value == argparse.SUPPRESS:
        return None
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        root = str(PROJECT_ROOT)
        if value == root:
            return "<project>"
        prefix = root + "/"
        if value.startswith(prefix):
            return "<project>/" + value[len(prefix):]
        return value
    if isinstance(value, (tuple, list)):
        return tuple(_normalized_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def _option_docs(parser: argparse.ArgumentParser) -> Tuple[OptionDoc, ...]:
    options = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            continue
        if action.help == argparse.SUPPRESS:
            continue
        choices = (None if action.choices is None else
                   tuple(_normalized_value(choice)
                         for choice in action.choices))
        options.append(OptionDoc(
            flags=tuple(action.option_strings),
            destination=action.dest,
            required=action.required,
            kind="flag" if action.nargs == 0 else "value",
            choices=choices,
            default=_normalized_value(action.default),
            metavar=_normalized_value(action.metavar),
            help=action.help or "",
        ))
    return tuple(options)


def _subparser_summaries(action: argparse._SubParsersAction) -> Dict[str, str]:
    return {
        choice.dest: choice.help
        for choice in action._choices_actions
    }


def command_docs(parser: argparse.ArgumentParser,
                 path: Tuple[str, ...] = (),
                 summary: Optional[str] = None) -> Tuple[CommandDoc, ...]:
    """Return a deterministic, presentation-neutral command tree."""
    current = CommandDoc(
        path=path,
        summary=summary if summary is not None else (parser.description or ""),
        options=_option_docs(parser),
    )
    descendants = []
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        summaries = _subparser_summaries(action)
        for name, child in action.choices.items():
            descendants.extend(command_docs(
                child, path + (name,), summaries.get(name, "")))
    return (current,) + tuple(descendants)


def cli_doc() -> CliDoc:
    parser = cli._build_parser()
    return CliDoc(
        program=parser.prog,
        summary=parser.description or "",
        epilog=parser.epilog or "",
        commands=command_docs(parser),
    )


def _walk_thresholds() -> Tuple[StatusThresholdDoc, ...]:
    rssi = walk_ui.RSSI_RATING_THRESHOLDS
    snr = walk_ui.SNR_RATING_THRESHOLDS
    return (
        StatusThresholdDoc("rssi", "dBm", rssi[0], rssi[1]),
        StatusThresholdDoc("snr", "dB", snr[0], snr[1]),
    )


def walk_doc() -> WalkDoc:
    controls = (
        ControlDoc("snapshot", walk_keys.KEY_SNAPSHOT),
        ControlDoc("speed_probe", walk_keys.KEY_SPEED_PROBE),
        ControlDoc("compare", walk_keys.KEY_COMPARE),
        ControlDoc("benchmark", walk_keys.KEY_BENCHMARK),
        ControlDoc("switch", walk_keys.KEY_SWITCH),
        ControlDoc("new", walk_keys.KEY_NEW),
        ControlDoc("quit", walk_keys.KEY_QUIT),
    )
    return WalkDoc(
        footer=walk_ui.format_walk_footer(),
        controls=controls,
        statuses=tuple(walk_ui._RATING_STYLE),
        status_thresholds=_walk_thresholds(),
        chart_glyphs=tuple(walk_ui._SPARK_CHARS),
    )


def _metric_doc(metric: evaluation.MetricSpec) -> MetricDoc:
    return MetricDoc(
        key=metric.key,
        label=metric.label,
        unit=metric.unit,
        higher_is_better=metric.higher_is_better,
        decimals=metric.decimals,
    )


def evaluation_doc() -> EvaluationDoc:
    return EvaluationDoc(
        kinds=tuple(EvaluationKindDoc(key, label)
                    for key, label in eval_state.KIND_OPTIONS),
        metrics=tuple(_metric_doc(metric) for metric in evaluation.METRICS),
        comparison_metrics=tuple(
            _metric_doc(metric) for metric in evaluation.COMPARISON_METRICS),
    )


def capability_doc() -> CapabilityDoc:
    return CapabilityDoc(
        package=package_doc(),
        cli=cli_doc(),
        walk=walk_doc(),
        evaluation=evaluation_doc(),
    )


def main() -> None:
    json.dump(
        asdict(capability_doc()), sys.stdout, ensure_ascii=False,
        separators=(",", ":"),
    )
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
