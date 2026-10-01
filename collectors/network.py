"""网络速率采集：基于网卡累计字节数做差分，支持同时监控多张网卡。

配置项见 config.toml [network]：
  items = ["enp3s0", "wlan0"]                         # 简写：只写网卡名
  [[network.items]]  name = "有线"  interface = "enp3s0"  # 完整写法
  都不配时退回单目标：interface（指定网卡）或“汇总所有网卡”

每个目标（target）维护自己的差分基准，互不影响。
第一次采样没有上一次的基准，返回 0（而不是报错）；网卡中途消失只影响该行。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

import psutil

import config

#: 汇总模式的内部 key（interface 为 None 时）
_TOTAL_KEY = "__total__"


class NetworkSpeedMonitor:
    """线程安全的网卡速率计算器，可同时跟踪多张网卡。"""

    def __init__(
        self,
        targets: Optional[list[tuple[str, Optional[str]]]] = None,
        include_loopback: bool = True,
        min_interval: float = 0.05,
    ) -> None:
        # [(显示名, 网卡名 | None)]；None 表示汇总所有网卡
        self._targets: list[tuple[str, Optional[str]]] = list(targets) if targets else [("", None)]
        self._include_loopback = include_loopback
        self._min_interval = max(float(min_interval), 0.0)
        self._lock = threading.Lock()
        self._last_bytes: dict[str, tuple[int, int]] = {}
        self._last_time: dict[str, float] = {}
        self._last_result: dict[str, tuple[float, float]] = {}

    # -- 内部工具 ---------------------------------------------------------
    @staticmethod
    def _key(interface: Optional[str]) -> str:
        return interface or _TOTAL_KEY

    def _read_counters(self, interface: Optional[str]) -> tuple[Optional[tuple[int, int]], Optional[str]]:
        """读取累计字节数，返回 ((sent, recv), error)。"""
        try:
            if interface:
                pernic = psutil.net_io_counters(pernic=True) or {}
                if interface not in pernic:
                    return None, f"网卡不存在：{interface}"
                counters = pernic[interface]
                return (int(counters.bytes_sent), int(counters.bytes_recv)), None

            if self._include_loopback:
                counters = psutil.net_io_counters()
                return (int(counters.bytes_sent), int(counters.bytes_recv)), None

            # 只统计非回环网卡
            pernic = psutil.net_io_counters(pernic=True) or {}
            sent = recv = 0
            for name, counters in pernic.items():
                if name == "lo" or name.startswith("lo:"):
                    continue
                sent += int(counters.bytes_sent)
                recv += int(counters.bytes_recv)
            return (sent, recv), None
        except Exception as exc:  # noqa: BLE001
            return None, f"无法读取网络计数器：{exc}"

    def _diff(self, key: str, counters: tuple[int, int]) -> tuple[float, float]:
        """与上一次采样做差，返回 (下载 bps, 上传 bps)。"""
        sent, recv = counters
        now = time.monotonic()

        with self._lock:
            if key not in self._last_bytes or key not in self._last_time:
                # 第一次采集：只记录基准，速度为 0
                self._last_bytes[key] = (sent, recv)
                self._last_time[key] = now
                self._last_result[key] = (0.0, 0.0)
                return self._last_result[key]

            elapsed = now - self._last_time[key]
            if elapsed <= self._min_interval:
                # 采样间隔过短，沿用上次结果，避免数值被异常放大
                return self._last_result.get(key, (0.0, 0.0))

            prev_sent, prev_recv = self._last_bytes[key]
            sent_delta = max(sent - prev_sent, 0)  # 计数器回绕/网卡重插时按 0 处理
            recv_delta = max(recv - prev_recv, 0)
            self._last_bytes[key] = (sent, recv)
            self._last_time[key] = now
            self._last_result[key] = (round(recv_delta / elapsed, 1), round(sent_delta / elapsed, 1))
            return self._last_result[key]

    # -- 对外接口 ---------------------------------------------------------
    def sample(self) -> list[dict[str, Any]]:
        """返回每个目标的上下行速率（字节/秒）。"""
        results: list[dict[str, Any]] = []
        for name, interface in self._targets:
            entry: dict[str, Any] = {
                "name": name,
                "interface": interface,
                "download_bps": None,
                "upload_bps": None,
                "error": None,
            }
            counters, error = self._read_counters(interface)
            if counters is None:
                entry["error"] = error
                results.append(entry)
                continue
            download_bps, upload_bps = self._diff(self._key(interface), counters)
            entry["download_bps"] = download_bps
            entry["upload_bps"] = upload_bps
            results.append(entry)
        return results


def _network_targets(items: Optional[list] = None) -> list[tuple[str, Optional[str]]]:
    """解析要监控的网卡列表，返回 [(显示名, 网卡名 | None)]。"""
    raw = getattr(config, "NETWORK_ITEMS", None) if items is None else items
    targets: list[tuple[str, Optional[str]]] = []
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            if isinstance(entry, dict):
                interface = str(entry.get("interface") or "").strip()
                if interface:
                    targets.append((str(entry.get("name") or "").strip(), interface))
            elif isinstance(entry, str) and entry.strip():
                targets.append(("", entry.strip()))
    if targets:
        return targets
    # 未配置 items：单目标（指定网卡或汇总）
    interface = getattr(config, "NETWORK_INTERFACE", None)
    interface = str(interface).strip() if interface else None
    return [("", interface or None)]


# 全局单例（按配置初始化），保证差分基准在多次 API 调用之间连续
_monitor = NetworkSpeedMonitor(
    targets=_network_targets(),
    include_loopback=getattr(config, "NETWORK_INCLUDE_LOOPBACK", True),
    min_interval=getattr(config, "NETWORK_MIN_INTERVAL_S", 0.05),
)


def get_network_speeds() -> list[dict[str, Any]]:
    """获取所有目标的当前网络速率（总开关关闭时返回配置中已关闭的说明）。"""
    if not getattr(config, "NETWORK_ENABLED", True):
        return [
            {"name": name, "interface": None, "download_bps": None, "upload_bps": None, "error": "已在配置中关闭"}
            for name, _ in _monitor._targets
        ]
    return _monitor.sample()
