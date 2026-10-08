from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import psutil

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _directory_size_bytes(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_file():
            try:
                total += entry.stat().st_size
            except OSError:
                pass
    return total


def _process_tree_rss_bytes(process: psutil.Process) -> int:
    try:
        total = process.memory_info().rss
    except psutil.Error:
        return 0
    for child in process.children(recursive=True):
        try:
            total += child.memory_info().rss
        except psutil.Error:
            continue
    return total


def _process_tree_cpu_seconds(process: psutil.Process) -> float:
    try:
        times = process.cpu_times()
        total = times.user + times.system
    except psutil.Error:
        return 0.0
    for child in process.children(recursive=True):
        try:
            child_times = child.cpu_times()
            total += child_times.user + child_times.system
        except psutil.Error:
            continue
    return total


@dataclass
class _Snapshot:
    wall_start: float
    cpu_seconds_start: float
    disk_bytes_start: int
    peak_rss_bytes: int
    stop_event: threading.Event = field(default_factory=threading.Event)
    sampler_thread: threading.Thread = None


_active: _Snapshot = None


def start(sample_interval: float = 0.2) -> None:
    """Start tracking this process's (and its children's) RAM, CPU and project-disk
    usage. Call report() later to print the usage since this call.
    """
    global _active

    process = psutil.Process(os.getpid())

    snapshot = _Snapshot(
        wall_start=time.time(),
        cpu_seconds_start=_process_tree_cpu_seconds(process),
        disk_bytes_start=_directory_size_bytes(PROJECT_ROOT),
        peak_rss_bytes=_process_tree_rss_bytes(process),
    )

    def _sample_peak_rss() -> None:
        while not snapshot.stop_event.wait(sample_interval):
            snapshot.peak_rss_bytes = max(snapshot.peak_rss_bytes, _process_tree_rss_bytes(process))

    snapshot.sampler_thread = threading.Thread(target=_sample_peak_rss, daemon=True)
    snapshot.sampler_thread.start()

    _active = snapshot
    print(f"[execution_statistics] tracking started (pid={process.pid})")


def report() -> dict:
    """Stop tracking and print the RAM, CPU and project-disk usage since the last start()."""
    global _active

    if _active is None:
        print("[execution_statistics] report() called without a matching start() - nothing to report")
        return {}

    snapshot = _active
    _active = None

    snapshot.stop_event.set()
    snapshot.sampler_thread.join(timeout=2)

    process = psutil.Process(os.getpid())
    snapshot.peak_rss_bytes = max(snapshot.peak_rss_bytes, _process_tree_rss_bytes(process))

    wall_elapsed = time.time() - snapshot.wall_start
    cpu_seconds = _process_tree_cpu_seconds(process) - snapshot.cpu_seconds_start
    disk_bytes_end = _directory_size_bytes(PROJECT_ROOT)
    disk_delta = disk_bytes_end - snapshot.disk_bytes_start

    stats = {
        "wall_time_seconds": round(wall_elapsed, 2),
        "cpu_time_seconds": round(cpu_seconds, 2),
        "avg_cpu_percent": round((cpu_seconds / wall_elapsed * 100) if wall_elapsed > 0 else 0.0, 1),
        "peak_ram_mb": round(snapshot.peak_rss_bytes / (1024 ** 2), 1),
        "project_disk_usage_mb": round(disk_bytes_end / (1024 ** 2), 1),
        "project_disk_delta_mb": round(disk_delta / (1024 ** 2), 1),
    }

    print("============ execution_statistics ===================")
    for key, value in stats.items():
        print(f"  {key}: {value}")

    return stats
