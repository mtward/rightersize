#!/usr/bin/env python3
import argparse
import datetime
import json
import math
import os
import socket
import subprocess
import sys

import subprocess, json

import subprocess
import json

SADF = "/usr/bin/sadf"

def run(cmd):
    """
    Python 3.6-compatible subprocess runner that captures stdout+stderr.
    """
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True  # Py3.6 replacement for text=True
    )
    if p.returncode != 0:
        raise RuntimeError(
            "Command failed ({rc}): {cmd}\nSTDOUT:\n{out}\nSTDERR:\n{err}\n".format(
                rc=p.returncode,
                cmd=" ".join(cmd),
                out=p.stdout,
                err=p.stderr
            )
        )
    return p.stdout

def sadf_json(sa_file, sar_args):
    cmd = [SADF, "-j", sa_file, "--"] + sar_args
    out = run(cmd)
    return json.loads(out)

def percentile(vals, p):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    vals.sort()
    if len(vals) == 1:
        return vals[0]
    k = (p / 100.0) * (len(vals) - 1)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return vals[int(k)]
    return vals[f] * (c - k) + vals[c] * (k - f)


def memtotal_mb():
    with open("/proc/meminfo", "r") as f:
        for line in f:
            if line.startswith("MemTotal:"):
                kb = int(line.split()[1])
                return kb // 1024
    return None


def cpu_count():
    return os.cpu_count() or 1


def choose_sa_files(days):
    """
    sysstat daily activity files are typically in /var/log/sa as saDD or saYYYYMMDD.
    """
    base = "/var/log/sa"
    today = datetime.date.today()
    files = []
    for i in range(days):
        d = today - datetime.timedelta(days=i)
        f1 = os.path.join(base, f"sa{d:%d}")
        f2 = os.path.join(base, f"sa{d:%Y%m%d}")
        if os.path.isfile(f2):
            files.append(f2)
        elif os.path.isfile(f1):
            files.append(f1)
    return files


def iter_stats(doc):
    sysstat = doc.get("sysstat", {})
    hosts = sysstat.get("hosts", [])
    for h in hosts:
        for snap in h.get("statistics", []):
            yield snap


def as_list(x):
    if x is None:
        return []
    if isinstance(x, list):
        return x
    return [x]


def ffloat(x, default=None):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def first_present(d, keys, default=None):
    for k in keys:
        if isinstance(d, dict) and k in d and d[k] is not None:
            return d[k]
    return default


def get_ts_key(snap, fallback_idx=None):
    """
    Try hard to create a stable timestamp key for aligning -u and -q snapshots.
    sysstat sadf -j timestamps may appear in different shapes across versions.
    """
    ts = snap.get("timestamp")
    if isinstance(ts, dict):
        # Common patterns:
        # {"date":"2026-04-14","time":"11:10:01","timezone":"UTC"} etc.
        d = ts.get("date")
        t = ts.get("time")
        iso = ts.get("iso")
        if iso:
            return str(iso)
        if d and t:
            return f"{d}T{t}"
        # Sometimes "epoch" exists
        if ts.get("epoch") is not None:
            return f"epoch:{ts.get('epoch')}"
    elif isinstance(ts, str):
        return ts

    # Fallback: sometimes there is "date" / "time" at top-level
    d = snap.get("date")
    t = snap.get("time")
    if d and t:
        return f"{d}T{t}"

    # Last resort: index-based key (keeps ordering)
    if fallback_idx is not None:
        return f"idx:{fallback_idx}"
    return None


def get_cpu_all(snap):
    """
    Robustly extract the 'all' CPU row from sadf JSON.
    Handles list-vs-dict variations across sysstat versions.
    """
    if not isinstance(snap, dict):
        return None

    cpu_section = snap.get("cpu-load")
    if not cpu_section:
        return None

    # Normalize to list
    if isinstance(cpu_section, dict):
        cpu_rows = [cpu_section]
    elif isinstance(cpu_section, list):
        cpu_rows = cpu_section
    else:
        return None

    for row in cpu_rows:
        if not isinstance(row, dict):
            continue
        cpu_id = row.get("cpu")
        if cpu_id is None:
            continue
        if str(cpu_id).lower() == "all":
            return row

    return None


