# AGENTS.md

给 AI 编码代理（pi / Copilot / Cursor / Claude）的工作说明。**先读这份，再动代码。**

一般性介绍（功能、配置项全清单、FAQ）见 [README.md](README.md)——不要在 README 里重复实现细节。

---

## 1. 项目概述

**PulseBoard** 是一个轻量级、单页面的服务器实时状态 WebUI：后端 Python + FastAPI 采集 CPU / 内存 / 磁盘 / 网络 / 功耗 / GPU 占用率 / 网站可用性 / 预设 CLI 输出，前端原生 HTML + CSS + JS 每 3 秒轮询一次并只更新 DOM。

面向自托管用户：想看自己那台小机器（软路由、NAS、树莓派、办公主机）现在什么状态，但不想装 Prometheus + Grafana 那一套。**只显示"当前"这一瞬**——没有历史数据、没有数据库、没有账号体系、没有登录页。

三条不可动摇的产品原则：

1. **诚实高于好看**：读不到的指标一律显示「不可用」**并给出原因**，绝不用 0 或估算值凑数。
2. **单项失败不扩散**：任何指标（温度、功耗、GPU、磁盘、网站、命令）失败都不能影响其它指标，`/api/status` 永远返回 200。
3. **零构建**：没有打包器、没有框架、没有 `node_modules`。改完前端就是刷新页面。

---

## 2. 技术栈

| 层 | 技术 | 版本约束 |
| --- | --- | --- |
| 语言 | Python | **3.12+ 目标**；3.10/3.11 也能跑（用 `tomli` 兜底） |
| Web 框架 | FastAPI | `>=0.110,<1.0` |
| ASGI 服务 | uvicorn[standard] | `>=0.27,<1.0` |
| 系统指标 | psutil | `>=5.9` |
| HTTP 客户端 | httpx（异步，网站检测） | `>=0.27` |
| 配置解析 | `tomllib`（3.11+ 标准库）/ `tomli` | 条件依赖，见 `requirements.txt` |
| 前端 | 原生 HTML + CSS + JavaScript（ES5 语法、IIFE、无模块） | 无依赖、无构建 |
| 主题 | Catppuccin（Latte 亮 / Mocha 暗，跟随 `prefers-color-scheme`） | 令牌写在 `static/style.css` |
| 部署 | systemd（系统级 / 用户级），可选 `tmpfiles.d` 规则 | 无 Docker |

**代码总量约 3.3k 行**，仓库跟踪 22 个文件。保持这个体量，不要引入框架。

---

## 3. 项目结构

```
.
├── app.py               # 【入口】FastAPI 应用：路由 + /api/status 聚合 + uvicorn 启动
├── config.py            # 【核心】配置加载器：读 TOML → 合并默认值 → 类型纠正 → 挂模块变量 → 自检
├── config.toml          # 【唯一被跟踪的配置文件】20 个段，逐项中文注释
├── config.local.toml    # 可选：个人覆盖（gitignored），深合并，解决 git pull 冲突
├── collectors/          # 指标采集器，一类指标一个模块
│   ├── system.py        # CPU 占用、CPU 温度、内存/Swap、运行时间、多分区磁盘
│   ├── network.py       # 网络上下行速率（字节差分，线程安全，多网卡）
│   ├── gpu.py           # GPU 占用率：NVIDIA → amdgpu → Intel 差分 → devfreq 四级回退
│   ├── power.py         # 功耗：GPU(nvidia-smi/hwmon/RAPL) / CPU(RAPL) / 整机(RAPL psys/power_supply)
│   ├── websites.py      # 网站可用性（httpx 异步 + 信号量限流 + 单站超时覆盖）
│   └── cli.py           # 预设命令执行器（白名单、shell=False、强制超时、输出截断）
├── templates/index.html # 唯一页面：语义化结构 + 静态页头（JS 不可用时也能看到标题）
├── static/
│   ├── app.js           # 轮询 + 渲染 + 动效触发（IIFE，约 760 行）
│   ├── style.css        # Catppuccin 令牌 + 布局 + 动效（约 705 行，60+ 个 CSS 变量）
│   └── favicon.svg      # 默认图标（SVG 内部有 prefers-color-scheme，亮暗自动变色）
├── deploy/              # systemd 示例（带中文注释）：system 级 / user 级 / RAPL tmpfiles 规则
└── run.sh               # 一键启动：建 venv → 装依赖 → exec python app.py
```

