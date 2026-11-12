# Benchmarks

```bash
make serve          # in one terminal (or with the local runner, see README)
make benchmark      # = python benchmarks/run_benchmarks.py --iterations 10 --warmup 1
```

Results go to the database (shown on Analytics → Benchmarks) and to
`benchmarks/results/<batch>.json`, which includes every sample and the machine details.

## Questions

1. How long does a developer wait for test feedback through DevFlow compared with
   running pytest by hand?
2. How long does it take to validate a proposed fix?
3. What does container isolation cost? (Docker runner only.)

## Method

- **Identical inputs.** Every iteration runs the same bundled repositories
  (`calculator-passing`: 3 tests, `inventory-service`: 14 tests, 5 failing) at the same
  commit.
- **Baseline: manual workflow.** Clone the repository into a temporary directory and run
  `python -m pytest -q` with the same interpreter. Timing runs from the start of the
  clone until pytest exits. For fix validation, the baseline also runs `git apply` with
  the known fix before pytest.
- **DevFlow workflow.** Timing runs from `POST /api/runs` until the run is terminal
  *and* its classified failures have been fetched, which is the point where a developer
  has the answer. For fix validation, timing runs from `POST /patches/{id}/review`
  (approve) until the retest verdict.
- **Polling granularity** is 50 ms. That adds about 25 ms on average and up to 50 ms
  to DevFlow measurements, and the numbers are left as measured.
- **Repetition.** 1 discarded warmup, then 10 measured iterations per scenario and
  mode, run sequentially on an otherwise idle machine. Reported: median, p90, min, max,
  and standard deviation.
- **Container start-up** (Docker runner only): `docker run --rm` of the sandbox image
  executing `python -c pass` with DevFlow's exact security flags.

## Results

Batch `20261112T214741-f14760`, Apple Silicon MacBook Air (16 GB), macOS 27.2,
Python 3.14.7, **local runner** (Docker was not installed when this batch ran).

| Scenario | Mode | Median | p90 | Min | Max | Std dev |
|---|---|---|---|---|---|---|
| Test feedback, calculator-passing | baseline | 208.5 ms | 216.8 | 203.1 | 216.8 | 4.3 |
| | DevFlow (local) | 402.3 ms | 415.4 | 344.2 | 415.4 | 19.8 |
| Test feedback, inventory-service | baseline | 216.6 ms | 218.0 | 215.6 | 218.0 | 0.8 |
| | DevFlow (local) | 405.7 ms | 410.3 | 392.1 | 410.3 | 5.1 |
| Fix validation, inventory-service | baseline | 218.3 ms | 219.9 | 217.4 | 219.9 | 0.9 |
| | DevFlow (local) | 456.7 ms | 461.6 | 429.9 | 461.6 | 10.4 |

**Interpretation.** For these very small suites, DevFlow is **about 190–240 ms slower**
in wall time than running pytest by hand. The extra time goes to job scheduling, the
blob-level snapshot, writing results and failure records, the API round trips, and
polling. DevFlow is not faster at running tests, and these numbers do not claim it is.

What the benchmark does *not* capture, and DevFlow is meant to change, is the human
time around a test run: finding the failing assertion in raw output, recognizing a
failure seen before, setting up a clean environment to try a fix, and keeping a record.
Those are visible in the product's own metrics (time to failure signal, time to
validate fix, repeated and resolved failures) but were not measured against a human
baseline here. Any such claim would need a user study.

**Pending:** Docker-runner results and the container start-up scenario. Run
`make benchmark` with the Docker runner and add the batch here. Expect container
start-up to add several hundred milliseconds per run on macOS.

## AI diagnosis timings (single observations, not a benchmark)

| Provider | Analysis time | Outcome |
|---|---|---|
| Claude Code CLI 2.1.322 | 23.9 s | Identified all 4 bugs; patch valid, approved, retest 14/14 |
| Codex CLI 0.163.0 | 18.3 s | Identified all 4 bugs; patch malformed, rejected by validation |
| Rule-based | 8 ms | Categorized failures with locations; no patch |

These are one call each and are listed only as order-of-magnitude context.
