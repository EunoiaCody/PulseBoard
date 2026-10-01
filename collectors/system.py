"""系统基础指标：CPU 占用率、内存、Swap、运行时间、CPU 温度、磁盘使用率。

所有可调参数均来自 config.py；任何一项读取失败都返回 None / 不可用，不抛异常。
"""

from __future__ import annotations

import glob
import os
from typing import Any, Optional

import psutil

import config

GB = 1024 ** 3

# 默认值（当 config 中缺少对应项时使用，保证模块可独立运行）
_DEFAULT_TEMP_CHIPS = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz", "it87")
_DEFAULT_LABEL_HINTS = ("package", "tctl", "tdie", "cpu", "core")
_DEFAULT_ZONE_KEYWORDS = ("cpu", "x86_pkg", "soc", "pkg")


def _cfg(name: str, default: Any) -> Any:
    """安全读取配置项（缺失时返回默认值）。"""
    return getattr(config, name, default)


# ---------------------------------------------------------------------------
# CPU 占用率
# ---------------------------------------------------------------------------
def get_cpu_usage() -> Optional[float]:
    """当前 CPU 总占用率（非阻塞，取上次调用以来的平均值）。"""
    if not _cfg("CPU_USAGE_ENABLED", True):
        return None
    try:
        return round(float(psutil.cpu_percent(interval=None)), 1)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CPU 温度
# ---------------------------------------------------------------------------
def get_cpu_temperature() -> dict[str, Any]:
    """CPU 温度：优先 psutil 传感器，其次 sysfs thermal_zone；失败返回不可用。"""
    if not _cfg("CPU_TEMP_ENABLED", True):
        return {"value": None, "available": False, "reason": "已在配置中关闭"}

    min_c = float(_cfg("CPU_TEMP_MIN_C", -20.0))
    max_c = float(_cfg("CPU_TEMP_MAX_C", 150.0))

    # 1) psutil 传感器（按配置的芯片优先级）
    try:
        sensors = psutil.sensors_temperatures(fahrenheit=False) or {}
        for chip in _cfg("CPU_TEMP_CHIPS", _DEFAULT_TEMP_CHIPS):
            entries = sensors.get(chip)
            if entries:
                value = _pick_temp_entry(entries, min_c, max_c)
                if value is not None:
                    return {"value": round(value, 1), "available": True, "reason": None}
        # 未命中已知芯片名时，退化为任意一个可用读数
        for entries in sensors.values():
            value = _pick_temp_entry(entries, min_c, max_c)
            if value is not None:
                return {"value": round(value, 1), "available": True, "reason": None}
    except Exception:
        pass

    # 2) sysfs thermal zone 兜底
    value = _read_thermal_zone(min_c, max_c)
    if value is not None:
        return {"value": round(value, 1), "available": True, "reason": None}

    return {"value": None, "available": False, "reason": "未检测到可用的 CPU 温度传感器"}


def _pick_temp_entry(entries, min_c: float, max_c: float) -> Optional[float]:
    """从传感器条目中挑选一个合理的温度值（优先匹配标签关键词）。"""
    hints = tuple(str(h).lower() for h in _cfg("CPU_TEMP_LABEL_HINTS", _DEFAULT_LABEL_HINTS))
    fallback = None
    for entry in entries:
        try:
            current = float(getattr(entry, "current", None))
        except (TypeError, ValueError):
            continue
        if not (min_c < current < max_c):
            continue
        label = (getattr(entry, "label", "") or "").lower()
        if any(hint in label for hint in hints):
            return current
        if fallback is None:
            fallback = current
    return fallback


