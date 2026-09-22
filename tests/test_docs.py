"""Contracts for the generated documentation capability adapter."""
from __future__ import annotations

import argparse
from html.parser import HTMLParser
import importlib.util
import json
from dataclasses import asdict
from importlib import metadata
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Optional
from urllib.parse import unquote, urlsplit

import pytest

from wifimap import cli, eval_state, evaluation, walk_keys, walk_ui
from wifimap.cli_common import _EXPORT_FIELDS


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTRACTOR = PROJECT_ROOT / "docs" / "extract_capabilities.py"
SITE_ROOT = PROJECT_ROOT / "docs" / "_site"
EXPECTED_PAGES = (
    "index.html",
    "quick-start/index.html",
    "walk-tui/index.html",
    "evaluate/index.html",
    "cli-reference/index.html",
    "data-troubleshooting/index.html",
)


def _extract() -> tuple[bytes, dict]:
    raw = subprocess.check_output([sys.executable, str(EXTRACTOR)])
    return raw, json.loads(raw)


def _command(document: dict, *path: str) -> dict:
    return next(
        command for command in document["cli"]["commands"]
        if command["path"] == list(path)
    )


def _option(command: dict, flag: str) -> dict:
    return next(option for option in command["options"]
                if flag in option["flags"])


def test_extractor_writes_deterministic_json_only() -> None:
    first_raw, first = _extract()
    second_raw, second = _extract()

    assert first_raw == second_raw
    assert first_raw.endswith(b"\n")
    assert first == second
    assert list(first) == ["package", "cli", "walk", "evaluation"]
    assert str(PROJECT_ROOT).encode() not in first_raw
    assert "generated_at" not in first
    assert "extracted_at" not in first


def test_cli_contract_includes_nested_commands_and_option_semantics() -> None:
    _raw, document = _extract()

    def parser_paths(parser: argparse.ArgumentParser,
                     path: tuple[str, ...] = ()) -> list[list[str]]:
        paths = [list(path)]
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                for name, child in action.choices.items():
                    paths.extend(parser_paths(child, path + (name,)))
        return paths

    assert [command["path"] for command in document["cli"]["commands"]] == (
        parser_paths(cli._build_parser()))

    assert _command(document)["summary"] == "Map and evaluate home WiFi."
    assert _command(document, "rooms")["summary"] == "Rooms CRUD."
    room_add = _command(document, "rooms", "add")
    assert room_add["summary"] == "Create a room."

    location = _option(room_add, "--location")
    assert location == {
        "flags": ["--location"],
        "destination": "location",
        "required": True,
        "kind": "value",
        "choices": None,
        "default": None,
        "metavar": None,
        "help": "Location ID|NAME",
    }

    outdoors = _option(room_add, "--outdoors")
    assert outdoors["kind"] == "flag"
    assert outdoors["required"] is False
    assert outdoors["default"] is False

    scan = _command(document, "scan")
    room_outdoors = _option(scan, "--room-outdoors")
    assert room_outdoors["choices"] == [0, 1]
    assert room_outdoors["default"] == 0

    database = _option(_command(document), "--db")
    assert database["default"] == "<project>/db/wifi-map.db"
    assert database["help"] == (
        "SQLite DB path (default <project>/db/wifi-map.db)")


def test_walk_contract_comes_from_live_keys_footer_ratings_and_glyphs() -> None:
    _raw, document = _extract()
    walk = document["walk"]

    assert walk["footer"] == walk_ui.format_walk_footer()
    assert walk["controls"] == [
        {"name": "snapshot", "key": walk_keys.KEY_SNAPSHOT},
        {"name": "speed_probe", "key": walk_keys.KEY_SPEED_PROBE},
        {"name": "compare", "key": walk_keys.KEY_COMPARE},
        {"name": "benchmark", "key": walk_keys.KEY_BENCHMARK},
        {"name": "switch", "key": walk_keys.KEY_SWITCH},
        {"name": "new", "key": walk_keys.KEY_NEW},
        {"name": "quit", "key": walk_keys.KEY_QUIT},
    ]
    assert walk["statuses"] == ["GREAT", "OK", "WEAK", "UNKNOWN"]
    assert walk["status_thresholds"] == [
        {"metric": "rssi", "unit": "dBm", "great_min": -60,
         "ok_min": -70},
        {"metric": "snr", "unit": "dB", "great_min": 25,
         "ok_min": 15},
    ]
    assert walk["chart_glyphs"] == list(walk_ui._SPARK_CHARS)


def test_evaluation_contract_comes_from_live_kinds_and_metrics() -> None:
    _raw, document = _extract()
    extracted = document["evaluation"]

    assert extracted["kinds"] == [
        {"key": key, "label": label}
        for key, label in eval_state.KIND_OPTIONS
    ]
    assert extracted["metrics"] == [asdict(metric)
                                    for metric in evaluation.METRICS]
    assert extracted["comparison_metrics"] == [
        asdict(metric) for metric in evaluation.COMPARISON_METRICS
    ]


