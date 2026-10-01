"""预设 CLI 命令执行器。

安全约束（对应 config.py 第 8 节）：
  * 只执行 config.CLI_COMMANDS 中预先配置好的命令，网页无法传入命令；
  * 使用参数列表形式调用，绝不使用 shell=True；
  * 必须设置 timeout；
  * 输出做长度截断；
  * 命令不存在 / 失败 / 超时都只返回错误信息，不抛出异常。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
from typing import Any, Optional, Sequence

import config


def _cfg(name: str, default: Any) -> Any:
    """安全读取配置项（缺失时返回默认值）。"""
    return getattr(config, name, default)


def _truncate(text: str, limit: int) -> str:
    """限制输出长度，避免超长输出破坏页面。"""
    text = text or ""
    if limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + "\n...(输出已截断)"


def _build_env() -> Optional[dict[str, str]]:
    """按配置合并子进程环境变量；未配置时返回 None（继承父进程环境）。"""
    extra = _cfg("CLI_EXTRA_ENV", None)
    if not isinstance(extra, dict) or not extra:
        return None
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in extra.items()})
    return env


def run_command(
    command: Sequence[str],
    timeout: Optional[float] = None,
    max_output: Optional[int] = None,
) -> dict[str, Any]:
    """安全执行单条预设命令，返回 {success, output, error}。"""
    if not command:
        return {"success": False, "output": "", "error": "未配置命令"}
    if isinstance(command, str):  # 防御性检查：不接受字符串命令
        return {"success": False, "output": "", "error": "命令必须以参数列表形式配置"}

    timeout = float(timeout if timeout is not None else _cfg("CLI_TIMEOUT", 5))
    max_output = int(max_output if max_output is not None else _cfg("CLI_MAX_OUTPUT", 3000))
    stderr_max = int(_cfg("CLI_STDERR_MAX", 500))
    cwd = _cfg("CLI_CWD", None)

    try:
        proc = subprocess.run(  # noqa: S603 - 命令来自本地配置白名单
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,
            check=False,
            cwd=cwd,
            env=_build_env(),
        )
    except FileNotFoundError:
        return {"success": False, "output": "", "error": f"命令不存在：{command[0]}"}
    except PermissionError:
        return {"success": False, "output": "", "error": f"没有执行权限：{command[0]}"}
    except NotADirectoryError:
        return {"success": False, "output": "", "error": f"工作目录不存在：{cwd}"}
    except subprocess.TimeoutExpired:
        return {"success": False, "output": "", "error": f"执行超时（>{timeout:g}s）"}
    except OSError as exc:
        return {"success": False, "output": "", "error": f"执行失败：{exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "output": "", "error": f"未知错误：{exc}"}

    stdout = _truncate((proc.stdout or "").strip(), max_output)
    stderr = _truncate((proc.stderr or "").strip(), stderr_max)

    if proc.returncode != 0:
        return {
            "success": False,
            "output": stdout,
            "error": f"退出码 {proc.returncode}" + (f"：{stderr}" if stderr else ""),
        }
    return {"success": True, "output": stdout or "(无输出)", "error": None}


async def _collect_one(
    key: str,
    command: Sequence[str],
    semaphore: asyncio.Semaphore,
) -> tuple[str, dict[str, Any]]:
    """执行一条命令并附加展示标签。"""
    async with semaphore:
        result = await asyncio.to_thread(run_command, command)
    result["label"] = (getattr(config, "CLI_LABELS", {}) or {}).get(key, key)
    return key, result


async def collect_cli() -> dict[str, Any]:
    """并发执行所有预设命令；单个失败不影响其它。"""
    if not _cfg("CLI_ENABLED", True):
        return {}
    commands = _cfg("CLI_COMMANDS", {}) or {}
    if not isinstance(commands, dict) or not commands:
        return {}

    max_concurrency = max(int(_cfg("CLI_MAX_CONCURRENCY", 6)), 1)
    semaphore = asyncio.Semaphore(max_concurrency)
    labels = getattr(config, "CLI_LABELS", {}) or {}

    keys = list(commands.keys())
    tasks = [_collect_one(key, commands[key], semaphore) for key in keys]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    output: dict[str, Any] = {}
    for key, item in zip(keys, results):
        if isinstance(item, BaseException):
            output[key] = {
                "success": False,
                "output": "",
                "error": f"采集失败：{item}",
                "label": labels.get(key, key),
            }
        else:
            output[item[0]] = item[1]
    return output
