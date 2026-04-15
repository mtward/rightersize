# rightersize

`rightersize` is a **conservative, audit‑friendly right‑sizing analysis pipeline** built on top of Linux `sysstat` (SAR) data. It uses Ansible only for **data collection and orchestration** and performs **all interpretation and recommendation logic offline in Python**, producing JSON and CSV artifacts suitable for peer review, spreadsheets, or capacity governance processes.

This project deliberately avoids:

*   real‑time monitoring
*   vendor‑specific heuristics
*   opaque scoring models
*   automatic resizing actions

The output is a **defensible recommendation**, not an imperative.

***

## Repository Contents

    .
    ├── ansible-rightsize-report.yml
    ├── sar2rightsize.py
    ├── json2csv.py
    ├── json2summarycsv.py
    ├── LICENSE
    └── README.md

### Roles of Each Component

| File                           | Purpose                                                                 |
| ------------------------------ | ----------------------------------------------------------------------- |
| `ansible-rightsize-report.yml` | Collects historical SAR data and invokes analysis                       |
| `sar2rightsize.py`             | Core analytics engine: parses, correlates, and produces recommendations |
| `json2csv.py`                  | Flattens raw JSON output to row‑level CSV                               |
| `json2summarycsv.py`           | Produces summary‑level, host‑centric CSV                                |

***

## Design Philosophy

1.  **SAR is the source of truth**  
    No procfs scraping, no live sampling. Historical data only.

2.  **Percentiles > averages**  
    Tail behavior matters for capacity planning.

3.  **Downsizing is opt‑out, not opt‑in**  
    Any sign of risk blocks reductions.

4.  **Bursts are real workloads**  
    Periodic saturation is treated as demand, not noise.

5.  **Swap semantics matter**  
    zram ≠ disk swap ≠ no swap.

***

## Data Collection (Ansible)

The Ansible playbook invokes `sar2rightsize.py` on each host and relies on:

*   `/var/log/sa/sa*` historical files
*   `sadf -j` to emit machine‑readable JSON
*   No live SAR execution

Defaults passed implicitly or explicitly mirror the Python defaults unless overridden.

***

## How `sar2rightsize.py` Works

### 1. Input Window Selection

By default:

```bash
--days 30
```

The script searches:

*   `/var/log/sa/saDD`
*   `/var/log/sa/saYYYYMMDD`

Only files that exist are used. Missing days are tolerated but reduce confidence.

***

### 2. Metrics Collected (and Why)

#### CPU (`sadf -u`)

Extracted fields:

*   `%user`
*   `%nice`
*   `%system`
*   `%irq`
*   `%soft`
*   `%steal`
*   `%iowait`

**Busy CPU** is defined as:

    user + nice + system + irq + soft + steal

Rationale:

*   I/O wait is excluded from “CPU demand”
*   Steal time is included in busy because it reflects *requested but denied* CPU

Collected series:

*   per-sample busy %
*   iowait %
*   steal %

***

#### Scheduler Queue (`sadf -q`)

Field:

*   `runq-sz`

Rationale:

*   CPU saturation without queue growth is often benign
*   Queue depth correlates strongly with user‑visible contention
*   Used as **burst corroboration**

***

#### Memory (`sadf -r`)

Key fields:

*   `kbmemused`
*   `kbbuffers`
*   `kbcached`
*   `kbslab`
*   `%commit`

**Working set approximation**:

```text
If memory used > 90% of total:
  WS ≈ used − (buffers + cache + slab)
Else:
  WS ≈ used
```

Rationale:

*   Avoids shrinking memory based on reclaimable page cache
*   Conservative under cache pressure

***

#### Swap Activity

From SAR:

*   `pswpin/s`
*   `pswpout/s`
*   `%swpused`

From `/proc/swaps`:

*   disk‑backed vs zram swap
*   whether swap is *actively used*

***

### 3. Percentiles (Default)

