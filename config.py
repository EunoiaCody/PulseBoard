# -*- coding: utf-8 -*-
"""配置加载器：从 config.toml 读取配置，并把每一项挂到本模块的同名变量上。

设计要点
--------
* **唯一配置文件是 config.toml**（TOML 原生支持 `#` 注释）。
* 本模块只负责：读取 → 合并内置默认值 → 类型纠正 → 挂载变量 → 启动自检。
* 因此任何一项缺失 / 写错 / 拼错都不会让程序崩溃：
  - 缺失 → 使用 _DEFAULTS 中的默认值；
  - 类型不对 → 尽量自动转换，失败则回退默认值并输出提示；
  - 未知项（拼写错误）→ 在启动日志里提示“未识别的配置项”。
* 业务代码仍按 `config.PAGE_TITLE` 这种方式读取，无需改动。

可用环境变量
------------
MONITOR_CONFIG
    指定其它配置文件路径，默认使用项目根目录下的 config.toml。

对外接口
--------
    CONFIG_PATH      当前使用的配置文件路径（str）
    CONFIG_LOADED    是否成功读到配置文件（bool）
    LOAD_WARNINGS    加载过程中的提示（list[str]）
    validate()       返回配置提示列表（app.py 启动时打印到日志）
    其余所有 config.X 由 config.toml 逐项生成，无需在此声明。
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

try:  # Python 3.11+ 自带 tomllib；3.10 及更早需要 tomli
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

BASE_DIR = Path(__file__).resolve().parent

#: 配置文件路径，可用环境变量 MONITOR_CONFIG 覆盖
CONFIG_PATH = os.environ.get("MONITOR_CONFIG") or str(BASE_DIR / "config.toml")

#: 是否成功读取到配置文件
CONFIG_LOADED = False

# ---------------------------------------------------------------------------
# 1. 内置默认值：TOML 路径 -> 默认值
#    这里是“所有受支持配置项”的唯一清单，config.toml 里的每一项都应能在下面找到。
# ---------------------------------------------------------------------------
_DEFAULTS: dict[str, Any] = {
    # 1) 服务
    "server.host": "0.0.0.0",
    "server.port": 8080,
    "server.reload": False,
    "server.log_level": "INFO",
    # 2) 页面
    "page.title": "服务器实时状态",
    # 3) 前端
    "frontend.refresh_interval_ms": 3000,
    "frontend.fetch_timeout_ms": 8000,
    "frontend.bar_warn_percent": 75.0,
    "frontend.bar_crit_percent": 90.0,
    "frontend.speed_mb_threshold_bps": 1048576,
    # 4) CPU
    "cpu.usage_enabled": True,
    # 5) CPU 温度
    "cpu.temp.enabled": True,
    "cpu.temp.chips": ["coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz", "it87"],
    "cpu.temp.label_hints": ["package", "tctl", "tdie", "cpu", "core"],
    "cpu.temp.zone_glob": "/sys/class/thermal/thermal_zone*",
    "cpu.temp.zone_keywords": ["cpu", "x86_pkg", "soc", "pkg"],
    "cpu.temp.min_c": -20.0,
    "cpu.temp.max_c": 150.0,
    # 6) 内存
    "memory.enabled": True,
    # 7) 运行时间
    "uptime.enabled": True,
    "uptime.text_template": "{days}天 {hours}小时 {minutes}分钟",
    "uptime.hide_zero_days": True,
    # 8) 磁盘（items 非空时监控多个分区，每项 {name, path} 或直接写路径字符串）
    "disk.enabled": True,
    "disk.path": "/",
    "disk.items": [],
    # 9) 网络（items 非空时监控多张网卡，每项 {name, interface} 或直接写网卡名字符串）
    "network.enabled": True,
    "network.interface": None,
    "network.items": [],
    "network.include_loopback": True,
    "network.min_interval_s": 0.05,
    # 10) 功耗
    "power.enabled": True,
    "power.gpu.enabled": True,
    "power.gpu.nvidia_smi_path": "nvidia-smi",
    "power.gpu.nvidia_smi_timeout": 3.0,
    "power.gpu.nvidia_smi_query": "power.draw",
    "power.gpu.sum_all": True,
    # 非 NVIDIA 的 GPU 功耗数据源（nvidia-smi 不可用时依次尝试）
    # AMD/Intel 独显：驱动导出的 hwmon power1_average（微瓦）
    "power.gpu.hwmon_power_files": ["/sys/class/drm/card*/device/hwmon/hwmon*/power1_average"],
    # Intel 核显：包含在 RAPL 的 uncore 域里
    "power.gpu.rapl_keywords": ["uncore"],
    "power.cpu.enabled": True,
    "power.cpu.rapl_glob": "/sys/class/powercap/*/energy_uj",
    "power.cpu.package_keywords": ["package"],
    "power.cpu.psys_keywords": ["psys"],
    "power.cpu.min_interval_s": 0.25,
    "power.cpu.max_watts": 2000.0,
    "power.total.enabled": True,
    "power.total.power_supply_glob": "/sys/class/power_supply/*/power_now",
    # 11) 网站检测
    "websites.enabled": True,
    "websites.timeout": 5.0,
    "websites.method": "GET",
    "websites.follow_redirects": True,
    "websites.verify_ssl": True,
    "websites.user_agent": "monitor-webui/1.0",
    "websites.headers": {},
    "websites.ok_max_code": 400,
    "websites.max_concurrency": 10,
    "websites.error_max_len": 120,
    "websites.items": [],
    # 12) CLI
    "cli.enabled": True,
    "cli.timeout": 5,
    "cli.max_output": 3000,
    "cli.stderr_max": 500,
    "cli.max_concurrency": 6,
    "cli.cwd": None,
    "cli.extra_env": {},
    "cli.commands": {},
    "cli.labels": {},
}

# ---------------------------------------------------------------------------
# 2. 变量名映射：默认按 “段名_键名” 全大写拼接，不规则的在下面显式指定
#    （例如 server.log_level 自动推导会变成 SERVER_LOG_LEVEL，这里改成 LOG_LEVEL）
# ---------------------------------------------------------------------------
_ALIASES: dict[str, str] = {
    # 服务 / 页面 / 前端
    "server.log_level": "LOG_LEVEL",
    "frontend.refresh_interval_ms": "REFRESH_INTERVAL_MS",
    "frontend.fetch_timeout_ms": "FETCH_TIMEOUT_MS",
    "frontend.bar_warn_percent": "BAR_WARN_PERCENT",
    "frontend.bar_crit_percent": "BAR_CRIT_PERCENT",
    "frontend.speed_mb_threshold_bps": "SPEED_MB_THRESHOLD_BPS",
    # 功耗（业务代码里用的是 GPU_POWER_/CPU_POWER_/RAPL_ 前缀）
    "power.gpu.enabled": "GPU_POWER_ENABLED",
    "power.gpu.nvidia_smi_path": "NVIDIA_SMI_PATH",
    "power.gpu.nvidia_smi_timeout": "NVIDIA_SMI_TIMEOUT",
    "power.gpu.nvidia_smi_query": "NVIDIA_SMI_QUERY",
    "power.gpu.sum_all": "GPU_POWER_SUM_ALL",
    "power.gpu.hwmon_power_files": "GPU_HWMON_POWER_FILES",
    "power.gpu.rapl_keywords": "GPU_RAPL_KEYWORDS",
    "power.cpu.enabled": "CPU_POWER_ENABLED",
    "power.cpu.rapl_glob": "RAPL_GLOB",
    "power.cpu.package_keywords": "RAPL_PACKAGE_KEYWORDS",
    "power.cpu.psys_keywords": "RAPL_PSYS_KEYWORDS",
    "power.cpu.min_interval_s": "RAPL_MIN_INTERVAL_S",
    "power.cpu.max_watts": "RAPL_MAX_WATTS",
    "power.total.enabled": "TOTAL_POWER_ENABLED",
    "power.total.power_supply_glob": "POWER_SUPPLY_GLOB",
    # 网站（单数 WEBSITE_ 前缀，业务代码已使用）
    "websites.items": "WEBSITES",
    "websites.timeout": "WEBSITE_TIMEOUT",
    "websites.method": "WEBSITE_METHOD",
    "websites.follow_redirects": "WEBSITE_FOLLOW_REDIRECTS",
    "websites.verify_ssl": "WEBSITE_VERIFY_SSL",
    "websites.user_agent": "WEBSITE_USER_AGENT",
    "websites.headers": "WEBSITE_HEADERS",
    "websites.ok_max_code": "WEBSITE_OK_MAX_CODE",
    "websites.max_concurrency": "WEBSITE_MAX_CONCURRENCY",
    "websites.error_max_len": "WEBSITE_ERROR_MAX_LEN",
    # CLI
    "cli.commands": "CLI_COMMANDS",
    "cli.labels": "CLI_LABELS",
}

# 这些路径的值整体作为一个配置项（字典本身是数据，不再继续展开成子配置项）
_OPAQUE_PATHS = {"cli.commands", "cli.labels", "websites.headers"}

#: 加载过程中的提示信息（app.py 启动时会打印到日志）
LOAD_WARNINGS: list[str] = []


# ---------------------------------------------------------------------------
# 3. 内部工具
# ---------------------------------------------------------------------------
def _to_python_name(toml_path: str) -> str:
    """TOML 路径 -> 模块变量名。"""
    if toml_path in _ALIASES:
        return _ALIASES[toml_path]
    return "_".join(part.upper() for part in toml_path.split("."))


def _flatten(node: Any, prefix: str = "") -> dict[str, Any]:
    """把嵌套的 TOML 表展开成 {"a.b.c": value} 形式。"""
    flat: dict[str, Any] = {}
    if not isinstance(node, dict):
        return {prefix: node} if prefix else {}
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict) and path not in _OPAQUE_PATHS:
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


def _coerce(value: Any, default: Any, path: str) -> tuple[Any, str | None]:
    """尽量把用户写的值纠正成默认值的类型；失败则回退默认值并给出提示。"""
    # 默认值为 None 的项（如网卡名、工作目录）：空字符串视为“未设置”
    if default is None:
        if isinstance(value, str) and not value.strip():
            return None, None
        return value, None

    if isinstance(default, bool):
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true", None
        if isinstance(value, (int, float)) and value in (0, 1):
            return bool(value), None
        return default, f"{path} 需要布尔值 true/false，已使用默认值 {default!r}"

    if isinstance(default, int):
        if isinstance(value, bool):
            return default, f"{path} 需要整数，已使用默认值 {default!r}"
        try:
            return int(value), None
        except (TypeError, ValueError):
            return default, f"{path} 需要整数，已使用默认值 {default!r}"

    if isinstance(default, float):
        if isinstance(value, bool):
            return default, f"{path} 需要数字，已使用默认值 {default!r}"
        try:
            return float(value), None
        except (TypeError, ValueError):
            return default, f"{path} 需要数字，已使用默认值 {default!r}"

    if isinstance(default, str):
        if isinstance(value, str):
            return value, None
        if isinstance(value, (int, float, bool)):
            return str(value), None
        return default, f"{path} 需要字符串，已使用默认值 {default!r}"

    if isinstance(default, list):
        if isinstance(value, list):
            return value, None
        return default, f"{path} 需要数组（如 [\"a\", \"b\"]），已使用默认值"

    if isinstance(default, dict):
        if isinstance(value, dict):
            return value, None
        return default, f"{path} 需要表（如 {{ k = \"v\" }}），已使用默认值"

    return value, None


# ---------------------------------------------------------------------------
# 4. 读取配置
# ---------------------------------------------------------------------------
def load_config(path: str | None = None) -> dict[str, Any]:
    """读取配置文件，返回 {变量名: 值}；不修改模块变量。"""
    global CONFIG_LOADED

    warnings: list[str] = []
    raw: dict[str, Any] = {}
    target = Path(path or CONFIG_PATH)

    if target.is_file():
        try:
            with open(target, "rb") as fh:
                raw = tomllib.load(fh)
            CONFIG_LOADED = True
        except Exception as exc:  # TOML 语法错误等
            CONFIG_LOADED = False
            warnings.append(f"解析配置文件失败（{target}）：{exc}；本次全部使用内置默认值")
    else:
        CONFIG_LOADED = False
        warnings.append(f"未找到配置文件 {target}，本次全部使用内置默认值")

    flat = _flatten(raw) if isinstance(raw, dict) else {}

    merged: dict[str, Any] = {}
    for toml_path, default in _DEFAULTS.items():
        if toml_path in flat:
            value, note = _coerce(flat.pop(toml_path), default, toml_path)
            merged[toml_path] = value
            if note:
                warnings.append(note)
        else:
            merged[toml_path] = copy.deepcopy(default)

    for unknown in sorted(flat):
        warnings.append(f"未识别的配置项 [{unknown}]（请检查拼写，或对照 config.toml 注释）")

    LOAD_WARNINGS[:] = warnings
    return {_to_python_name(p): copy.deepcopy(v) for p, v in merged.items()}


# ---------------------------------------------------------------------------
# 5. 启动自检
# ---------------------------------------------------------------------------
def validate() -> list[str]:
    """返回配置提示列表（包含加载提示 + 语义检查），不抛异常。"""
    warnings = list(LOAD_WARNINGS)

    if not CONFIG_LOADED:
        warnings.append("当前使用的是内置默认值，建议直接修改项目根目录的 config.toml")

    if REFRESH_INTERVAL_MS < 1000:
        warnings.append("frontend.refresh_interval_ms 小于 1000ms，可能给服务器带来不必要的压力")
    if FETCH_TIMEOUT_MS < REFRESH_INTERVAL_MS:
        warnings.append("frontend.fetch_timeout_ms 小于刷新间隔，慢响应时可能频繁显示“连接失败”")
    if not (0 < BAR_WARN_PERCENT <= BAR_CRIT_PERCENT <= 100):
        warnings.append("进度条阈值需满足 0 < bar_warn_percent <= bar_crit_percent <= 100")

    if not isinstance(CLI_COMMANDS, dict):
        warnings.append("[cli.commands] 必须是表（key = 参数数组）")
    else:
        for key, command in CLI_COMMANDS.items():
            if isinstance(command, str):
                warnings.append(
                    f"cli.commands.{key} 不能写成字符串，必须写成数组，例如 {key} = [\"df\", \"-h\", \"/\"]"
                )
            elif not isinstance(command, (list, tuple)) or not command:
                warnings.append(f"cli.commands.{key} 必须是非空数组")
            elif not all(isinstance(part, str) for part in command):
                warnings.append(f"cli.commands.{key} 的元素必须全部是字符串")

    if CLI_ENABLED and not CLI_COMMANDS:
        warnings.append("[cli] enabled = true 但 [cli.commands] 为空，页面将显示“未配置命令”")

    if not isinstance(WEBSITES, list):
        warnings.append("[[websites.items]] 必须是数组表（每项包含 name 与 url）")
    else:
        for index, site in enumerate(WEBSITES):
            if not isinstance(site, dict):
                warnings.append(f"websites.items[{index}] 必须是表（name = ..., url = ...）")
                continue
            if not site.get("url"):
                warnings.append(f"websites.items[{index}] 缺少 url")
            if not site.get("name"):
                warnings.append(f"websites.items[{index}] 缺少 name，将用 URL 代替")

    if UPTIME_TEXT_TEMPLATE.count("{") != UPTIME_TEXT_TEMPLATE.count("}"):
        warnings.append("uptime.text_template 的花括号不匹配，将回退到默认格式")

    if CPU_TEMP_MIN_C >= CPU_TEMP_MAX_C:
        warnings.append("cpu.temp.min_c 必须小于 cpu.temp.max_c")

    return warnings


# ---------------------------------------------------------------------------
# 6. 模块导入时立即加载，并把所有配置挂到模块变量上
# ---------------------------------------------------------------------------
globals().update(load_config())
