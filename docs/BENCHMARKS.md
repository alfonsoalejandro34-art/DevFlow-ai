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

## Results: Docker sandbox runner

Batch `20261112T223434-f46e5b`, Apple Silicon MacBook Air (16 GB), macOS 27.2,
Docker Desktop 29.9.3, Python 3.14.7 (host) / 3.12 (runner image).

| Scenario | Mode | Median | p90 | Min | Max | Std dev |
|---|---|---|---|---|---|---|
| Test feedback, calculator-passing | baseline | 185.6 ms | 190.8 | 184.2 | 190.8 | 1.9 |
| | DevFlow (Docker) | 774.0 ms | 785.6 | 716.8 | 785.6 | 23.5 |
| Test feedback, inventory-service | baseline | 210.1 ms | 215.2 | 207.2 | 215.2 | 2.3 |
| | DevFlow (Docker) | 778.1 ms | 787.0 | 768.6 | 787.0 | 5.4 |
| Fix validation, inventory-service | baseline | 211.1 ms | 214.9 | 208.3 | 214.9 | 1.8 |
| | DevFlow (Docker) | 852.6 ms | 897.5 | 839.4 | 897.5 | 17.4 |
| Container start-up (`python -c pass`) | Docker | 123.9 ms | 150.4 | 108.6 | 150.4 | 11.7 |

**Where the time goes** (medians, inventory-service, from each run's recorded timings):

| Component | Time |
|---|---|
| pytest executing the 14 tests | 28 ms |
| Container path: create/start (~124 ms), Python and pytest start-up over Docker Desktop's file sharing (a bare `python -c "import pytest"` container takes ~380 ms), kill/remove | ~490 ms |
| DevFlow pipeline: API, job scheduling, blob snapshot, JUnit parsing, DB writes, 50 ms polling | ~260 ms |
| **End to end** | **778 ms** |

The sandbox costs about half a second per run on macOS. Most of that is Docker
Desktop's VM and file-sharing layer, not DevFlow. On Linux, where containers run
natively, it is expected to be lower, but that was not measured. Possible reductions:
reusing a warm container per repository (which weakens per-run isolation) or copying
the snapshot into the container instead of bind-mounting it.

## Results: local runner (no isolation)

Batch `20261112T214741-f14760`, same machine, **local runner**, measured before Docker
was installed.

| Scenario | Mode | Median | p90 | Min | Max | Std dev |
|---|---|---|---|---|---|---|
| Test feedback, calculator-passing | baseline | 208.5 ms | 216.8 | 203.1 | 216.8 | 4.3 |
| | DevFlow (local) | 402.3 ms | 415.4 | 344.2 | 415.4 | 19.8 |
| Test feedback, inventory-service | baseline | 216.6 ms | 218.0 | 215.6 | 218.0 | 0.8 |
| | DevFlow (local) | 405.7 ms | 410.3 | 392.1 | 410.3 | 5.1 |
| Fix validation, inventory-service | baseline | 218.3 ms | 219.9 | 217.4 | 219.9 | 0.9 |
| | DevFlow (local) | 456.7 ms | 461.6 | 429.9 | 461.6 | 10.4 |

**Interpretation.** For these very small suites, DevFlow is slower than running pytest
by hand: **about 570–640 ms with the Docker sandbox** and **about 190–240 ms with the
local runner**. The extra time goes to job scheduling, the
blob-level snapshot, writing results and failure records, the API round trips, and
polling. DevFlow is not faster at running tests, and these numbers do not claim it is.

What the benchmark does *not* capture, and DevFlow is meant to change, is the human
time around a test run: finding the failing assertion in raw output, recognizing a
failure seen before, setting up a clean environment to try a fix, and keeping a record.
Those are visible in the product's own metrics (time to failure signal, time to
validate fix, repeated and resolved failures) but were not measured against a human
baseline here. Any such claim would need a user study.

## AI diagnosis timings (single observations, not a benchmark)

| Provider | Analysis time | Outcome |
|---|---|---|
| Claude Code CLI 2.1.322 | 23.9 s | Identified all 4 bugs; patch valid, approved, retest 14/14 |
| Codex CLI 0.163.0 | 18.3 s | Identified all 4 bugs; patch malformed, rejected by validation |
| Rule-based | 8 ms | Categorized failures with locations; no patch |

These are one call each and are listed only as order-of-magnitude context.