| Resource        | Percentile |
| --------------- | ---------- |
| CPU             | p95        |
| Memory          | p99        |
| Swap IO         | p95        |
| Burst detection | p99.5      |

Percentiles are computed explicitly (no numpy dependency) and ignore missing samples.

***

### 4. Timestamp Alignment

CPU and run‑queue samples are aligned by:

*   SADF timestamp fields (`date`, `time`, `iso`, or epoch)
*   Falling back to index ordering if necessary

This enables **correlated burst detection**, not just independent maxima.

***

## Burst Detection (Why Downsizing Is Hard)

The script intentionally biases toward **false negatives**, not false positives.

### Default Burst Criteria

A host is considered **bursty** if *any* of the following are true:

1.  **Sustained burst**
    *   ≥ 3 consecutive 5‑min samples
    *   CPU busy ≥ 90%
    *   AND runq ≥ vCPUs × 1.0

2.  **Recurring burst**
    *   ≥ 6 total burst‑qualified samples
    *   Same conditions as above

3.  **Tail burst**
    *   p99.5 busy ≥ 90%
    *   Computed over runq‑qualified samples

Run queue corroboration is **required by default** to reduce misclassification from single‑thread or cache‑miss artifacts.

***

## CPU Recommendation Logic

### Demand Estimation

```text
effective_demand =
    (p95_busy / 100)
  × (1 + cpu_headroom)

recommended_vcpus =
    ceil(
      current_vcpus ×
      (effective_demand / cpu_target)
    )
```

Defaults:

*   target utilization: **70%**
*   headroom: **20%**

This intentionally lands **below saturation**, not at it.

***

### Guards That Block Downsizing

CPU downsizing is blocked if **any** of the following are true:

*   Bursty classification triggered
*   p95 steal ≥ 2%
*   max steal ≥ 5%

Upsizing *is still allowed* under contention.

Maximum one‑step reduction is capped via:

```bash
--max-vcpu-reduction
```

Default is effectively unlimited but present for change‑control workflows.

***

## Memory Recommendation Logic

### Base Formula

```text
recommended_mb =
    ceil(
      p99_working_set × (1 + mem_headroom)
    )
  + os_reserve_mb
```

Defaults:

*   memory headroom: **20%**
*   OS reserve: **1024 MB**

***

### Swap‑Aware Guardrails

Swap behavior fundamentally alters interpretation:

| Swap Type      | Behavior                              |
| -------------- | ------------------------------------- |
| Disk swap used | **Hard stop** – no memory reduction   |
| zram only      | Soft signal – allow limited reduction |
| Unknown        | Conservative stop                     |

For zram:

*   Adds additional 10% headroom
*   Caps downsizing to **≤20%** total reduction

Rationale:

*   zram often masks pressure safely
*   disk swap indicates real shortage

***

## Confidence Scoring

| Condition        | Confidence Impact |
| ---------------- | ----------------- |
| <200 CPU samples | High → Medium     |
| Swap activity    | High → Medium     |
| Missing SAR data | Medium → Low      |

Confidence is surfaced explicitly in output.

***

## Output Artifacts

### JSON (`sar2rightsize.py`)

Contains:

*   Classification details
*   Raw percentiles
*   Burst and contention rationale
*   Final recommendations with notes

Designed for audit and review.

***

### CSV (`json2csv.py`, `json2summarycsv.py`)

*   `json2csv.py`: row‑level, metric‑centric
*   `json2summarycsv.py`: host‑level recommendations

Both preserve interpretability over compression.

***

## Non‑Goals (Explicit)

*   No automatic resizing
*   No cloud flavor tuning
*   No NUMA or topology modeling
*   No latency‑aware QoS inference

Those are policy decisions, not tooling decisions.

***

## Intended Audience

This tool is designed for:

*   Infrastructure engineers
*   Capacity planners
*   SREs governing large fleets
*   Environments where *downsizing errors are worse than overages*

Just say which direction you want to go.