def get_queue(snap):
    q = snap.get("queue")
    if isinstance(q, dict):
        return q
    if isinstance(q, list) and q:
        return q[0]
    return None


def get_memory(snap):
    m = snap.get("memory")
    if isinstance(m, dict):
        return m
    if isinstance(m, list) and m:
        return m[0]
    return None


def get_swap_pages(snap):
    sp = snap.get("swap-pages")
    if isinstance(sp, dict):
        return sp
    if isinstance(sp, list) and sp:
        return sp[0]
    return None


def get_swap(snap):
    sw = snap.get("swap")
    if isinstance(sw, dict):
        return sw
    if isinstance(sw, list) and sw:
        return sw[0]
    return None

def detect_swap_type_and_usage():
    """
    Returns:
      swap_type: 'none', 'zram', 'disk'
      disk_swap_used: True/False
    Policy:
      - If disk swap exists AND is used -> disk
      - If disk swap exists but used == 0 and zram is used -> treat as zram
      - Mixed but unused disk is ignored
    """
    zram_present = False
    zram_used = False
    disk_present = False
    disk_used = False

    try:
        with open("/proc/swaps") as f:
            for line in f:
                if line.startswith("Filename") or not line.strip():
                    continue
                parts = line.split()
                dev = parts[0]
                used_kb = int(parts[3])  # Used column is in KB

                if dev.startswith("/dev/zram"):
                    zram_present = True
                    if used_kb > 0:
                        zram_used = True
                else:
                    # swap partition or swapfile
                    disk_present = True
                    if used_kb > 0:
                        disk_used = True
    except Exception:
        pass

    if disk_present and disk_used:
        return "disk", True
    if zram_present:
        return "zram", False
    return "none", False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)

    ap.add_argument("--cpu-percentile", type=float, default=95.0)
    ap.add_argument("--mem-percentile", type=float, default=99.0)

    # CPU sizing knobs
    ap.add_argument("--cpu-target", type=float, default=0.70)
    ap.add_argument("--cpu-headroom", type=float, default=0.20)

    # Memory sizing knobs
    ap.add_argument("--mem-headroom", type=float, default=0.20)
    ap.add_argument("--os-reserve-mb", type=int, default=1024)

    # Conservative change management
    ap.add_argument("--max-vcpu-reduction", type=int, default=1000,
                    help="Maximum vCPUs to reduce in a single recommendation (when downsizing is allowed).")

    # ---- Burst detection (tuned for 5-minute sysstat sampling) ----
    # Even fewer false positives:
    # - bursty classification requires sustained OR recurring burst evidence
    # - AND by default requires runq corroboration during those burst samples
    ap.add_argument("--burst-busy-threshold", type=float, default=90.0,
                    help="CPU busy% considered a burst level for burst classification.")
    ap.add_argument("--burst-sustained-samples", type=int, default=3,
                    help="Consecutive 5-min samples >= threshold to mark sustained burst (default 3 => ~15 minutes).")
    ap.add_argument("--burst-recurring-samples", type=int, default=6,
                    help="Total samples >= threshold to mark recurring burst (default 6 => ~30 minutes total).")
    ap.add_argument("--burst-runq-factor", type=float, default=1.0,
                    help="Runq corroboration threshold: runq >= vcpus * factor (default 1.0).")
    ap.add_argument("--require-runq-to-confirm-burst", action="store_true", default=True,
                    help="Default TRUE: require runq corroboration to classify bursty (reduces false positives).")
    ap.add_argument("--burst-percentile", type=float, default=99.5,
                    help="Tail percentile used for burst classification (computed over burst-qualified samples if runq required).")

    # ---- Contention checks ----
    ap.add_argument("--steal-hold-threshold", type=float, default=2.0,
                    help="If p95 steal >= this %, do not downsize CPU (possible hypervisor contention).")
    ap.add_argument("--steal-max-hold-threshold", type=float, default=5.0,
                    help="If max steal >= this %, do not downsize CPU.")

    ap.add_argument("--out", default="/var/tmp/rightsizing.json")
    args = ap.parse_args()

    host = socket.gethostname()
    vcpus = cpu_count()
    mem_mb = memtotal_mb()

    notes = []

    sa_files = choose_sa_files(args.days)
    if not sa_files:
        result = {
            "host": host,
            "error": "No /var/log/sa/sa* files found",
            "window_days_requested": args.days
        }
        print(json.dumps(result, indent=2))
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)
        return 2

    # Raw series (global)
    cpu_busy = []
    cpu_iowait = []
    cpu_steal = []
    runq = []
    ws_mb = []
    commit_pct = []
    swpin = []
    swpout = []
    swpused_pct = []
    swap_type, disk_swap_used = detect_swap_type_and_usage()

    # Timestamp-aligned series
    cpu_by_ts = {}   # ts -> dict(busy, steal, iowait)
    runq_by_ts = {}  # ts -> runq-sz

    parsed_cpu_snaps = 0
    parsed_q_snaps = 0
    parsed_mem_snaps = 0

    for f in sa_files:
        try:
            doc_u = sadf_json(f, ["-u"])
            doc_q = sadf_json(f, ["-q"])
            doc_r = sadf_json(f, ["-r"])
            doc_W = sadf_json(f, ["-W"])
            doc_S = sadf_json(f, ["-S"])
        
        except Exception as e:
            notes.append(f"sadf failed for {f}: {e}")
            continue

        # CPU snapshots with timestamps
        for idx, snap in enumerate(iter_stats(doc_u)):
            row = get_cpu_all(snap)
            if not row:
                continue

            ts_key = get_ts_key(snap, fallback_idx=("u:" + str(idx)))

            user   = ffloat(first_present(row, ["user", "%user"], 0.0), 0.0)
            nice   = ffloat(first_present(row, ["nice", "%nice"], 0.0), 0.0)
            system = ffloat(first_present(row, ["system", "%system"], 0.0), 0.0)
            irq    = ffloat(first_present(row, ["irq", "%irq"], 0.0), 0.0)
            soft   = ffloat(first_present(row, ["soft", "%soft"], 0.0), 0.0)
            steal  = ffloat(first_present(row, ["steal", "%steal"], 0.0), 0.0)
            iow    = ffloat(first_present(row, ["iowait", "%iowait"], 0.0), 0.0)

            busy = user + nice + system + irq + soft + steal
            busy = max(0.0, min(100.0, busy))

            cpu_busy.append(busy)
            cpu_iowait.append(iow)
            cpu_steal.append(steal)

            if ts_key:
                cpu_by_ts[ts_key] = {"busy": busy, "steal": steal, "iowait": iow}

            parsed_cpu_snaps += 1

        # Queue snapshots with timestamps
        for idx, snap in enumerate(iter_stats(doc_q)):
            q = get_queue(snap)
            if not q:
                continue
            ts_key = get_ts_key(snap, fallback_idx=("q:" + str(idx)))
            rq = ffloat(first_present(q, ["runq-sz", "runq_sz", "runq"], None), None)
            if rq is None:
                continue

            runq.append(rq)
            if ts_key:
                runq_by_ts[ts_key] = rq
            parsed_q_snaps += 1

        # Memory
        for snap in iter_stats(doc_r):
            m = get_memory(snap)
            if not m:
                continue

            kbmemused = ffloat(first_present(m, ["kbmemused", "memused", "used"], 0.0), 0.0)
            kbbuf     = ffloat(first_present(m, ["kbbuffers", "buffers"], 0.0), 0.0)
            kbcached  = ffloat(first_present(m, ["kbcached", "cached"], 0.0), 0.0)
            kbslab    = ffloat(first_present(m, ["kbslab", "slab"], 0.0), 0.0)

            pctcommit = ffloat(first_present(m, ["%commit", "commit", "pcommit"], 0.0), 0.0)

            # Working set approximation
            ws_kb = kbmemused
            if mem_mb is not None and ws_kb > (mem_mb * 1024 * 0.90) and (kbbuf + kbcached) > 0:
                ws_kb = max(0.0, ws_kb - kbbuf - kbcached - kbslab)

            ws_mb.append(ws_kb / 1024.0)
            commit_pct.append(pctcommit)
            parsed_mem_snaps += 1

        # Swap pages
        for snap in iter_stats(doc_W):
            sp = get_swap_pages(snap)
            if not sp:
                continue
            swpin.append(ffloat(first_present(sp, ["pswpin/s", "pswpin"], 0.0), 0.0))
            swpout.append(ffloat(first_present(sp, ["pswpout/s", "pswpout"], 0.0), 0.0))

        # Swap usage
        for snap in iter_stats(doc_S):
            sw = get_swap(snap)
            if not sw:
                continue
            swpused_pct.append(ffloat(first_present(sw, ["%swpused", "swpused"], 0.0), 0.0))

    # Percentiles
    p_cpu   = percentile(cpu_busy, args.cpu_percentile)
    p_iow   = percentile(cpu_iowait, args.cpu_percentile)
    p_steal = percentile(cpu_steal, args.cpu_percentile)
    p_runq  = percentile(runq, args.cpu_percentile)

    max_cpu   = max(cpu_busy) if cpu_busy else None
    max_runq  = max(runq) if runq else None
    max_steal = max(cpu_steal) if cpu_steal else None

    p_ws     = percentile(ws_mb, args.mem_percentile)
    p_commit = percentile(commit_pct, args.mem_percentile)

    p_swpin   = percentile(swpin, 95)
    p_swpout  = percentile(swpout, 95)
    p_swpused = percentile(swpused_pct, 95)

    notes = []
    confidence = "high"

    if p_cpu is None or p_ws is None:
        confidence = "low"
        notes.append("No parsable sadf samples; check sysstat collection/retention.")
    if parsed_cpu_snaps < 200:
        confidence = "medium" if confidence == "high" else confidence
        notes.append(f"Only {parsed_cpu_snaps} CPU snapshots parsed across {len(sa_files)} files; sampling may be sparse.")

    # -------------------------------
    # Burst classification (reduced false positives)
    # -------------------------------
    burst_indicators = []
    contention_indicators = []

    burst_level = args.burst_busy_threshold
    runq_threshold = vcpus * args.burst_runq_factor

    # Build aligned series list sorted by timestamp key (best-effort)
    # We only evaluate CPU timestamps; runq may be missing for some timestamps.
    aligned = []
    for ts, c in cpu_by_ts.items():
        rq = runq_by_ts.get(ts)
        aligned.append((ts, c.get("busy"), rq))
    aligned.sort(key=lambda x: x[0])

    # Define what qualifies as a "burst sample"
    # If runq corroboration required, only count samples where BOTH busy>=threshold and runq>=runq_threshold
    def is_burst_sample(busy, rq):
        if busy is None:
            return False
        if busy < burst_level:
            return False
        if args.require_runq_to_confirm_burst:
            return (rq is not None and rq >= runq_threshold)
        return True

    # Sustained burst: consecutive qualifying samples
    sustained = False
    consec = 0
    for _, busy, rq in aligned:
        if is_burst_sample(busy, rq):
            consec += 1
            if consec >= args.burst_sustained_samples:
                sustained = True
                break
        else:
            consec = 0

    # Recurring burst: total qualifying samples
    recurring_count = sum(1 for _, busy, rq in aligned if is_burst_sample(busy, rq))
    recurring = recurring_count >= args.burst_recurring_samples

    # Tail percentile burst: compute over qualifying samples if runq corroboration required
    burst_busy_population = []
    if args.require_runq_to_confirm_burst:
        burst_busy_population = [busy for _, busy, rq in aligned if (busy is not None and rq is not None and rq >= runq_threshold)]
    else:
        burst_busy_population = [busy for busy in cpu_busy if busy is not None]

    p_burst = percentile(burst_busy_population, args.burst_percentile) if burst_busy_population else None
    tail_burst = (p_burst is not None and p_burst >= burst_level)

    # Determine bursty classification
    bursty = False
    if sustained:
        bursty = True
        burst_indicators.append(
            f"sustained burst: >= {args.burst_sustained_samples} consecutive samples with busy>={burst_level}%"
            + (f" AND runq>={runq_threshold:.2f}" if args.require_runq_to_confirm_burst else "")
        )
    elif recurring:
        bursty = True
        burst_indicators.append(
            f"recurring burst: {recurring_count} samples with busy>={burst_level}%"
            + (f" AND runq>={runq_threshold:.2f}" if args.require_runq_to_confirm_burst else "")
            + f" (>= {args.burst_recurring_samples})"
        )
    elif tail_burst:
        bursty = True
        burst_indicators.append(
            f"tail burst: p{args.burst_percentile}_busy={p_burst:.2f}% >= {burst_level}%"
            + (" (computed on runq-qualified samples)" if args.require_runq_to_confirm_burst else "")
        )

    # Helpful context indicators
    if max_runq is not None and max_runq >= runq_threshold:
        burst_indicators.append(f"runq context: max_runq_sz={max_runq:.2f} >= {runq_threshold:.2f}")

    # Contention checks (still conservative)
    if p_steal is not None and p_steal >= args.steal_hold_threshold:
        contention_indicators.append(f"p{args.cpu_percentile}_steal={p_steal:.2f}%>= {args.steal_hold_threshold}%")
    if max_steal is not None and max_steal >= args.steal_max_hold_threshold:
        contention_indicators.append(f"max_steal={max_steal:.2f}%>= {args.steal_max_hold_threshold}%")

    contention = bool(contention_indicators)
    usage_pattern = "bursty usage" if bursty else "steady usage"

    # -------------------------------
    # CPU recommendation (conservative default, but not paralyzing)
    # -------------------------------
    vcpu_rec_percentile = vcpus
    if p_cpu is not None:
        demand = (p_cpu / 100.0) * (1.0 + args.cpu_headroom)
        vcpu_rec_percentile = max(1, int(math.ceil(vcpus * (demand / max(0.05, args.cpu_target)))))

    vcpu_rec = vcpus  # default: no downsize

    if bursty:
        notes.append("CPU downsizing blocked: burst classification triggered (batch/peak protection).")
    if contention:
        notes.append("CPU downsizing blocked: steal indicates possible hypervisor contention.")

    if (not bursty) and (not contention):
        # Allow downsize in small steps
        if vcpu_rec_percentile < vcpus:
            step = max(0, args.max_vcpu_reduction)
            vcpu_rec = max(vcpus - step, vcpu_rec_percentile)
        else:
            vcpu_rec = vcpu_rec_percentile
    else:
        # Never recommend below current when blocked; allow increases if percentile indicates underprovisioning
        vcpu_rec = max(vcpus, vcpu_rec_percentile)

    # -------------------------------
    # Memory recommendation (zram + disk-swap-aware guardrails)
    # -------------------------------
    mem_rec_mb = mem_mb or 0
    if p_ws is not None:
        mem_rec_mb = int(math.ceil(p_ws * (1.0 + args.mem_headroom) + args.os_reserve_mb))

    # Detect swap activity from sar
    swap_activity = False
    if (p_swpin or 0.0) > 0.0 or (p_swpout or 0.0) > 0.0:
        swap_activity = True
        notes.append("Swap in/out activity detected.")
    if (p_swpused or 0.0) > 0.0:
        swap_activity = True
        notes.append("Swap space in use.")

    # Apply swap policy
    if mem_mb is not None and swap_activity and mem_rec_mb < mem_mb:

        if swap_type == "disk":
            # Disk swap actively used → hard stop
            mem_rec_mb = mem_mb
            notes.append("Disk-backed swap is in use; memory downsizing blocked.")
            if confidence == "high":
                confidence = "medium"

        elif swap_type == "zram":
            # zram swap (disk swap unused or absent) → soft signal
            notes.append("zram swap in use; treating swap activity as memory compression.")

            # Add extra safety headroom
            extra_headroom = 0.10
            if p_ws is not None:
                mem_rec_mb = int(math.ceil(
                    p_ws * (1.0 + args.mem_headroom + extra_headroom)
                    + args.os_reserve_mb
                ))

            # Cap reduction (do not shrink aggressively)
            max_reduction_pct = 0.20
            floor_mb = int(mem_mb * (1.0 - max_reduction_pct))
            if mem_rec_mb < floor_mb:
                mem_rec_mb = floor_mb
                notes.append("zram present: memory reduction capped at 20%.")

            if confidence == "high":
                confidence = "medium"

        else:
            # Unknown swap state → conservative fallback
            mem_rec_mb = mem_mb
            notes.append("Swap activity detected but swap type unclear; memory downsizing blocked.")
            if confidence == "high":
                confidence = "medium"


    # Floors
    vcpu_rec = max(1, vcpu_rec)
    mem_rec_mb = max(256, mem_rec_mb)

    result = {
        "host": host,
        "window_days_requested": args.days,
        "sa_files_used": len(sa_files),

        "classification": {
            "usage_pattern": usage_pattern,  # 'steady usage' or 'bursty usage'
            "bursty": bursty,
            "burst_indicators": burst_indicators,
            "contention_detected": contention,
            "contention_indicators": contention_indicators,

            # Expose the knobs used (helps with auditability)
            "burst_busy_threshold": burst_level,
            "burst_sustained_samples": args.burst_sustained_samples,
            "burst_recurring_samples": args.burst_recurring_samples,
            "burst_runq_threshold": runq_threshold,
            "require_runq_to_confirm_burst": bool(args.require_runq_to_confirm_burst),
            "burst_percentile": args.burst_percentile,
            "p_burst_busy_pct": p_burst,
            "burst_qualified_samples": recurring_count
        },

        "current": {
            "vcpus": vcpus,
            "mem_mb": mem_mb
        },

        "cpu": {
            f"p{args.cpu_percentile}_busy_pct": p_cpu,
            f"p{args.cpu_percentile}_iowait_pct": p_iow,
            f"p{args.cpu_percentile}_steal_pct": p_steal,
            f"p{args.cpu_percentile}_runq_sz": p_runq,

            "max_busy_pct": max_cpu,
            "max_runq_sz": max_runq,
            "max_steal_pct": max_steal,

            "cpu_samples_parsed": len(cpu_busy),
            "queue_samples_parsed": len(runq),
            "cpu_timestamps_parsed": len(cpu_by_ts),
            "queue_timestamps_parsed": len(runq_by_ts),
            "aligned_timestamps_evaluated": len(aligned)
        },

        "memory": {
            f"p{args.mem_percentile}_workingset_mb": p_ws,
            f"p{args.mem_percentile}_commit_pct": p_commit,
            "p95_pswpin_per_s": p_swpin,
            "p95_pswpout_per_s": p_swpout,
            "p95_swpused_pct": p_swpused,
            "mem_samples_parsed": len(ws_mb)
        },
        "swap": {
            "swap_type": swap_type,
            "disk_swap_used": disk_swap_used
        },
        "recommendation": {
            "vcpu_recommended": vcpu_rec,
            "vcpu_percentile_advisory": vcpu_rec_percentile,
            "cpu_target_util": args.cpu_target,
            "cpu_headroom": args.cpu_headroom,
            "max_vcpu_reduction_per_step": args.max_vcpu_reduction,

            "mem_mb_recommended": mem_rec_mb,
            "mem_gb_recommended": round(mem_rec_mb / 1024.0, 2),
            "mem_headroom": args.mem_headroom,
            "os_reserve_mb": args.os_reserve_mb,

            "confidence": confidence,
            "notes": notes
        }
    }

    print(json.dumps(result, indent=2))
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
