"""GPU 占用率采集：按顺序自动尝试多个数据源，兼容 NVIDIA / AMD / Intel / ARM。

数据源（存在哪个用哪个，都不存在时显示“不可用”，无需按显卡品牌改配置）：

  1. NVIDIA          nvidia-smi --query-gpu=utilization.gpu（多卡取平均）
  2. AMD（amdgpu）    /sys/class/drm/card*/device/gpu_busy_percent（本身就是百分比）
  3. Intel（i915/xe） RC6 / GT idle 常驻计数器差分：busy% = 100 - Δidle / Δwall
                     内核没有类似 gpu_busy_percent 的文件，只能这样算
  4. ARM / 嵌入式      devfreq 的 load 文件（Mali 等），格式 “百分比@频率Hz”

说明：
  * 第 3 类数据源靠两次采样求差，第一次调用返回“不可用”，一个刷新周期后即有值；
    采样间隔过短时沿用上次结果，避免多个浏览器同时轮询导致数值抖动。
  * 单个数据源失败只影响 GPU 这一行，不影响其它指标。
  * GPU 功耗是另一条独立的回退链，见 collectors/power.py。
"""

from __future__ import annotations

import glob
import shutil
import subprocess
import threading
import time
from typing import Any, Optional

import config

_NA_VALUES = {"n/a", "[n/a]", "not supported", "unknown", ""}

# 差分型数据源的状态：path -> 上一次的 (计数器值, 单调时钟)
_residency_lock = threading.Lock()
_residency_state: dict[str, tuple[int, float]] = {}
_residency_last: dict[str, float] = {}


def _cfg(name: str, default: Any) -> Any:
    """安全读取配置项（缺失时返回默认值）。"""
    return getattr(config, name, default)


# ---------------------------------------------------------------------------
# 通用文件 / 数值读取
# ---------------------------------------------------------------------------
def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read().strip()
    except Exception:
        return None


def _read_number(path: str) -> Optional[float]:
    raw = _read_text(path)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _in_range(value: float) -> bool:
    return float(_cfg("GPU_MIN_PERCENT", 0.0)) <= value <= float(_cfg("GPU_MAX_PERCENT", 100.0))


def _result(value: float, source: str) -> dict[str, Any]:
    return {"percent": round(value, 1), "available": True, "source": source, "error": None}


def _missing(reason: Optional[str] = None) -> dict[str, Any]:
    return {"percent": None, "available": False, "source": None, "error": reason}


