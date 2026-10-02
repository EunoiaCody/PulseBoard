"""FastAPI 应用入口：提供单页 WebUI 与实时状态 API。

所有配置都在 config.py 中，包含每一项的注释说明。
启动：
    ./run.sh              # 推荐：建虚拟环境 + 装依赖 + 读 config.toml [server] 启动
    python3 app.py        # 同上（host/port 取自 config.toml [server]）
    HOST=127.0.0.1 PORT=9000 python3 app.py    # 环境变量可临时覆盖
    uvicorn app:app --port 9000                # 直接用 uvicorn 时以命令行参数为准
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import config
from collectors import cli as cli_collector
from collectors import gpu as gpu_collector
from collectors import network as network_collector
from collectors import power as power_collector
from collectors import system as system_collector
from collectors import websites as websites_collector

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

logging.basicConfig(
    level=str(getattr(config, "LOG_LEVEL", "INFO")).upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("monitor-webui")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """启动时打印生效的配置文件与自检结果，便于发现写错的配置项。"""
    sources = getattr(config, "CONFIG_SOURCES", []) or []
    logger.info("生效的配置文件：%s", " + ".join(sources) if sources else "（无，全部使用内置默认值）")
    for warning in config.validate():
        logger.warning("配置提示：%s", warning)
    logger.info("服务启动完成，页面标题：%s", getattr(config, "PAGE_TITLE", "PulseBoard"))
    yield


app = FastAPI(
    title=str(getattr(config, "PAGE_TITLE", "PulseBoard")),
    version="1.1.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _ok(value: Any, fallback: Any) -> Any:
    """把 gather(return_exceptions=True) 的结果或 None 转换为安全值。"""
    if isinstance(value, BaseException) or value is None:
        return fallback
    return value


@app.get("/")
async def index() -> FileResponse:
    """返回单页前端。"""
    return FileResponse(str(TEMPLATES_DIR / "index.html"), media_type="text/html")


@app.get("/api/config")
async def api_config() -> dict[str, Any]:
    """前端需要的展示类配置（来源：config.py 第 1、2、7 节）。"""
    return {
        "page_title": getattr(config, "PAGE_TITLE", "PulseBoard"),
        "favicon": config.resolve_favicon(),
        "refresh_interval_ms": int(getattr(config, "REFRESH_INTERVAL_MS", 3000)),
        "fetch_timeout_ms": int(getattr(config, "FETCH_TIMEOUT_MS", 8000)),
        "bar_warn_percent": float(getattr(config, "BAR_WARN_PERCENT", 75.0)),
        "bar_crit_percent": float(getattr(config, "BAR_CRIT_PERCENT", 90.0)),
        "speed_mb_threshold_bps": int(getattr(config, "SPEED_MB_THRESHOLD_BPS", 1048576)),
        "websites_enabled": bool(getattr(config, "WEBSITES_ENABLED", True)),
        "cli_enabled": bool(getattr(config, "CLI_ENABLED", True)),
    }


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    """聚合所有实时指标。任何单项失败都只影响该项，接口始终返回 200。"""
    updated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    # 阻塞型采集放到线程池，避免卡住事件循环；网站检测本来就是异步的
    (
        cpu_usage,
        cpu_temp,
        gpu_usage,
        memory,
        uptime,
        disks,
        nets,
        power,
        cli_info,
        websites,
    ) = await asyncio.gather(
        asyncio.to_thread(system_collector.get_cpu_usage),
        asyncio.to_thread(system_collector.get_cpu_temperature),
        asyncio.to_thread(gpu_collector.get_gpu_usage),
        asyncio.to_thread(system_collector.get_memory),
        asyncio.to_thread(system_collector.get_uptime),
        asyncio.to_thread(system_collector.get_disks),
        asyncio.to_thread(network_collector.get_network_speeds),
        asyncio.to_thread(power_collector.get_power),
        cli_collector.collect_cli(),
        websites_collector.check_websites(
            getattr(config, "WEBSITES", []) or [],
            getattr(config, "WEBSITE_TIMEOUT", 5.0),
        ),
        return_exceptions=True,
    )

    cpu_usage = _ok(cpu_usage, None)
    cpu_temp = _ok(cpu_temp, {"value": None, "available": False, "reason": "采集失败"})
    gpu_usage = _ok(gpu_usage, {"percent": None, "available": False, "source": None, "error": "采集失败"})
    memory = _ok(memory, {})
    uptime = _ok(uptime, {"seconds": None, "text": None})
    disks = _ok(disks, [])
    nets = _ok(nets, [])
    power = _ok(power, {"cpu_watts": None, "gpu_watts": None, "total_watts": None})
    cli_info = _ok(cli_info, {})
    websites = _ok(websites, [])

    if not isinstance(disks, list):
        disks = []
    if not isinstance(nets, list):
        nets = []

    return {
        "updated_at": updated_at,
        "webservice": {"status": "ok", "status_text": "在线"},
        "cpu": {
            "usage": cpu_usage,
            "temperature": cpu_temp.get("value"),
            "temperature_available": bool(cpu_temp.get("available")),
            "temperature_reason": cpu_temp.get("reason"),
        },
        "gpu": {
            "percent": gpu_usage.get("percent"),
            "available": bool(gpu_usage.get("available")),
            "source": gpu_usage.get("source"),
            "error": gpu_usage.get("error"),
        },
        "gpu_enabled": bool(getattr(config, "GPU_ENABLED", True)),
        "memory": {
            "used_gb": memory.get("used_gb"),
            "total_gb": memory.get("total_gb"),
            "percent": memory.get("percent"),
            "available_gb": memory.get("available_gb"),
            "swap_used_gb": memory.get("swap_used_gb"),
            "swap_total_gb": memory.get("swap_total_gb"),
            "swap_percent": memory.get("swap_percent"),
        },
        "networks": [
            {
                "name": item.get("name") or "",
                "interface": item.get("interface"),
                "download_bps": item.get("download_bps"),
                "upload_bps": item.get("upload_bps"),
                "error": item.get("error"),
            }
            for item in nets
            if isinstance(item, dict)
        ],
        "networks_enabled": bool(getattr(config, "NETWORK_ENABLED", True)),
        "power": {
            "cpu_watts": power.get("cpu_watts"),
            "gpu_watts": power.get("gpu_watts"),
            "total_watts": power.get("total_watts"),
            "notes": power.get("notes", []),
        },
        "uptime": {
            "seconds": uptime.get("seconds"),
            "text": uptime.get("text"),
        },
        "disks": [
            {
                "name": item.get("name") or "",
                "path": item.get("path"),
                "used_gb": item.get("used_gb"),
                "total_gb": item.get("total_gb"),
                "free_gb": item.get("free_gb"),
                "percent": item.get("percent"),
                "error": item.get("error"),
            }
            for item in disks
            if isinstance(item, dict)
        ],
        "disks_enabled": bool(getattr(config, "DISK_ENABLED", True)),
        "websites": websites,
        "websites_enabled": bool(getattr(config, "WEBSITES_ENABLED", True)),
        "cli": cli_info,
        "cli_enabled": bool(getattr(config, "CLI_ENABLED", True)),
    }


@app.get("/api/health")
async def api_health() -> dict[str, str]:
    """轻量健康检查接口。"""
    return {"status": "ok"}


def _env_port(fallback: int) -> int:
    """环境变量 PORT 优先，非法值则回退到配置（不抛异常）。"""
    raw = (os.environ.get("PORT") or "").strip()
    if not raw:
        return fallback
    try:
        port = int(raw)
    except ValueError:
        logging.getLogger("monitor-webui").warning("环境变量 PORT=%r 不是整数，改用配置值 %s", raw, fallback)
        return fallback
    if not 1 <= port <= 65535:
        logging.getLogger("monitor-webui").warning("环境变量 PORT=%s 越界，改用配置值 %s", port, fallback)
        return fallback
    return port


def main() -> None:
    """允许 `python3 app.py` / `./run.sh` 直接启动。

    监听地址与端口默认取自 config.toml 的 [server]，并允许环境变量
    HOST / PORT 临时覆盖（systemd 里用 Environment=PORT=9000 同样生效），
    这样配置文件始终是唯一事实来源，不会被启动脚本默默覆盖。
    """
    import uvicorn

    host = (os.environ.get("HOST") or "").strip() or str(getattr(config, "SERVER_HOST", "0.0.0.0"))
    port = _env_port(int(getattr(config, "SERVER_PORT", 8080)))
    logging.getLogger("monitor-webui").info("监听 %s:%s（config.toml [server]，可用 HOST/PORT 环境变量覆盖）", host, port)

    uvicorn.run(
        "app:app",
        host=host,
        port=port,
        reload=bool(getattr(config, "SERVER_RELOAD", False)),
        log_level=str(getattr(config, "LOG_LEVEL", "INFO")).lower(),
    )


if __name__ == "__main__":
    main()