**不属于项目本体**（`.gitignore` 已排除，勿提交、勿引用）：`.agents/`、`.pi/`、`data/skills/`、`skills-lock.json`（本地 Agent 工具链）、`.venv/`、`__pycache__/`、`config.local.toml`。

---

## 4. 构建、运行、验证

没有编译步骤。`./run.sh` 覆盖全部：建虚拟环境（有则跳过）→ 依赖已满足则跳过 pip → 启动。

```bash
./run.sh                          # 监听地址/端口读 config.toml [server]
PORT=9000 ./run.sh                # 环境变量临时覆盖端口
HOST=127.0.0.1 ./run.sh           # 只监听本机
MONITOR_CONFIG=/etc/pb.toml ./run.sh   # 用别的配置文件（此时不再叠加 config.local.toml）
```

手动等价方式（systemd 用的就是这条）：

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app.py           # 必须 exec app.py，不能用 uvicorn --port，否则会静默覆盖 [server]
```

**⚠ 端口只认 `config.toml` 的 `[server]` 或环境变量 `HOST`/`PORT`。** 历史上就是因为 `run.sh` 偷偷传 `--port` 导致改配置不生效——`run.sh` 和两个 systemd 单元现在都执行 `python app.py`，别改回去。

### 语法自检（改动后必跑）

```bash
.venv/bin/python -m compileall -q app.py config.py collectors   # Python 语法
node --check static/app.js                                        # JS 语法（若装了 node）
.venv/bin/python -c "import tomllib;tomllib.load(open('config.toml','rb'))"   # 配置语法
```

### 起服务做端到端验证

```bash
.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8092 &
curl -s http://127.0.0.1:8092/api/health          # {"status":"ok"}
curl -s http://127.0.0.1:8092/api/config | python3 -m json.tool
curl -s http://127.0.0.1:8092/api/status | python3 -m json.tool | head -30
# 收尾按端口找 PID 再 kill，不要用 pkill -f：
kill $(ss -ltnp | grep ':8092 ' | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1)
```

### 页面截图自检（无头 Chrome）

```bash
google-chrome-stable --headless=new --disable-gpu --hide-scrollbars \
  --window-size=1280,900 --virtual-time-budget=9000 \
  --screenshot=/tmp/check.png http://127.0.0.1:8092/
```

`--virtual-time-budget` 要 ≥ 刷新间隔，否则截到的是首屏空白。加 `--dump-dom` 可检查渲染后的文本。

---

## 5. 代码约定

### Python

- **注释与文档字符串用中文**，每个模块开头写清"这个文件负责什么、怎么用"，每个公开函数写一行 `"""..."""`。
- **类型标注齐全**，文件顶部 `from __future__ import annotations`。
- **读取配置只用 `config.XXX`**，不要在业务代码里读 TOML、不要新增环境变量（除已有的 5 个）。
- **异常一律不外泄**：采集器内部 try/except 捕获，返回 `{"值": None, "error": "原因"}`；绝不 `raise` 给 API 层。
- 私有工具函数前缀 `_`；模块级常量全大写（`_NA_VALUES`、`_DEFAULTS`、`_ALIASES`）。
- 无 lint/format 配置（无 ruff/black/flake8 依赖），靠风格一致性：4 空格缩进、行宽约 110、类型标注用内置泛型（`list[str]`、`dict[str, Any]`）。

### 配置（改这块前务必读懂 config.py）

- **配置项的唯一清单是 `config.py` 里的 `_DEFAULTS`**，键是 TOML 路径（`"power.cpu.rapl_glob"`）。
- 加配置项必须**四处同步**，缺一处用户就配不了：

  | 位置 | 动作 |
  | --- | --- |
  | `config.py` `_DEFAULTS` | 加 `"段.键": 默认值,` |
  | `config.py` `_ALIASES` | 一般不用加（默认按 `段_键` 推导大写）；只在名字不规则时加 |
  | `config.toml` 对应段 | 加一行 `键 = 默认值  # 中文注释` |
  | `README.md` | 需要用户知道的才写进 §3.4 段落索引表 |

