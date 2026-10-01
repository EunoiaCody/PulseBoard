"""功耗采集：GPU（多源）、CPU（RAPL package 域）、整机（RAPL psys / power_supply）。

严格区分三类功耗，避免把 GPU 功耗当作整机功耗：
  - gpu_watts   : GPU 功耗（见下方回退链）
  - cpu_watts   : CPU/封装功耗（RAPL package 域）
  - total_watts : 整机/平台功耗（RAPL psys 域，或电源供应器 power_now）
所有开关与路径均来自 config.py；任一项失败只返回 None。

GPU 功耗回退链（按顺序尝试，某个源不存在就跳过，不需要改配置就能兼容）：
  1. NVIDIA 独显      nvidia-smi --query-gpu=power.draw
  2. AMD/Intel 独显   hwmon power1_average（微瓦，amdgpu/i915 驱动导出）
  3. Intel 核显       RAPL 名为 uncore 的域
  都没有则返回 None。例如 AMD APU 的核显与 CPU 共享一个 package 域，
  没有独立的 GPU 功耗节点，此时显示“不可用”比估算一个假数字更诚实。
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import threading
import time
from typing import Any, Optional

import config

# ---------------------------------------------------------------------------
# RAPL（Linux powercap）采样状态
# ---------------------------------------------------------------------------
_rapl_lock = threading.Lock()
# path -> (energy_uj, monotonic_ts)
_rapl_state: dict[str, tuple[int, float]] = {}
# path -> 最近一次计算出的功率
_rapl_last_watts: dict[str, float] = {}

_NA_VALUES = {"n/a", "[n/a]", "not supported", "unknown", ""}


def _cfg(name: str, default: Any) -> Any:
    """安全读取配置项（缺失时返回默认值）。"""
    return getattr(config, name, default)


# ---------------------------------------------------------------------------
# 通用文件读取
# ---------------------------------------------------------------------------
def _read_text(path: str) -> Optional[str]:
    """读取文本文件，失败返回 None。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read().strip()
    except Exception:
        return None


