# CA-21 camera-group counts: before/after benchmark

Measured on 3 November 2026 using Python 3.13.12, SQLAlchemy 2.0.41,
FastAPI 0.116.1 and PostgreSQL 17.9 on loopback. The old handler is from source
revision `6c2d0b57af64315f4f46f58456204904f39e9124` (2.6.4); the optimized handler
ships in 2.6.5. [Raw measurements and response hashes](camera_groups_2026_11_03.json)
include all 30 measured samples per dataset and variant, plus source-file hashes.

| Groups | SELECTs before → after | Median before (ms) | Median after (ms) | Speedup | p95 before (ms) | p95 after (ms) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 1 → 1 | 4.340 | 6.315 | 0.69× | 5.756 | 10.497 |
| 10 | 31 → 1 | 25.567 | 7.607 | 3.36× | 28.678 | 9.870 |
| 50 | 151 → 1 | 113.492 | 8.864 | 12.80× | 143.022 | 11.467 |
| 100 | 301 → 1 | 246.131 | 12.014 | 20.49× | 263.935 | 15.590 |
| 250 | 751 → 1 | 674.469 | 15.282 | 44.13× | 736.654 | 25.173 |

At 250 groups, median response time fell by 97.73% and SELECT count by 99.87%.
The empty response has approximately 2 ms higher median overhead in this run:
constructing the aggregate query costs more than the previous empty-table lookup.
The optimization targets repeated counts as the number of groups grows; it does
not make every possible request faster. Values are observed local measurements,
not a production latency/capacity guarantee.

| Groups | Cameras | Camera memberships | NVRs | Users |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 0 | 0 | 0 | 1 |
| 10 | 180 | 216 | 18 | 28 |
| 50 | 980 | 1,176 | 98 | 148 |
| 100 | 1,980 | 2,376 | 198 | 298 |
| 250 | 4,980 | 5,976 | 498 | 748 |

Each nonempty dataset has one empty group. Active groups have 20 cameras, two
NVRs and three users; every fifth camera also belongs to the next active group.
One unrestricted user has NULL group_id and is excluded from group counts. Camera
legacy group_id is NULL, so association membership determines camera counts.

The original endpoint executes one group lookup plus three counts per group.
The new endpoint aggregates cameras, NVRs and users independently, then left-joins
those subqueries to groups in one SELECT. Coalescing missing counts to zero keeps
empty groups; separate aggregation prevents join multiplication. HTTP response
bodies have identical SHA-256 hashes for every before/after dataset, including
field order, totals and name sorting.

Method: the production router runs in FastAPI TestClient (in-process ASGI HTTP),
with a real PostgreSQL database and a fresh SQLAlchemy session per request. Both
variants use the same generated schemas and unchanged rows, five warmups and 30
serial measured requests. Timing covers dependency/session handling, SQL/driver
round trips, response construction and JSON serialization. Authentication is
overridden; application startup, camera/notification jobs, browser rendering,
Cloudflare/TCP client latency and real auth queries are excluded. SELECT counts
come from SQLAlchemy execution events; p95 uses the nearest-rank method.

Use [benchmark_camera_groups.py](../../../scripts/benchmark_camera_groups.py) with
an explicit **test** PostgreSQL URL in `BSNAP_BENCHMARK_DATABASE_URL`. On the old
handler, run `--phase before`; after changing the handler, run `--phase after`
with the same state/output and request counts:

```powershell
python scripts/benchmark_camera_groups.py --phase before --state tmp/ca21-state.json --output tmp/ca21-benchmark.json
# Apply the optimized handler, retaining the benchmark datasets and state.
python scripts/benchmark_camera_groups.py --phase after --state tmp/ca21-state.json --output tmp/ca21-benchmark.json
python scripts/benchmark_camera_groups.py --phase cleanup --state tmp/ca21-state.json --output tmp/ca21-benchmark.json
```

To repeat historical results, keep this script available in the isolated old/new
source checkouts. The script refuses to overwrite an existing before-run artifact,
compares response hashes, and cleans only its validated generated schema names.
No application/production data is modified. No schema migration or new index is
needed for the optimization.
