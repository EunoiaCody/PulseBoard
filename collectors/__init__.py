"""指标采集器集合。

每个子模块只负责一类指标，并且内部自行捕获异常，
保证任何单个指标失败都不会让 /api/status 整体失败。
"""

from . import cli, gpu, network, power, system, websites

__all__ = ["cli", "gpu", "network", "power", "system", "websites"]