def _read_int(path: str) -> Optional[int]:
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# GPU 功耗（NVIDIA）
# ---------------------------------------------------------------------------
def _query_nvidia_smi(query: str) -> Optional[str]:
    """执行 nvidia-smi 查询，失败返回 None（命令不存在也不会抛错）。"""
    executable = str(_cfg("NVIDIA_SMI_PATH", "nvidia-smi"))
    if not shutil.which(executable):
        # 本机没装 nvidia-smi，直接返回，不浪费子进程
        return None
    timeout = float(_cfg("NVIDIA_SMI_TIMEOUT", 3.0))
    command = [executable, f"--query-gpu={query}", "--format=csv,noheader,nounits"]
    try:
        proc = subprocess.run(  # noqa: S603 - 命令与参数来自本地配置
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip()


def _parse_nvidia_values(output: Optional[str]) -> list[float]:
    """解析 nvidia-smi 输出中的数值列表（跳过 N/A 等无效值）。"""
    values: list[float] = []
    if not output:
        return values
    for line in output.splitlines():
        first = line.strip().split(",")[0].strip().lower()
        if first in _NA_VALUES:
            continue
        try:
            values.append(float(first))
        except ValueError:
            continue
    return values


def _get_hwmon_gpu_watts() -> Optional[float]:
    """AMD/Intel 独显：从 hwmon power1_average 读取整卡功耗（微瓦 → 瓦）。

    power1_average 通常由 amdgpu / i915 等驱动在
    /sys/class/drm/card*/device/hwmon/hwmon*/ 下导出，数值单位是微瓦。
    多张卡（多个匹配文件）时相加。
    """
    patterns = _cfg("GPU_HWMON_POWER_FILES", None)
    if not patterns:
        return None
    if isinstance(patterns, str):
        patterns = [patterns]

    max_watts = float(_cfg("RAPL_MAX_WATTS", 2000.0))
    total_watts = 0.0
    found = False
    for pattern in patterns:
        try:
            paths = glob.glob(str(pattern))
        except Exception:
            continue
        for path in paths:
            raw = _read_int(path)
            if raw is None or raw <= 0:
                continue
            watts = raw / 1_000_000.0
            if watts > max_watts:  # 明显的异常读数，丢弃而不是显示荒谬的值
                continue
            total_watts += watts
            found = True
    return round(total_watts, 1) if found else None


def _get_rapl_gpu_watts() -> Optional[float]:
    """Intel 核显：GPU 功耗包含在 RAPL 的 uncore 域里。"""
    keywords = _cfg("GPU_RAPL_KEYWORDS", ["uncore"])
    if not keywords:
        return None
    return _find_rapl_watts(list(keywords))


def get_gpu_watts() -> Optional[float]:
    """GPU 功耗（瓦）：nvidia-smi → hwmon power1_average → RAPL uncore。"""
    if not _cfg("GPU_POWER_ENABLED", True):
        return None

    # 1) NVIDIA 独显
    values = _parse_nvidia_values(_query_nvidia_smi(str(_cfg("NVIDIA_SMI_QUERY", "power.draw"))))
    if values:
        if _cfg("GPU_POWER_SUM_ALL", True):
            return round(sum(values), 1)
        return round(values[0], 1)

    # 2) AMD / Intel 独显的 hwmon 功耗节点
    watts = _get_hwmon_gpu_watts()
    if watts is not None:
        return watts

    # 3) Intel 核显的 RAPL uncore 域
    return _get_rapl_gpu_watts()


# ---------------------------------------------------------------------------
# RAPL（CPU / 平台）
# ---------------------------------------------------------------------------
def _rapl_domains() -> list[tuple[str, str]]:
    """返回 [(energy_uj 路径, 域名小写)]。"""
    pattern = str(_cfg("RAPL_GLOB", "/sys/class/powercap/*/energy_uj"))
    domains: list[tuple[str, str]] = []
    try:
        for energy_path in glob.glob(pattern):
            name_path = os.path.join(os.path.dirname(energy_path), "name")
            name = (_read_text(name_path) or os.path.basename(os.path.dirname(energy_path))).lower()
            domains.append((energy_path, name))
    except Exception:
        return []
    return domains


def _rapl_watts(energy_path: str, max_energy: Optional[int]) -> Optional[float]:
    """通过两次采样的能量差计算瞬时功率（瓦）。"""
    energy = _read_int(energy_path)
    if energy is None:
        return None

    min_interval = float(_cfg("RAPL_MIN_INTERVAL_S", 0.25))
    max_watts = float(_cfg("RAPL_MAX_WATTS", 2000.0))
    now = time.monotonic()

    with _rapl_lock:
        prev = _rapl_state.get(energy_path)
        _rapl_state[energy_path] = (energy, now)
        if prev is None:
            return None
        prev_energy, prev_ts = prev
        elapsed = now - prev_ts
        if elapsed <= min_interval:
            # 间隔太短，沿用上次结果
            return _rapl_last_watts.get(energy_path)
        delta = energy - prev_energy
        if delta < 0:
            # 计数器回绕
            if max_energy:
                delta += max_energy
            else:
                return _rapl_last_watts.get(energy_path)
        watts = delta / 1_000_000.0 / elapsed
        if watts < 0 or watts > max_watts:
            return _rapl_last_watts.get(energy_path)
        _rapl_last_watts[energy_path] = round(watts, 1)
        return _rapl_last_watts[energy_path]


def _find_rapl_watts(keywords: list) -> Optional[float]:
    """按域名关键词查找并计算 RAPL 功率。"""
    lowered = [str(k).lower() for k in keywords]
    try:
        for energy_path, name in _rapl_domains():
            if not any(keyword in name for keyword in lowered):
                continue
            max_energy = _read_int(os.path.join(os.path.dirname(energy_path), "max_energy_range_uj"))
            watts = _rapl_watts(energy_path, max_energy)
            if watts is not None:
                return watts
    except Exception:
        return None
    return None


def get_cpu_watts() -> Optional[float]:
    """CPU（RAPL package 域）功耗，读取失败返回 None。"""
    if not _cfg("CPU_POWER_ENABLED", True):
        return None
    return _find_rapl_watts(_cfg("RAPL_PACKAGE_KEYWORDS", ["package"]))


def get_total_watts() -> Optional[float]:
    """整机功耗：优先 RAPL psys 平台域，其次电源供应器 power_now。"""
    if not _cfg("TOTAL_POWER_ENABLED", True):
        return None

    # 1) RAPL psys
    watts = _find_rapl_watts(_cfg("RAPL_PSYS_KEYWORDS", ["psys"]))
    if watts is not None:
        return watts

    # 2) /sys/class/power_supply/*/power_now（微瓦）
    pattern = str(_cfg("POWER_SUPPLY_GLOB", "/sys/class/power_supply/*/power_now"))
    try:
        values: list[float] = []
        for power_now in glob.glob(pattern):
            raw = _read_int(power_now)
            if raw is None or raw <= 0:
                continue
            values.append(raw / 1_000_000.0)
        if values:
            return round(max(values), 1)
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------
def get_power() -> dict[str, Any]:
    """聚合功耗信息，任一项失败都不影响其它项。"""
    if not _cfg("POWER_ENABLED", True):
        return {"cpu_watts": None, "gpu_watts": None, "total_watts": None, "notes": ["已在配置中关闭功耗采集"]}

    try:
        gpu = get_gpu_watts()
    except Exception:
        gpu = None

    try:
        cpu = get_cpu_watts()
    except Exception:
        cpu = None

    try:
        total = get_total_watts()
    except Exception:
        total = None

    notes: list[str] = []
    if cpu is None and total is None:
        notes.append("CPU/整机功耗不可用（RAPL 需 intel_rapl/amd_rapl 驱动与读取权限）")
    if gpu is None:
        notes.append("GPU 功耗不可用（无 nvidia-smi / hwmon 功耗节点 / RAPL uncore 域）")

    return {
        "cpu_watts": cpu,
        "gpu_watts": gpu,
        "total_watts": total,
        "notes": notes,
    }
