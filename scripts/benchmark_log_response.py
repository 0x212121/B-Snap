"""Benchmark the production get_log_content function without app/DB startup.

Run: python scripts/benchmark_log_response.py --input logs/snapshot.log
Only the response constructor changes between variants. Function timings include
file read, parsing, filtering, and serialization; HTTP/auth/network are excluded.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import os
import platform
import re
import tempfile

from collections.abc import Awaitable, Callable
from importlib.metadata import version
from pathlib import Path
from statistics import median
from time import perf_counter_ns
from typing import Any

from fastapi import Depends, HTTPException, Query
from fastapi.responses import JSONResponse, ORJSONResponse

ROOT = Path(__file__).resolve().parents[1]


def load_function(log_directory: Path) -> tuple[dict, object]:
    """Compile the unchanged production parser and handler in an isolated namespace."""
    source = (ROOT / "app/routes/logs.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)) and node.name in {
            "LogEntry",
            "get_log_content",
        }:
            node.decorator_list = []
            nodes.append(node)
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    namespace = {
        "LOG_DIR": log_directory,
        "LOG_TYPES": ["main", "snapshot", "healthcheck", "scheduler", "management"],
        "LOG_COLORS": {
            "ERROR": "red",
            "WARNING": "yellow",
            "WARN": "yellow",
            "INFO": "blue",
            "DEBUG": "gray",
        },
        "os": os,
        "re": re,
        "Depends": Depends,
        "Query": Query,
        "HTTPException": HTTPException,
        "operator_access_required": lambda: None,
    }
    exec(compile(module, str(ROOT / "app/routes/logs.py"), "exec"), namespace)
    return namespace, namespace["get_log_content"]


async def _verify_edge_cases(
    namespace: dict, handler: Callable[..., Awaitable[Any]], log_dir: Path
) -> None:
    """Check both variants on filtered, paginated, Unicode, and missing-file responses."""
    # Verify pagination/filtering, Unicode, and the missing-file branch with both variants.
    (log_dir / "edge.log").write_text(
        "[2026-10-07 08:30:00,123] [INFO] [scheduler] [pid=1] Camera normal\n"
        "[2026-10-07 08:30:01,123] [ERROR] [snapshot] [pid=2] Gagal: kamera \u2713 \U0001f4f7\n",
        encoding="utf-8",
    )
    for filename, filters in (
        ("edge.log", {"level": "ERROR", "logger": "snapshot", "search": "Gagal"}),
        ("edge.log", {"offset": 1}),
        ("missing.log", {}),
    ):
        kwargs = {
            "log_type": "snapshot",
            "filename": filename,
            "level": None,
            "logger": None,
            "search": None,
            "limit": 100,
            "offset": 0,
            "current_operator": None,
        }
        kwargs.update(filters)
        namespace["JSONResponse"] = namespace["ORJSONResponse"] = JSONResponse
        baseline = await handler(**kwargs)
        namespace["JSONResponse"] = namespace["ORJSONResponse"] = ORJSONResponse
        candidate = await handler(**kwargs)
        assert (baseline.body, baseline.status_code, dict(baseline.headers)) == (
            candidate.body,
            candidate.status_code,
            dict(candidate.headers),
        )


def _measure_serialization(renderers: dict, payload: dict, iterations: int) -> dict:
    """Time seven alternating batches of response construction for a fixed payload."""
    serial_samples = {name: [] for name in renderers}
    for batch in range(7):
        for name in list(renderers)[:: 1 if batch % 2 == 0 else -1]:
            renderer = renderers[name]
            started = perf_counter_ns()
            for _ in range(iterations):
                renderer(payload)
            serial_samples[name].append((perf_counter_ns() - started) / iterations / 1e6)
    return serial_samples


async def benchmark(args: argparse.Namespace) -> dict:
    """Compare both renderers with the same immutable log file and payload."""
    raw = args.input.read_bytes()
    result = {
        "source": args.input.name,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "file_bytes": len(raw),
        "nonblank_lines": sum(
            bool(line.strip()) for line in raw.decode("utf-8", errors="ignore").splitlines()
        ),
        "python": platform.python_version(),
        "packages": {name: version(name) for name in ("fastapi", "starlette", "orjson")},
        "route_repeats": args.repeats,
        "serialization_iterations_per_batch": args.iterations,
        "serialization_batches": 7,
        "scope": "Warm filesystem cache; function call excludes HTTP, auth, middleware, database, and network. Serialization uses the actual handler payload.",
        "cases": [],
    }
    with tempfile.TemporaryDirectory(prefix="bsnap-log-benchmark-") as directory:
        log_dir = Path(directory)
        (log_dir / "snapshot.log").write_bytes(raw)
        namespace, handler = load_function(log_dir)

        class Capture:
            def __init__(self, content):
                self.content = content

        renderers = {"JSONResponse": JSONResponse, "ORJSONResponse": ORJSONResponse}
        for limit in (100, 1000, 5000):
            kwargs = {
                "log_type": "snapshot",
                "filename": "snapshot.log",
                "level": None,
                "logger": None,
                "search": None,
                "limit": limit,
                "offset": 0,
                "current_operator": None,
            }
            namespace["JSONResponse"] = namespace["ORJSONResponse"] = Capture
            payload = (await handler(**kwargs)).content
            reference = JSONResponse(payload)
            alternative = ORJSONResponse(payload)
            assert reference.body == alternative.body, "Response bytes differ"
            assert reference.status_code == alternative.status_code
            assert dict(reference.headers) == dict(alternative.headers)
            case = {
                "limit": limit,
                "returned_entries": len(payload["entries"]),
                "payload_bytes": len(reference.body),
                "identical_bytes_status_headers": True,
                "serialization_median_ms": {},
                "function_median_ms": {},
            }
            function_samples = {name: [] for name in renderers}
            # Warm both renderers before timing; alternate execution order per round.
            for renderer in renderers.values():
                renderer(payload)
                namespace["JSONResponse"] = namespace["ORJSONResponse"] = renderer
                await handler(**kwargs)
            serial_samples = _measure_serialization(renderers, payload, args.iterations)
            for round_number in range(args.repeats):
                for name in list(renderers)[:: 1 if round_number % 2 == 0 else -1]:
                    namespace["JSONResponse"] = namespace["ORJSONResponse"] = renderers[name]
                    started = perf_counter_ns()
                    response = await handler(**kwargs)
                    elapsed = (perf_counter_ns() - started) / 1e6
                    assert response.body == reference.body
                    function_samples[name].append(elapsed)
            for name in renderers:
                case["serialization_median_ms"][name] = median(serial_samples[name])
                case["function_median_ms"][name] = median(function_samples[name])
            for phase in ("serialization", "function"):
                timings = case[f"{phase}_median_ms"]
                case[f"{phase}_speedup"] = timings["JSONResponse"] / timings["ORJSONResponse"]
            result["cases"].append(case)
            print(json.dumps(case), flush=True)
        await _verify_edge_cases(namespace, handler, log_dir)
        result["edge_cases_identical"] = True
    return result


def main() -> None:
    """Run the comparison and save aggregate timings without log contents."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "logs/snapshot.log")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks/log_response.json")
    parser.add_argument("--repeats", type=int, default=15)
    parser.add_argument("--iterations", type=int, default=100)
    args = parser.parse_args()
    if args.repeats < 1 or args.iterations < 1:
        parser.error("repeats and iterations must be positive")
    result = asyncio.run(benchmark(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print("Results saved to " + str(args.output))


if __name__ == "__main__":
    main()
