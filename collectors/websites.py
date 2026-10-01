"""网站可用性检测：并发发起 HTTP 请求，带超时，失败不影响其它指标。

所有参数（方法、超时、是否跟随重定向、是否校验证书、请求头、并发数、
“正常”的状态码上限）均来自 config.py 第 7 节。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

import config

_DEFAULT_ERROR_MAX_LEN = 120


def _cfg(name: str, default: Any) -> Any:
    """安全读取配置项（缺失时返回默认值）。"""
    return getattr(config, name, default)


def _short_error(exc: BaseException, max_len: int) -> str:
    """把异常转换为简短可读的错误信息。"""
    if isinstance(exc, httpx.TimeoutException):
        return "请求超时"
    if isinstance(exc, httpx.ConnectError):
        return "无法连接（DNS 或网络错误）"
    if isinstance(exc, httpx.TooManyRedirects):
        return "重定向次数过多"
    if isinstance(exc, httpx.UnsupportedProtocol):
        return "不支持的协议"

    text = str(exc).strip().replace("\n", " ")
    if not text:
        text = exc.__class__.__name__
    if len(text) > max_len:
        text = text[: max(max_len - 3, 1)] + "..."
    return f"{exc.__class__.__name__}: {text}"


def _base_result(site: dict[str, Any]) -> dict[str, Any]:
    """构造单个网站的初始结果结构。"""
    return {
        "name": str(site.get("name") or site.get("url") or "未命名"),
        "url": str(site.get("url") or ""),
        "status": "down",
        "status_text": "异常",
        "http_code": None,
        "latency_ms": None,
        "error": None,
    }


async def _check_one(
    client: httpx.AsyncClient,
    site: dict[str, Any],
    default_timeout: float,
    method: str,
    ok_max_code: int,
    error_max_len: int,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """检测单个网站，任何异常都转换为结构化结果。"""
    result = _base_result(site)
    url = result["url"]
    if not url:
        result["error"] = "未配置 URL"
        return result

    # 允许单个网站覆盖全局超时：{"name": ..., "url": ..., "timeout": 3}
    try:
        timeout = float(site.get("timeout", default_timeout))
    except (TypeError, ValueError):
        timeout = default_timeout

    start = time.perf_counter()
    try:
        async with semaphore:
            response = await client.request(method, url, timeout=timeout)
        latency_ms = int(round((time.perf_counter() - start) * 1000))
        code = response.status_code
        result["http_code"] = code
        result["latency_ms"] = latency_ms
        if code < ok_max_code:
            result["status"] = "up"
            result["status_text"] = "正常"
        else:
            result["error"] = f"HTTP {code}"
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 - 单项失败不应影响整体
        result["latency_ms"] = int(round((time.perf_counter() - start) * 1000))
        result["error"] = _short_error(exc, error_max_len)

    return result


def _headers() -> dict[str, str]:
    """组装请求头（UA + 用户自定义头）。"""
    headers: dict[str, str] = {}
    user_agent = _cfg("WEBSITE_USER_AGENT", None)
    if user_agent:
        headers["User-Agent"] = str(user_agent)
    extra = _cfg("WEBSITE_HEADERS", None) or {}
    if isinstance(extra, dict):
        headers.update({str(k): str(v) for k, v in extra.items()})
    return headers


async def check_websites(websites: list[dict[str, Any]], timeout: float = 5.0) -> list[dict[str, Any]]:
    """并发检测网站列表；未启用或未配置时返回空列表。"""
    sites = websites or []
    if not sites:
        return []
    if not _cfg("WEBSITES_ENABLED", True):
        return []

    method = str(_cfg("WEBSITE_METHOD", "GET")).upper()
    ok_max_code = int(_cfg("WEBSITE_OK_MAX_CODE", 400))
    error_max_len = int(_cfg("WEBSITE_ERROR_MAX_LEN", _DEFAULT_ERROR_MAX_LEN))
    max_concurrency = max(int(_cfg("WEBSITE_MAX_CONCURRENCY", 10)), 1)
    semaphore = asyncio.Semaphore(max_concurrency)

    try:
        async with httpx.AsyncClient(
            follow_redirects=bool(_cfg("WEBSITE_FOLLOW_REDIRECTS", True)),
            verify=bool(_cfg("WEBSITE_VERIFY_SSL", True)),
            headers=_headers(),
        ) as client:
            tasks = [
                _check_one(client, site, timeout, method, ok_max_code, error_max_len, semaphore)
                for site in sites
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        # 客户端创建失败（极少见）：返回全部异常状态
        fallback = []
        for site in sites:
            item = _base_result(site)
            item["error"] = "HTTP 客户端初始化失败"
            fallback.append(item)
        return fallback

    output: list[dict[str, Any]] = []
    for site, item in zip(sites, results):
        if isinstance(item, BaseException):
            failed = _base_result(site)
            failed["error"] = _short_error(item, error_max_len)
            output.append(failed)
        else:
            output.append(item)
    return output
