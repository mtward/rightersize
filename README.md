# Linux VM Right‑Sizing via `sar` (Conservative, Burst‑Aware)

## Overview

`rightsizing_sar.py` is a **conservative, production‑safe Linux VM right‑sizing tool** that analyzes **historical sysstat (`sar`) data** to produce **CPU and memory recommendations** while explicitly protecting:

*   **Batch workloads**
*   **Scheduled peak jobs**
*   **Short but critical CPU bursts**
*   **Virtualized environments with CPU contention (steal time)**

Unlike naïve percentile‑only approaches, this tool is designed to **avoid breaking workloads** by default.  
It favors **stability and SLA protection over aggressive cost reclamation**.

***

## Key Design Principles

1.  **Conservative by default**
    *   CPU downsizing is *blocked* unless strong evidence shows it is safe.
2.  **Burst‑aware**
    *   Short, infrequent CPU spikes are *not ignored* if they indicate real parallel demand.
3.  **VM‑aware**
    *   Uses `%steal` to detect hypervisor contention and avoid false downsizing.
4.  **Explainable**
    *   Every decision includes explicit indicators and rationale in the output.
5.  **Uses native Linux data**
    *   No agents, Prometheus, or cloud APIs required.
    *   Relies entirely on `sysstat` (`sar` / `sadf`) history.

***

## What This Tool Is (and Is Not)

### ✅ This tool **is**

*   A **VM‑level rightsizing advisor** for Linux
*   Suitable for **mixed fleets** (services + batch)
*   Designed for **vSphere / KVM environments**
*   Safe to run at scale across hundreds of hosts

### ❌ This tool is **not**

*   A real‑time monitoring tool
*   A cloud cost optimizer
*   A replacement for application‑level load testing
*   Aggressive by default

***

## Data Sources

The script reads historical binary sysstat data from:

    /var/log/sa/sa*

It uses `sadf -j` (JSON output) to extract:

*   CPU utilization (`sar -u`)
*   Run queue depth (`sar -q`)
*   Memory usage (`sar -r`)
*   Swap activity (`sar -W`, `sar -S`)

**Default sampling interval assumed:** 5 minutes  
**Default lookback window:** 30 days

***

## CPU Sizing Methodology

### 1. Percentile Analysis (Advisory)

*   Computes **P95 CPU busy**
*   Applies **headroom** (default 20%)
*   Targets **70% sustained utilization**

This produces an **advisory CPU size**, *not an automatic reduction*.

***

### 2. Burst Classification (Critical)

A host is classified as **`bursty usage`** if it shows evidence of **real, repeatable CPU bursts**, defined as:

#### Sustained burst

*   ≥ **3 consecutive samples** (≈ 15 minutes)
*   CPU busy ≥ **90%**
*   **AND** run queue ≥ vCPUs (parallel demand)

#### Recurring burst

*   ≥ **6 total samples** (≈ 30 minutes cumulative)
*   CPU busy ≥ **90%**
*   **AND** run queue ≥ vCPUs

#### Tail burst

*   **P99.5 CPU busy ≥ 90%**
*   Evaluated on run‑queue‑qualified samples

> **Run‑queue corroboration is required by default** to avoid false positives from short single‑thread spikes.

***

### 3. Hypervisor Contention Protection

CPU downsizing is **blocked** if:

*   P95 `%steal` ≥ **2%**, or
*   Max `%steal` ≥ **5%**

This prevents shrinking VMs when CPU pressure is caused by **host‑level contention**, not guest demand.

***

### 4. Final CPU Decision Logic

| Condition                 | CPU Recommendation        |
| ------------------------- | ------------------------- |
| Bursty usage detected     | **No CPU downsizing**     |
| Steal contention detected | **No CPU downsizing**     |
| Steady usage + clean data | Allow **small step‑down** |
| Under‑provisioned         | Allow increase            |

**Maximum CPU reduction per run:** 1 vCPU (default)

***

## Memory Sizing Methodology

Memory sizing is simpler but still conservative:

1.  Compute **P99 working set memory**
2.  Add **20% headroom**
3.  Add **OS reserve** (default 1024 MB)

### Hard Safety Rule

If **any swap‑in or swap‑out activity** is observed:

*   Memory **will not be reduced**, even if percentiles suggest it.

This avoids slow‑burn performance regressions and OOM risk.

***

## Host Classification

Each host is explicitly classified:

*   `steady usage`
*   `bursty usage`

Along with:

*   `burst_indicators`
*   `contention_indicators`

This makes it easy to:

*   Filter safe candidates
*   Defend decisions to application teams
*   Audit why a host was protected

***

## Output Format

The script produces **one JSON file per host**, for example:

```json
{
  "host": "app01",
  "classification": {
    "usage_pattern": "bursty usage",
    "burst_indicators": [
      "sustained burst: >= 3 consecutive samples with busy>=90% AND runq>=6"
    ]
  },
  "current": {
    "vcpus": 6,
    "mem_mb": 32768
  },
  "recommendation": {
    "vcpu_recommended": 6,
    "mem_mb_recommended": 28672,
    "confidence": "high"
  }
}
```

These JSON files are designed to be **merged later into CSV** for reporting.

***

## Requirements

### Software

*   Python 3.8+
*   `sysstat` (version ≥ 12 recommended)

### System

*   Linux (RHEL 8/9/10 assumed)
*   Historical `sar` data enabled and retained

Verify collection:

```bash
systemctl status sysstat
ls /var/log/sa/sa*
```

***

## Running the Script

Basic run (recommended defaults):

```bash
/usr/bin/python3 rightsizing_sar.py \
  --days 30 \
  --out /var/tmp/rightsizing.json
```

### Common tuning knobs (optional)

More permissive downsizing:

```bash
--burst-recurring-samples 12
--burst-runq-factor 1.2
```

More conservative:

```bash
--burst-sustained-samples 2
--max-vcpu-reduction 0
```

***

## Interpreting Results (Important)

*   **“No change” does not mean failure**  
    It means the system showed evidence that downsizing might be risky.
*   **Bursty systems are protected intentionally**  
    These are often the most business‑critical.
*   **Confidence level matters**  
    Low confidence usually means insufficient data, not low usage.

***

## When to Trust the Tool

✅ Safe to trust for:

*   Identifying *obviously* oversized steady services
*   Blocking dangerous downsizing
*   Fleet‑wide analysis and trend tracking

⚠️ Still validate manually for:

*   Databases
*   Licensed software
*   Known seasonal workloads
*   Hosts with recent application changes

***

## Philosophy (Why This Exists)

Most right‑sizing tools answer:

> “How much do you use most of the time?”

This tool also asks:

> **“How much do you ever need when it matters?”**

That second question is why it exists.

***