- 业务代码读到的变量名由 `_to_python_name()` 从 TOML 路径推导：`"page.title"` → `PAGE_TITLE`；不规则映射在 `_ALIASES`（如 `server.log_level` → `LOG_LEVEL`）。
- `validate()` 负责启动自检，只 `logger.warning` 不阻断启动。新增容易写错的项就往里加检查。

### 前端

- **ES5 语法**（`var`/`function`，不用箭头函数、不用 `let/const`、不用模板字符串）——`static/app.js` 整体是一个 `"use strict"` 的 IIFE，禁止引入模块/构建。
- **只改文本节点，不重建 DOM**：`setText()` 内部比较后再写；网站/命令列表用 `sitesSignature()`/`buildReadoutRow()` 做 key 签名比对，**只有条目集合变化时才重建**，否则读屏软件会被每 3 秒打断一次。
- **不可用 = 写 `"不可用"` + `data-na="true"`**，样式负责降调；不要写 `0`、`-` 或留空。
- **新增读数行的顺序**：在 `buildReadoutRow(spec)` 的调用处决定，磁盘/网卡行通过 `syncReadoutRows()` 按 key 对齐插入到「功耗」行之前；别用 `innerHTML` 拼接用户数据。
- **CSS 令牌只改 `static/style.css` 顶部**（`:root` 与 `@media (prefers-color-scheme: dark)` 两处，60 个变量），不写字面色值。令牌名保持 shadcn 风格（`--background`/`--foreground`/`--primary`/`--muted-foreground`/`--border`/`--ring`/`--radius`），取值必须来自 Catppuccin 官方 Style Guide 的功能分类；确需偏离要在注释里写明理由（如 `--muted-foreground` 用 `subtext1` 而非 `subtext0` 是因为对比度）。
- 主题由 CSS 媒体查询驱动，**JS 不参与亮暗切换**。
- 尊重 `prefers-reduced-motion`：所有动效（入场 / 心跳 / 状态提示 / 进度条过渡 / 折叠开合）集中在 `style.css` 的 keyframes 与 `--dur-*`/`--ease-*` 令牌里，新增动效必须走这两套令牌，并在 reduced-motion 下被关闭。

### 提交

Conventional Commits，**中文说明**：

```
feat(gpu): 内置 GPU 占用率采集，兼容 NVIDIA / AMD / Intel / ARM
fix(server): config.toml 的 [server] 端口不再被启动脚本静默覆盖
docs(readme): 精简文档，并去掉与个人环境绑定的示例值
```

提交前确认 `git status` 里**没有** `config.local.toml`、`.venv/`、`.agents/`、`.pi/`、截图文件。

---

## 6. 架构与数据流

```
浏览器
  │  GET /api/config  (一次)  → 页面标题、favicon、刷新间隔、阈值
  │  GET /api/status  (每 3s) → 全部指标
  ▼
app.py  api_status()
  │  asyncio.gather(return_exceptions=True)
  ├─ 阻塞型采集 → asyncio.to_thread(collectors.system.get_cpu_usage) …
  ├─ 网络/功耗/CLI → 各自的并发 + 限流逻辑（在线程池里跑）
  └─ 网站检测   → asyncio.gather + Semaphore（本来就是异步的）
  │  每个结果过 _ok() 兜底（异常/None → 该项降级为 null + 说明）
  ▼
统一 JSON（永远 200）
  ▼
static/app.js  render() → 只更新变化的文本节点
```

关键模式：

- **零状态聚合**：除了差分计算必需的基线（网络字节数、RAPL 能量、Intel 空闲计数器），服务端不保留任何历史；每个采集器自带 `threading.Lock` 保护缓存。
- **差分型数据源的处理**（`network.py` / `power.py` / `gpu.py` 的 Intel 分支）：首次调用没有基线 → 返回不可用 + "约 N 秒后显示"；间隔小于 `min_interval_s` → **沿用上次结果**而不是重新算，否则多个浏览器标签轮询会让数字乱跳。
- **多目标采集器**（磁盘、网卡、网站、命令）：返回**列表**，逐项带 `error` 字段，配置顺序原样保留。单项失败只让那一行显示「不可用」。
- **回退链用"返回 None 表示不可用"表达**，见 `collectors/gpu.py` 的 `_from_nvidia() → _from_busy_percent() → _from_residency() → _from_devfreq()`。
- **静态 HTML 里已经有标题和 favicon**，JS 只在 `/api/config` 的值与默认值不同时才覆盖——保证禁用 JS 时页面依然有标题、图标、语义结构。

