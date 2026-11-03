"""Benchmark the production camera-group HTTP handler on disposable PostgreSQL data.

Set BSNAP_BENCHMARK_DATABASE_URL to a test database. Run --phase before, change
the handler, then --phase after with the same --state and --output paths. Auth is
overridden, but SQL, request handling and JSON serialization are real. No camera,
email, scheduler or production application startup is executed. --phase cleanup
only drops the generated schemas recorded in the state file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import sys

from collections.abc import Generator
from importlib.metadata import version
from pathlib import Path
from statistics import median
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, delete, event, insert, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.database import Base, get_db  # noqa: E402
from app.models.camera import Camera, camera_camera_groups  # noqa: E402
from app.models.camera_group import CameraGroup  # noqa: E402
from app.models.nvr import NVR  # noqa: E402
from app.models.user import User  # noqa: E402
from app.routes import camera_groups  # noqa: E402
from app.routes.auth import admin_access_required  # noqa: E402

SCHEMA_PATTERN = re.compile(r"ca21_bench_[0-9a-f]{12}_g[0-9]+")


def scoped_engine(url: str, schema: str) -> Engine:
    """Restrict ORM tables to one generated schema, without changing public data."""
    if not SCHEMA_PATTERN.fullmatch(schema):
        raise ValueError("Refusing a schema outside this benchmark namespace")
    return create_engine(url, execution_options={"schema_translate_map": {None: schema}})


def seed_dataset(url: str, schema: str, group_count: int) -> dict:
    """Seed memberships, unassigned rows and one empty group in a private schema."""
    engine = scoped_engine(url, schema)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            Base.metadata.create_all(
                connection,
                tables=[
                    CameraGroup.__table__,
                    Camera.__table__,
                    NVR.__table__,
                    User.__table__,
                    camera_camera_groups,
                ],
            )
            connection.execute(delete(CameraGroup))  # Only freshly seeded default groups.
            if group_count:
                connection.execute(
                    insert(CameraGroup),
                    [{"id": i, "name": f"Group {i:04d}"} for i in range(1, group_count + 1)],
                )
            active = max(0, group_count - 1)
            cameras, memberships, nvrs, users = [], [], [], []
            for group in range(1, active + 1):
                for index in range(20):
                    camera_id = f"camera-{group}-{index}"
                    cameras.append({"id": camera_id, "hostname": camera_id, "group_id": None})
                    memberships.append({"camera_id": camera_id, "camera_group_id": group})
                    if index % 5 == 0 and active > 1:
                        memberships.append(
                            {"camera_id": camera_id, "camera_group_id": group % active + 1}
                        )
                nvrs.extend(
                    {
                        "id": f"nvr-{group}-{i}",
                        "hostname": f"nvr-{group}-{i}",
                        "ip": "192.0.2.1",
                        "group_id": group,
                    }
                    for i in range(2)
                )
                users.extend(
                    {
                        "id": (group - 1) * 3 + i + 1,
                        "username": f"user-{group}-{i}",
                        "password": "!",
                        "role": "viewer",
                        "group_id": group,
                    }
                    for i in range(3)
                )
            if cameras:
                connection.execute(insert(Camera), cameras)
                connection.execute(insert(camera_camera_groups), memberships)
                connection.execute(insert(NVR), nvrs)
                connection.execute(insert(User), users)
            connection.execute(
                insert(User),
                {
                    "id": 1000000,
                    "username": "unrestricted",
                    "password": "!",
                    "role": "admin",
                    "group_id": None,
                },
            )
            return {
                "schema": schema,
                "groups": group_count,
                "cameras": len(cameras),
                "memberships": len(memberships),
                "nvrs": len(nvrs),
                "users": len(users) + 1,
            }
    finally:
        engine.dispose()


def measure(url: str, dataset: dict, warmups: int, runs: int) -> dict:
    """Measure warmed HTTP responses with a new session per request and count SQL."""
    engine = scoped_engine(url, dataset["schema"])
    count = [0]

    @event.listens_for(engine, "before_cursor_execute")
    def count_selects(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().upper().startswith("SELECT"):
            count[0] += 1

    def session() -> Generator[Session, None, None]:
        with Session(engine) as db:
            yield db

    app = FastAPI()
    app.include_router(camera_groups.router)
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[admin_access_required] = lambda: SimpleNamespace(id=1, role="admin")
    timings, queries, digest = [], [], None
    try:
        with TestClient(app) as client:
            for index in range(warmups + runs):
                count[0] = 0
                started = perf_counter()
                response = client.get("/api/camera-groups")
                elapsed = (perf_counter() - started) * 1000
                if response.status_code != 200:
                    raise RuntimeError(f"Benchmark HTTP status {response.status_code}")
                current_digest = hashlib.sha256(response.content).hexdigest()
                if digest is not None and digest != current_digest:
                    raise RuntimeError("Response changed within one benchmark variant")
                digest = current_digest
                if index >= warmups:
                    timings.append(elapsed)
                    queries.append(count[0])
        return {
            **dataset,
            "response_sha256": digest,
            "selects_per_request": sorted(set(queries)),
            "median_ms": round(median(timings), 3),
            "p95_ms": round(sorted(timings)[math.ceil(len(timings) * 0.95) - 1], 3),
            "samples_ms": [round(value, 3) for value in timings],
        }
    finally:
        engine.dispose()


def prepare_run(args: argparse.Namespace, url: str) -> tuple[dict, dict]:
    """Persist generated schemas incrementally so interrupted setup can be cleaned."""
    if args.phase == "before":
        if args.state.exists() or args.output.exists():
            raise ValueError("Before phase requires new state/output paths")
        args.state.parent.mkdir(parents=True, exist_ok=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        token = uuid4().hex[:12]
        state = {"kind": "ca21_benchmark", "datasets": []}
        args.state.write_text(json.dumps(state, indent=2), encoding="utf-8")
        for size in [0, 10, 50, 100, 250]:
            state["datasets"].append(seed_dataset(url, f"ca21_bench_{token}_g{size}", size))
            args.state.write_text(json.dumps(state, indent=2), encoding="utf-8")
        results = {
            "environment": {
                "python": platform.python_version(),
                "sqlalchemy": version("sqlalchemy"),
                "fastapi": version("fastapi"),
            },
            "scope": "Serial warmed HTTP requests on disposable PostgreSQL schemas; auth overridden; no production network or startup",
            "warmups": args.warmups,
            "runs": args.runs,
        }
        with create_engine(url).connect() as connection:
            results["environment"]["postgresql"] = connection.scalar(text("SHOW server_version"))
    else:
        state = json.loads(args.state.read_text(encoding="utf-8"))
        if state.get("kind") != "ca21_benchmark":
            raise ValueError("Unknown benchmark state")
        if args.phase == "cleanup":
            return state, {}
        results = json.loads(args.output.read_text(encoding="utf-8"))
        if results["runs"] != args.runs or results["warmups"] != args.warmups:
            raise ValueError("Before/after request counts must match")
    return state, results


def main() -> None:
    """Create datasets once, measure both source versions, and clean up explicitly."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=["before", "after", "cleanup"], required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--warmups", type=int, default=5)
    args = parser.parse_args()
    url = os.environ.get("BSNAP_BENCHMARK_DATABASE_URL")
    if not url or not url.startswith("postgresql"):
        parser.error("Set BSNAP_BENCHMARK_DATABASE_URL to an explicit PostgreSQL test database")
    if args.runs < 2 or args.warmups < 0:
        parser.error("Use at least two measured requests and nonnegative warmups")
    state, results = prepare_run(args, url)
    if args.phase == "cleanup":
        for dataset in state["datasets"]:
            engine = scoped_engine(url, dataset["schema"])
            try:
                with engine.begin() as connection:
                    connection.execute(text(f'DROP SCHEMA "{dataset["schema"]}" CASCADE'))
            finally:
                engine.dispose()
        print("Removed only the generated benchmark schemas")
        return
    measurements = [measure(url, data, args.warmups, args.runs) for data in state["datasets"]]
    if args.phase == "after":
        for old, new in zip(results["before"], measurements, strict=True):
            if old["response_sha256"] != new["response_sha256"]:
                raise RuntimeError("Before/after HTTP bodies differ")
    results[args.phase] = measurements
    results[f"{args.phase}_source_sha256"] = hashlib.sha256(
        (ROOT / "app/routes/camera_groups.py").read_text(encoding="utf-8").encode()
    ).hexdigest()
    args.output.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    for row in measurements:
        print(
            f"{args.phase}: groups={row['groups']} SELECTs={row['selects_per_request']} median={row['median_ms']} ms p95={row['p95_ms']} ms"
        )


if __name__ == "__main__":
    main()