def test_package_contract_comes_from_installed_distribution_metadata() -> None:
    _raw, document = _extract()
    package = document["package"]
    installed = metadata.metadata("wifimap")

    assert package == {
        "name": installed["Name"],
        "version": installed["Version"],
        "python_requires": installed["Requires-Python"],
        "optional_dependencies": {"test": ["pytest"]},
    }


def test_command_docs_omit_hidden_parser_actions() -> None:
    spec = importlib.util.spec_from_file_location(
        "docs_extract_capabilities", EXTRACTOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--visible", help="Shown")
    parser.add_argument("--internal", help=argparse.SUPPRESS)

    [command] = module.command_docs(parser)

    assert [option.flags for option in command.options] == [("--visible",)]


class _DocumentParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.ids: set[str] = set()
        self.links: list[str] = []
        self.images: list[tuple[str, Optional[str]]] = []
        self.headings: list[int] = []
        self.landmarks: set[str] = set()
        self.text: list[str] = []

    def handle_starttag(self, tag: str,
                        attrs: list[tuple[str, Optional[str]]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"] or "")
        if tag == "a" and values.get("href"):
            self.links.append(values["href"] or "")
        if tag == "img" and values.get("src"):
            self.images.append((values["src"] or "", values.get("alt")))
        if tag in {"main", "nav", "header", "footer"}:
            self.landmarks.add(tag)
        if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            self.headings.append(int(tag[1]))

    def handle_data(self, data: str) -> None:
        self.text.append(data)


def _build_site() -> dict[str, bytes]:
    shutil.rmtree(SITE_ROOT, ignore_errors=True)
    subprocess.run(
        ["npm", "run", "docs:build"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return {
        str(path.relative_to(SITE_ROOT)): path.read_bytes()
        for path in sorted(SITE_ROOT.rglob("*"))
        if path.is_file()
    }


@pytest.fixture(scope="module")
def generated_site() -> dict[str, bytes]:
    first = _build_site()
    second = _build_site()
    assert first == second
    return second


def test_generated_site_has_all_pages_assets_and_stable_toolchain(
        generated_site: dict[str, bytes]) -> None:
    assert set(EXPECTED_PAGES).issubset(generated_site)
    assert {
        "assets/favicon.svg",
        "assets/inframanufaktur-logo.svg",
        "assets/site.css",
        "assets/site.js",
    }.issubset(generated_site)

    package = json.loads((PROJECT_ROOT / "package.json").read_text())
    lock = json.loads((PROJECT_ROOT / "package-lock.json").read_text())
    assert package["devDependencies"]["@11ty/eleventy"] == "3.1.6"
    assert lock["packages"][""]["devDependencies"]["@11ty/eleventy"] == (
        "3.1.6")
    assert (PROJECT_ROOT / ".nvmrc").read_text().strip() == "26"


def test_keyboard_reference_rows_align_both_columns(
        generated_site: dict[str, bytes]) -> None:
    css = generated_site["assets/site.css"].decode()
    walk_html = generated_site["walk-tui/index.html"].decode()

    assert ".key-reference__item {\n  align-items: start;\n}" in css
    assert "<dd></p>" not in walk_html


def test_visual_system_uses_sparse_lines_and_high_contrast_nav_focus(
        generated_site: dict[str, bytes]) -> None:
    css = generated_site["assets/site.css"].decode()

    def rule(selector: str) -> str:
        return css.split(f"{selector} {{", 1)[1].split("}", 1)[0]

    assert "background-image" not in rule("body")
    assert "border-bottom" not in rule("h2")
    assert "border-bottom" not in rule(
        ".key-reference__item,\n.reference-list > div,\n.option-metadata > div"
    )
    assert "border-top" not in rule(".generated-reference__sample")
    assert "border-top" not in rule(".option-reference")
    assert "border" not in rule("th,\ntd")

    nav_focus = rule(".site-nav a:focus-visible")
    assert "outline: 0.2rem solid var(--surface-inset);" in nav_focus
    assert "background: var(--focus);" in nav_focus
    assert "color: var(--surface-inset);" in nav_focus


def test_generated_pages_have_accessible_structure_and_live_references(
        generated_site: dict[str, bytes]) -> None:
    _raw, capabilities = _extract()

    for relative in EXPECTED_PAGES:
        document = generated_site[relative].decode()
        parsed = _DocumentParser()
        parsed.feed(document)
        assert parsed.headings.count(1) == 1, relative
        assert all(
            current <= previous + 1
            for previous, current in zip(parsed.headings, parsed.headings[1:])
        ), relative
        assert {"header", "nav", "main", "footer"} <= parsed.landmarks
        assert str(PROJECT_ROOT) not in document
        assert "https://www.inframanufaktur.org/" in parsed.links
        assert (
            "/assets/inframanufaktur-logo.svg", ""
        ) in parsed.images
        assert "Cyber Design Inframanufaktur" in document
        assert (
            "Geheimorganisation zur Verknotung von Netzwerkkabeln"
            in document
        )
        assert document.count("<table>") == document.count(
            'class="table-scroll"'
        ), relative

    cli_html = generated_site["cli-reference/index.html"].decode()
    for command in capabilities["cli"]["commands"]:
        command_name = "wifimap " + " ".join(command["path"])
        assert command_name.strip() in cli_html

    walk_html = generated_site["walk-tui/index.html"].decode()
    for control in capabilities["walk"]["controls"]:
        assert control["key"] in walk_html

    evaluate_html = generated_site["evaluate/index.html"].decode()
    for metric in capabilities["evaluation"]["metrics"]:
        assert metric["label"] in evaluate_html

    data_html = generated_site["data-troubleshooting/index.html"].decode()
    assert ",".join(_EXPORT_FIELDS) in data_html


def test_generated_site_internal_links_and_fragments_resolve(
        generated_site: dict[str, bytes]) -> None:
    parsed_documents: dict[str, _DocumentParser] = {}
    for relative in EXPECTED_PAGES:
        parsed = _DocumentParser()
        parsed.feed(generated_site[relative].decode())
        parsed_documents[relative] = parsed

    for source, parsed in parsed_documents.items():
        for href in parsed.links:
            url = urlsplit(href)
            if url.scheme or url.netloc or href.startswith(("mailto:", "#")):
                if href.startswith("#"):
                    assert href[1:] in parsed.ids, (source, href)
                continue
            target = unquote(url.path)
            if target.startswith("/"):
                target = target.lstrip("/")
            else:
                target = str((Path(source).parent / target))
            if not target or target.endswith("/"):
                target += "index.html"
            target = str(Path(target))
            assert target in generated_site, (source, href, target)
            if url.fragment and target in parsed_documents:
                assert url.fragment in parsed_documents[target].ids, (
                    source, href)


def test_reader_copy_avoids_documentation_implementation_details(
        generated_site: dict[str, bytes]) -> None:
    unwanted_phrases = (
        "built for repeatable decisions",
        "chart glyphs",
        "color reinforces these labels",
        "continue with the",
        "extracted from the live",
        "generated command reference",
        "generated from",
        "generated parser epilog",
        "generated reference",
        "live reference data",
        "one route from question to answer",
        "source-backed csv reference",
        "source-derived",
    )

    for relative in EXPECTED_PAGES:
        parsed = _DocumentParser()
        parsed.feed(generated_site[relative].decode())
        reader_copy = " ".join(parsed.text).lower()
        for phrase in unwanted_phrases:
            assert phrase not in reader_copy, (relative, phrase)


def test_templates_escape_capability_values_and_site_output_is_ignored() -> None:
    config = (PROJECT_ROOT / "eleventy.config.js").read_text()
    assert "autoescape: true" in config

    templates = list((PROJECT_ROOT / "docs" / "src").rglob("*.njk"))
    templates.extend((PROJECT_ROOT / "docs" / "src").glob("*.md"))
    for template in templates:
        source = template.read_text()
        assert "capabilities | safe" not in source
        assert "capabilities|safe" not in source

    ignored = subprocess.run(
        ["git", "check-ignore", "docs/_site/index.html"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert ignored.returncode == 0


def test_readme_is_a_compact_project_entry_point() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text()

    assert len(readme.splitlines()) <= 80
    for required in (
        "macOS",
        "Python 3.9+",
        "wifimap scan",
        "wifimap walk",
        "wifimap eval",
        "npm ci",
        "npm run docs:build",
        "npm run docs:serve",
        "docs/src",
        "https://github.com/inframanufaktur/wifi-map",
        "Production site",
    ):
        assert required in readme


def test_docs_workflow_verifies_before_secret_isolated_deployment() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "docs.yml")
    source = workflow.read_text()
    verify, deploy = source.split("\n  deploy:\n", 1)

    assert "pull_request:" in verify and "branches: [main]" in verify
    assert "push:" in verify and "workflow_dispatch:" in verify
    assert "actions/checkout@v7" in source
    assert "actions/setup-python@v7" in source
    assert "python-version: \"3.9\"" in source
    assert "actions/setup-node@v7" in source
    assert "node-version-file: .nvmrc" in source
    assert "npm ci" in verify
    assert "python -m pip install -e \".[test]\"" in verify
    assert "pytest" in verify
    assert "npm run docs:build" in verify
    assert "pytest tests/test_docs.py" in verify
    assert "secrets." not in verify

    assert "needs: verify" in deploy
    assert "environment: production" in deploy
    assert "group: docs-production" in deploy
    assert "cancel-in-progress: true" in deploy
    assert "github.event_name != 'pull_request'" in deploy
    assert "github.ref == 'refs/heads/main'" in deploy
    for secret in (
        "UBERSPACE_SSH_KEY",
        "UBERSPACE_HOST",
        "UBERSPACE_SSH_USER",
        "UBERSPACE_SSH_KNOWN_HOSTS",
    ):
        assert "secrets.%s" % secret in deploy
    assert "rsync -avz --delete --chmod=D755,F644" in deploy
    assert "/var/www/virtual/$UBERSPACE_SSH_USER/html/" in deploy