---

## 7. 入口点

| 入口 | 文件 | 说明 |
| --- | --- | --- |
| 服务启动（开发/生产唯一入口） | `app.py` 的 `main()` / `__main__` | `python app.py`，uvicorn 参数由代码算出：`host = HOST or SERVER_HOST`、`port = PORT or SERVER_PORT` |
| WSGI/ASGI 导入 | `app.py` 的 `app` | `uvicorn app:app --port 9000` 也能跑，但**命令行端口会覆盖配置** |
| 一键脚本 | `run.sh` | 建 venv → 装依赖 → `exec "$VENV_PY" app.py` |
| 配置加载 | `config.py` **模块导入时** | 导入即执行 `load_config()` 并把每项挂成模块变量，**不要改成惰性加载** |
| 前端 | `templates/index.html` + `static/app.js` | 无打包，`app.js` 末尾 `document.readyState` 判断后自启动 |

---

## 8. 关键依赖

| 包 | 用途 | 备注 |
| --- | --- | --- |
| `fastapi` | 路由、`lifespan` 启动日志、静态文件挂载、`/docs` | 只用 GET 路由，无鉴权无数据库 |
| `uvicorn[standard]` | ASGI 服务 | `reload=True` 时必须传字符串 `"app:app"` |
| `psutil` | CPU/内存/磁盘/网卡/温度 | 所有"读系统状态"的唯一入口，别直接 `os.getloadavg()` |
| `httpx` | 网站可用性检测（异步） | 单站可覆盖 `timeout`；错误信息截断到 `websites.error_max_len` |
| `tomllib` / `tomli` | 解析 `config.toml` | 3.11+ 走标准库，**不要为此引入 toml/tomlkit 之类的第三方 TOML 库** |

**子进程**只用标准库 `subprocess`（仅 `collectors/cli.py`，且 `shell=False`）。

---

## 9. 环境与配置

### 环境变量（全部 5 个，不要新增）

| 变量 | 作用 | 默认 |
| --- | --- | --- |
| `HOST` | 覆盖监听地址 | `config.toml` 的 `server.host` |
| `PORT` | 覆盖监听端口（非法值只告警并回退） | `config.toml` 的 `server.port` |
| `MONITOR_CONFIG` | 改用其它配置文件；**设置后不再叠加 `config.local.toml`** | 无 → 项目根 `config.toml` |
| `VENV_DIR` | `run.sh` 用的虚拟环境目录 | `.venv` |
| `PYTHON_BIN` | `run.sh` 创建 venv 用的解释器 | `python3` |

### 配置文件

优先级：**内置默认值 (`config.py::_DEFAULTS`) < `config.toml` < `config.local.toml`**。

- `config.toml` 是**被 git 跟踪**的，会随上游更新。**个人改动一律写进 `config.local.toml`**（gitignored），否则用户 `git pull` 会冲突。这是给用户踩过的坑，别让任何人再去改 `config.toml`。
- `config.local.toml`：**表深合并，数组整体替换**。只写要改的键即可。
- 任何缺失/拼错/类型错误都不得让服务起不来：缺失用默认值，类型能转就转，转不了回退并告警，未知键在启动日志提示 `未识别的配置项 [段.键]`。
- 启动日志第一行 `生效的配置文件：...`，末尾列出全部 `配置提示：`——排查配置问题先看这两处。

段落：`server`、`page`、`frontend`、`cpu`、`cpu.temp`、`memory`、`uptime`、`disk`、`network`、`gpu`、`power.{gpu,cpu,total}`、`websites` + `[[websites.items]]`、`cli` + `[cli.commands]` + `[cli.labels]`。

---

## 10. 测试策略

**仓库内没有测试目录，也没有测试框架依赖**——这是刻意选择：给一个 3k 行的读数面板引入 pytest 反而增加维护面。改动请按下面的方式验证（临时脚本放 `/tmp`，**不要提交**）：

