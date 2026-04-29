#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
import sys
import tomllib
from pathlib import Path


ASSET_NAMES = ["boot.py", "templates", "clients", "tests"]


def parse_component(value: str) -> tuple[str, str]:
    bundle, sep, component = value.partition(":")
    if not sep or not bundle or not component:
        raise argparse.ArgumentTypeError("components must be BUNDLE:COMPONENT")
    return bundle, component


def parse_source(value: str) -> tuple[str, str]:
    alias, sep, source = value.partition("=")
    if not sep or not alias or not source:
        raise argparse.ArgumentTypeError("sources must be ALIAS=SOURCE")
    return alias, source


def toml_quote(value: str) -> str:
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def toml_array(values: list[str]) -> str:
    return "[" + ", ".join(toml_quote(v) for v in values) + "]"


def ordered_unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def copy_assets(src: Path, dst: Path) -> None:
    for name in ASSET_NAMES:
        source = src / name
        target = dst / name
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(source, target)


def merge_manifest(paths: list[Path]) -> dict:
    merged: dict = {"sources": {}, "sets": {}, "bundles": {}, "suites": {}}
    for path in paths:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for key in ("sources", "sets", "bundles", "suites"):
            merged[key].update(data.get(key, {}))
    return merged


def build_config(args: argparse.Namespace, manifest: dict) -> tuple[list[str], dict[str, list[str]]]:
    sets = manifest.get("sets", {})
    bundles = manifest.get("bundles", {})
    if args.all:
        set_name = "all"
    else:
        set_name = args.set
    if set_name not in sets:
        raise SystemExit(f"unknown testbed set {set_name!r}; available: {', '.join(sorted(sets))}")

    selected = list(sets[set_name])
    selected.extend(args.bundle)
    components: dict[str, list[str]] = {}
    for bundle, component in args.component:
        selected.append(bundle)
        components.setdefault(bundle, []).append(component)
    selected = [name for name in ordered_unique(selected) if name not in set(args.without)]

    for name in selected:
        if name not in bundles:
            raise SystemExit(f"unknown testbed bundle {name!r}; available: {', '.join(sorted(bundles))}")
    return selected, {k: ordered_unique(v) for k, v in components.items() if k in selected}


def write_distro(output: Path, manifest: dict, selected: list[str], components: dict[str, list[str]], source_overrides: dict[str, str]) -> None:
    bundles = manifest.get("bundles", {})
    sources = {name: dict(data) for name, data in manifest.get("sources", {}).items()}
    for alias, source in source_overrides.items():
        sources.setdefault(alias, {})["source"] = source

    lines = [
        "[distro]",
        'name = "testbed"',
        'version = "0.1.0"',
        "",
        "[requires]",
        'kernel = ">=0.9.0,<1.0.0"',
        "",
    ]
    for alias in sorted(sources):
        source = sources[alias].get("source", "")
        if not source:
            raise SystemExit(f"missing [sources.{alias}].source")
        lines.extend([f"[sources.{alias}]", f"source = {toml_quote(source)}", ""])
    for name in selected:
        entry = bundles[name]
        lines.extend([
            "[[bundles]]",
            f"name = {toml_quote(name)}",
            f"source = {toml_quote(entry['source'])}",
        ])
        if components.get(name):
            lines.append(f"components = {toml_array(components[name])}")
        lines.append("")
    (output / "distro.toml").write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a concrete testbed distro from bundle selections")
    parser.add_argument("--manifest", action="append", default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--set", default="baseline")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--bundle", action="append", default=[])
    parser.add_argument("--without", action="append", default=[])
    parser.add_argument("--component", action="append", default=[], type=parse_component)
    parser.add_argument("--source", action="append", default=[], type=parse_source, help="Override source alias: ALIAS=SOURCE")
    args = parser.parse_args(argv)

    src = Path(__file__).resolve().parent
    manifest_paths = args.manifest or ["testbed.toml"]
    resolved_manifests: list[Path] = []
    for raw in manifest_paths:
        path = Path(raw)
        if not path.is_absolute():
            path = src / path
        resolved_manifests.append(path)
    manifest = merge_manifest(resolved_manifests)
    source_overrides = dict(args.source)

    selected, components = build_config(args, manifest)
    output = Path(args.output).resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    copy_assets(src, output)
    write_distro(output, manifest, selected, components, source_overrides)
    print("generated testbed distro", output)
    print("bundles", ",".join(selected))
    if components:
        print("components", ",".join(f"{k}:{'|'.join(v)}" for k, v in sorted(components.items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
