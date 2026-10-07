# get_log_content response benchmark

Measured against an immutable copy of the local snapshot.log. No log contents are included in this report.

Input: 17,852 nonblank lines; 2,864,813 bytes.
Runtime: Python 3.13.12; fastapi 0.116.1, starlette 0.47.2, orjson 3.11.0.

The production LogEntry parser and get_log_content handler are loaded via AST to avoid application/DB startup. Only the response constructor changes. Authentication, middleware, HTTP transport, and network are outside this measurement.

Serialization: median of 7 batches, 100 responses per batch. Handler: median of 15 calls per method. Both methods are warmed and execution order alternates. File reads use a warm filesystem cache.

| Entries | Payload bytes | JSON serialization (ms) | ORJSON serialization (ms) | Speedup | JSON handler (ms) | ORJSON handler (ms) | Handler reduction |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 100 | 38,600 | 0.262 | 0.047 | 5.54x | 103.926 | 100.596 | 3.2% |
| 1,000 | 385,384 | 2.333 | 0.405 | 5.77x | 105.993 | 98.888 | 6.7% |
| 5,000 | 1,927,844 | 13.241 | 2.990 | 4.43x | 122.582 | 110.513 | 9.8% |

Both variants produced identical response bytes, HTTP status, and headers for all measured payloads. Additional equivalence checks passed for filters, pagination, Unicode, and missing files.

ORJSON improves response serialization, particularly for large pages. The complete handler benefits less because it reads and parses the whole file regardless of page size. Timing differences of a few milliseconds can include ordinary runtime noise; these results are not whole-server latency measurements.

Reproduce with:

```powershell
python scripts/benchmark_log_response.py --input logs/snapshot.log
```

Results depend on the input log, machine, installed package versions, and workload. The benchmark used installed versions, not a fresh installation of requirements.txt.
