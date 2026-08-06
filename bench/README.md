# katcp message-codec benchmarks (MT-7273)

Verification and benchmarking scripts for the `katcp/core.py` large-message fast paths (parse / copy / encode).

Scripts resolve the target repository via the `KATCP_REPO` environment variable, defaulting to the parent repository containing this `bench/` directory.

(The pre-existing `benchtx*`/`runner.py`/`results.py` scripts in this directory are legacy twisted-based benchmarks, unrelated to these tools.)

### Requirements

Requires repository dependencies (`future`, `tornado>=4.3,<5` for e2e tests).

### Scripts

| Script | Description | Run Time |
|---|---|---|
| `ab_compare.py` | Compares baseline (default `master` via `git worktree`) against working tree. Prints per-operation speedup factors. Options: `--e2e`, `--ref <sha>`, `--py <python>`. | ~1 min |
| `fuzz_parse_branch.py` | Differential fuzz test: 300k generated lines against both the working tree parser and pristine master pulled from git history. Exits 1 on any divergence. Custom ref via `KATCP_BASELINE_REF`. | ~2 min |
| `run_core_tests.py` | Runs katcp core + BNF grammar test suites (62 tests). | seconds |
| `bench_katcp.py` | Standalone parse/encode/init/copy timings across channel counts for current checkout. | ~30 s |
| `e2e_proxy_bench.py` | Local client → mini-proxy → mock device chain to measure serial vs pipelined per-message latency. Options: `-n`, `--chans`. | ~1 min |
| `devm_repro.py` | Live test against a running CBF+ proxy. Queries and re-sets existing gains to keep state neutral. Requires `--port <proxy-port>` (see the proxy's `--addr` on its command line); `--smoke` (24 msgs), default 880 msgs. | ~5 min |
| `test_latency_target.py` | Gain-set timing against either the CBF+ proxy (`?wide-gain`) or the product controller (`?gain`), so the two can be subtracted to isolate proxy overhead. Input labels discovered from the proxy, or given via `--inputs` (required for the product). `--serial` for per-request latency, default pipelined. Needs `aiokatcp`, `numpy`, `uvloop` — use the katsdpcontroller venv. | ~1 min per run |

### Quick Start

```bash
cd bench
python ab_compare.py
python fuzz_parse_branch.py