def _read_thermal_zone(min_c: float, max_c: float) -> Optional[float]:
    """读取 /sys/class/thermal/thermal_zone* 作为温度兜底。"""
    pattern = str(_cfg("CPU_TEMP_ZONE_GLOB", "/sys/class/thermal/thermal_zone*"))
    keywords = tuple(str(k).lower() for k in _cfg("CPU_TEMP_ZONE_KEYWORDS", _DEFAULT_ZONE_KEYWORDS))
    try:
        for zone in sorted(glob.glob(pattern)):
            zone_type = ""
            try:
                with open(os.path.join(zone, "type"), "r", encoding="utf-8", errors="ignore") as fh:
                    zone_type = fh.read().strip().lower()
            except Exception:
                pass
            if zone_type and keywords and not any(k in zone_type for k in keywords):
                continue
            try:
                with open(os.path.join(zone, "temp"), "r", encoding="utf-8", errors="ignore") as fh:
                    raw = float(fh.read().strip())
            except Exception:
                continue
            if raw > 1000:  # 毫摄氏度
                raw = raw / 1000.0
            if min_c < raw < max_c:
                return raw
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# 内存 / Swap
# ---------------------------------------------------------------------------
def get_memory() -> dict[str, Any]:
    """内存与 Swap 使用情况。"""
    result: dict[str, Any] = {
        "used_gb": None,
        "total_gb": None,
        "percent": None,
        "available_gb": None,
        "swap_used_gb": None,
        "swap_total_gb": None,
        "swap_percent": None,
    }
    if not _cfg("MEMORY_ENABLED", True):
        return result

    try:
        vm = psutil.virtual_memory()
        used = max(vm.total - vm.available, 0)
        result.update(
            {
                "used_gb": round(used / GB, 2),
                "total_gb": round(vm.total / GB, 2),
                "percent": round(float(vm.percent), 1),
                "available_gb": round(vm.available / GB, 2),
            }
        )
    except Exception:
        pass

    try:
        swap = psutil.swap_memory()
        result.update(
            {
                "swap_used_gb": round(swap.used / GB, 2),
                "swap_total_gb": round(swap.total / GB, 2),
                "swap_percent": round(float(swap.percent), 1),
            }
        )
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# 运行时间
# ---------------------------------------------------------------------------
def get_uptime() -> dict[str, Any]:
    """系统运行时间，附带人类可读文本（模板可在 config 中配置）。"""
    if not _cfg("UPTIME_ENABLED", True):
        return {"seconds": None, "text": None}
    try:
        seconds = max(int(_now() - psutil.boot_time()), 0)
    except Exception:
        return {"seconds": None, "text": None}
    return {"seconds": seconds, "text": format_uptime(seconds)}


def _now() -> float:
    import time

    return time.time()


def format_uptime(seconds: int) -> str:
    """按 UPTIME_TEXT_TEMPLATE 格式化运行时间。"""
    seconds = max(int(seconds), 0)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)

    template = str(_cfg("UPTIME_TEXT_TEMPLATE", "{days}天 {hours}小时 {minutes}分钟"))
    if _cfg("UPTIME_HIDE_ZERO_DAYS", True) and days == 0:
        template = template.replace("{days}天", "").replace("{days} 天", "")

    try:
        text = template.format(days=days, hours=hours, minutes=minutes, seconds=secs)
    except Exception:
        text = f"{days}天 {hours}小时 {minutes}分钟"
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# 磁盘
# ---------------------------------------------------------------------------
def get_disk_usage(path: Optional[str] = None, name: str = "") -> dict[str, Any]:
    """单个路径所在文件系统的磁盘使用率；失败时把原因写进 error。"""
    target = path or str(_cfg("DISK_PATH", "/"))
    item: dict[str, Any] = {
        "name": name,
        "path": target,
        "used_gb": None,
        "total_gb": None,
        "free_gb": None,
        "percent": None,
        "error": None,
    }
    if not _cfg("DISK_ENABLED", True):
        item["error"] = "已在配置中关闭"
        return item
    try:
        usage = psutil.disk_usage(target)
    except Exception as exc:  # noqa: BLE001
        # 只取简短原因（如 “No such file or directory”），避免把路径重复写两遍
        reason = getattr(exc, "strerror", None) or str(exc)
        item["error"] = f"无法读取 {target}：{reason}"
        return item
    item.update(
        {
            "used_gb": round(usage.used / GB, 2),
            "total_gb": round(usage.total / GB, 2),
            "free_gb": round(usage.free / GB, 2),
            "percent": round(float(usage.percent), 1),
        }
    )
    return item


def _disk_targets(items: Optional[list] = None) -> list[tuple[str, str]]:
    """解析要监控的分区列表，返回 [(显示名, 路径)]。

    支持三种写法（见 config.toml [disk]）：
      items = ["/", "/data"]                     # 简写：只有路径
      [[disk.items]]  name = "数据盘"  path = "/data"   # 完整写法
      都不配时退回单一路径 disk.path
    """
    raw = _cfg("DISK_ITEMS", None) if items is None else items
    targets: list[tuple[str, str]] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            if isinstance(entry, dict):
                path = str(entry.get("path") or "").strip()
                if path:
                    targets.append((str(entry.get("name") or "").strip(), path))
            elif isinstance(entry, str) and entry.strip():
                targets.append(("", entry.strip()))
    if targets:
        return targets
    return [("", str(_cfg("DISK_PATH", "/") or "/"))]


def get_disks(items: Optional[list] = None) -> list[dict[str, Any]]:
    """按配置返回每个分区的使用率；单个分区失败不影响其它分区。"""
    return [get_disk_usage(path, name) for name, path in _disk_targets(items)]