| 改动类型 | 验证方式 |
| --- | --- |
| 改 `config.py` / `config.toml` | 写 `/tmp` 脚本覆盖各种边界：缺失项、类型错、拼错键、深合并、数组替换；断言 `validate()` 的提示文案 |
| 改某个 collector | **假数据注入**：在 `/tmp` 造假的 sysfs 树（`/sys/class/drm/card0/device/gpu_busy_percent`、`/sys/class/powercap/intel-rapl:0/energy_uj`、`/sys/class/devfreq/*/load` 等），用配置项把 glob 指过去，覆盖"存在/不存在/垃圾内容/越界/权限拒绝"；`nvidia-smi` 类用假可执行脚本桩 |
| 改 `app.py` 聚合 | `fastapi.testclient.TestClient` + **故障注入**（让所有 collector 抛异常），断言 `/api/status` 仍 200 且各字段为 `null` + 有 `error` |
| 改前端 | `node --check static/app.js` → 起服务 → 无头 Chrome `--dump-dom` 看渲染结果 → `--screenshot` 看视觉（含 `--window-size=390,900` 手机宽度和亮/暗两种主题） |
| 改 favicon / 静态资源 | 起服务后 `curl -sI` 检查 `Content-Type`，并模拟浏览器强制刷新场景 |
| 改 `run.sh` / systemd 文件 | 真跑 `./run.sh`；service 用 `systemd-analyze verify <file>` 检查语法 |

**硬性回归线**（任何改动后都必须满足）：

1. `curl /api/health` → `{"status":"ok"}`
2. `curl /api/status` → **HTTP 200**，且字段数不减（改前记下字段清单做对比）
3. 全部 collector 抛异常时，`/api/status` 仍 200，页面显示「不可用」而不是崩溃
4. 桌面（1280px）与手机（390px）两种宽度下无溢出、无重叠

---

## 11. 部署

无 Docker、无 CI——直接 systemd 常驻。三份示例在 `deploy/`（均带中文注释）：

| 文件 | 用途 |
| --- | --- |
| `deploy/monitor-webui.service` | 系统级，推荐。`User=monitor` 专用低权限用户；需要看 Docker 就 `usermod -aG docker monitor` |
| `deploy/monitor-webui.user.service` | 用户级（`systemctl --user`），免 root；`WorkingDirectory=%h/PulseBoard` |
| `deploy/monitor-webui-rapl.conf` | 可选 `tmpfiles.d` 规则，让非 root 也能读 RAPL 功耗计数器 |

```bash
sudo cp deploy/monitor-webui.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now monitor-webui
systemd-analyze verify /etc/systemd/system/monitor-webui.service   # 改过 service 先验语法
journalctl -u monitor-webui -f
```

部署注意：

- `ExecStart` **必须**是 `.venv/bin/python app.py`（配置才是唯一事实来源）；临时改端口用 `Environment=PORT=9000`。
- 项目在 `/home` 下时 `ProtectHome=no` 必须保留，否则服务读不到代码。
- `PrivateTmp=yes` 存在，`MONITOR_CONFIG` 不要指向 `/tmp`。
- 用户级服务要开机自启需 `sudo loginctl enable-linger "$USER"`。
- systemd 的 `Environment=PATH=...` 是为了能找到 `nvidia-smi` / `docker` / `df`。
- 交付给用户时**不要**写入具体用户名、端口、内网地址、`/home/<某人>` 路径——示例保持中性（`monitor` 用户、`%h/PulseBoard`、`PORT=9000`）。

---

## 12. 修改时的红线

1. 不引入前端框架、打包器、`package.json`、`node_modules`。
2. 不引入数据库、认证、历史数据存储。
3. 不把 `/api/status` 改成可能非 200；不让单个 collector 的异常冒泡到聚合层。
4. 不用编造值填充不可用指标——「不可用 + 原因」是特性，不是缺陷。
5. 不给 `collectors/cli.py` 加 `shell=True`、不加用户可传入的命令、不去掉 `timeout` 与输出截断。
6. 不把用户个人配置写回 `config.toml`（那是 `config.local.toml` 的职责）。
7. 不要重命名 `config.py` 或删掉它的模块级加载行为——`app.py` 与 6 个采集器都 `import config`，且它依赖"导入即读配置"的设计。
8. 改完 `config.py` / `config.toml` 后必须同步 README（配置项表、段落索引）。