# ---------------------------------------------------------------------------
# 1) NVIDIA
# ---------------------------------------------------------------------------
def _from_nvidia() -> Optional[dict[str, Any]]:
    """nvidia-smi --query-gpu=utilization.gpu；多卡时取平均值。"""
    executable = str(_cfg("NVIDIA_SMI_PATH", "nvidia-smi"))
    if not shutil.which(executable):
        return None  # 没有这个命令就静默跳到下一个数据源

    query = str(_cfg("GPU_NVIDIA_SMI_QUERY", "utilization.gpu"))
    timeout = float(_cfg("NVIDIA_SMI_TIMEOUT", 3.0))
    try:
        proc = subprocess.run(  # noqa: S603 - 命令与参数来自本地配置
            [executable, f"--query-gpu={query}", "--format=csv,noheader,nounits"],
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

    # 只取查询结果的第一列（默认就是 utilization.gpu）
    values: list[float] = []
    for line in (proc.stdout or "").splitlines():
        first = line.strip().split(",")[0].strip().lower()
        if first in _NA_VALUES:
            continue
        try:
            value = float(first)
        except ValueError:
            continue
        if _in_range(value):
            values.append(value)
    if not values:
        return None

    average = sum(values) / len(values)
    source = "nvidia-smi" if len(values) == 1 else f"nvidia-smi（{len(values)} 张卡平均）"
    return _result(average, source)


# ---------------------------------------------------------------------------
# 2) AMD：amdgpu 直接给出百分比
# ---------------------------------------------------------------------------
def _from_busy_percent() -> Optional[dict[str, Any]]:
    """amdgpu 的 gpu_busy_percent：文件内容就是 0-100 的整数。"""
    patterns = _cfg("GPU_BUSY_PERCENT_GLOBS", None) or []
    for pattern in patterns:
        for path in sorted(glob.glob(str(pattern))):
            value = _read_number(path)
            if value is None or not _in_range(value):
                continue
            return _result(value, "amdgpu gpu_busy_percent")
    return None


# ---------------------------------------------------------------------------
# 3) Intel：RC6 / GT idle 常驻计数器差分
# ---------------------------------------------------------------------------
def _busy_from_residency(path: str) -> Optional[float]:
    """由空闲常驻计数器算占用率：busy% = 100 - Δidle / Δwall。"""
    counter = _read_number(path)
    if counter is None:
        return None

    now = time.monotonic()
    min_interval = float(_cfg("GPU_MIN_INTERVAL_S", 0.5))

    with _residency_lock:
        previous = _residency_state.get(path)
        _residency_state[path] = (int(counter), now)
        if previous is None:
            return None  # 第一次：只记录基准
        previous_counter, previous_time = previous
        elapsed = now - previous_time
        if elapsed <= min_interval:
            # 间隔太短（例如多个浏览器同时轮询）：沿用上次结果，避免数值抖动
            return _residency_last.get(path)

        idle_ms = counter - previous_counter
        if idle_ms < 0:
            return _residency_last.get(path)  # 计数器被重置（挂起/驱动重载）

        idle_ratio = idle_ms / (elapsed * 1000.0)
        busy = (1.0 - min(max(idle_ratio, 0.0), 1.0)) * 100.0
        if not _in_range(busy):
            return _residency_last.get(path)
        _residency_last[path] = round(busy, 1)
        return _residency_last[path]


def _from_residency() -> tuple[Optional[dict[str, Any]], bool]:
    """返回 (结果, 是否存在“等第二次采样”的候选数据源)。"""
    patterns = _cfg("GPU_IDLE_RESIDENCY_GLOBS", None) or []
    pending = False
    for pattern in patterns:
        for path in sorted(glob.glob(str(pattern))):
            busy = _busy_from_residency(path)
            if busy is None:
                pending = True  # 文件在、只是还没攒够两次采样
                continue
            source = "Intel i915 RC6 差分" if "rc6" in path else "Intel GT idle 差分"
            return _result(busy, source), pending
    return None, pending


# ---------------------------------------------------------------------------
# 4) ARM / 嵌入式：devfreq 的 load（“百分比@频率Hz”）
# ---------------------------------------------------------------------------
def _from_devfreq() -> Optional[dict[str, Any]]:
    patterns = _cfg("GPU_DEVFREQ_LOAD_GLOBS", None) or []
    keywords = [str(k).lower() for k in (_cfg("GPU_DEVFREQ_KEYWORDS", None) or [])]
    for pattern in patterns:
        for path in sorted(glob.glob(str(pattern))):
            # /sys/class/devfreq/<设备名>/load —— 按设备名筛出 GPU（ARM 上常为 ffe40000.gpu）
            device = path.rsplit("/", 2)[-2] if path.count("/") >= 2 else ""
            if keywords and not any(keyword in device.lower() for keyword in keywords):
                continue
            raw = _read_text(path)
            if not raw:
                continue
            percent_text, _, freq_text = raw.partition("@")
            try:
                value = float(percent_text.strip())
            except ValueError:
                continue
            if not _in_range(value):
                continue
            source = "devfreq load"
            freq_hz = freq_text.strip().rstrip("Hz").strip()
            if freq_hz:
                try:
                    source = f"devfreq load（{int(float(freq_hz)) // 1_000_000} MHz）"
                except ValueError:
                    pass
            return _result(value, source)
    return None


# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------
def get_gpu_usage() -> dict[str, Any]:
    """GPU 占用率（百分比）。任何失败都只是返回“不可用”并说明原因。"""
    if not _cfg("GPU_ENABLED", True):
        return _missing("已在配置中关闭")

    try:
        result = _from_nvidia()
        if result:
            return result
    except Exception:  # noqa: BLE001 - 单个数据源失败不能影响其它数据源
        pass

    try:
        result = _from_busy_percent()
        if result:
            return result
    except Exception:  # noqa: BLE001
        pass

    try:
        result, pending = _from_residency()
        if result:
            return result
    except Exception:  # noqa: BLE001
        pending = False

    try:
        result = _from_devfreq()
        if result:
            return result
    except Exception:  # noqa: BLE001
        pass

    if pending:
        return _missing("空闲计数器差分需要两次采样，约 1 秒后自动显示")
    return _missing("未检测到可读取占用率的 GPU")
