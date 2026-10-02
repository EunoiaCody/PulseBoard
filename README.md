<img src="static/favicon.svg" width="64" height="64" alt="PulseBoard">

# PulseBoard WebUI

一个**轻量级、单页面**的 **PulseBoard** 面板：只显示**当前**状态，没有历史数据、没有数据库、没有 Prometheus / Grafana。

- 后端：Python 3 + FastAPI + psutil + httpx；前端：原生 HTML / CSS / JavaScript（无框架、无构建步骤）
- 主题：Catppuccin（浅色 Latte / 深色 Mocha，跟随系统自动切换），桌面端与手机端自适应
- 浏览器每 3 秒请求一次 `/api/status`，只更新数据区域，不刷新整页
- 任何单个指标（温度、功耗、GPU、Docker、网站）失败都**不会**影响其它指标和整个接口
- 所有配置集中在 `config.toml` 一个带中文注释的文件里；磁盘支持多个分区、网络支持多张网卡

---

## 1. 项目结构

```
.                        # 仓库根目录（PulseBoard/）
├── app.py               # FastAPI 应用：页面路由 + /api/status 聚合
├── config.toml          # 【唯一配置文件】服务/页面/指标/网站/CLI 全部配置，逐项中文注释
├── config.local.toml    # 可选：个人覆盖项（不进版本控制，见 3.1）
├── config.py            # 配置加载器：读 config.toml + 内置默认值 + 类型纠正 + 启动自检
├── requirements.txt
├── README.md
├── run.sh               # 一键启动脚本
├── collectors/
│   ├── system.py        # CPU / 内存 / Swap / 运行时间 / CPU 温度 / 磁盘（支持多个分区）
│   ├── network.py       # 网络上下行速率（差分计算，支持多张网卡）
│   ├── gpu.py           # GPU 占用率：NVIDIA / AMD / Intel(差分) / ARM devfreq 多源回退
│   ├── power.py         # 功耗：GPU(nvidia-smi / hwmon / RAPL 三级回退) / CPU(RAPL) / 整机
│   ├── websites.py      # 网站可用性检测（httpx 异步 + 超时）
│   └── cli.py           # 预设 CLI 命令安全执行器（subprocess + timeout）
├── templates/
│   └── index.html       # 单页页面结构
├── static/
│   ├── favicon.svg      # 默认网站图标（脉冲波形，随系统亮/暗主题变色）
│   ├── style.css        # 样式与 Catppuccin Latte / Mocha 令牌
│   └── app.js           # 轮询与渲染逻辑
└── deploy/              # systemd 部署示例
    ├── monitor-webui.service       # 系统级服务（推荐，可跑在专用低权限用户下）
    ├── monitor-webui.user.service  # 用户级服务（systemd --user，无需 root）
    └── monitor-webui-rapl.conf     # 可选：让非 root 用户读取 RAPL 功耗计数器
```

---

## 2. 安装与启动

### 2.1 一键启动

```bash
git clone https://github.com/EunoiaCody/PulseBoard
cd PulseBoard
chmod +x run.sh && ./run.sh
```

脚本会自己建虚拟环境、装依赖、按 `config.toml` 的 `[server]` 启动服务（依赖已装好时自动跳过安装），
然后浏览器访问 `http://<服务器IP>:8080`（端口以 `config.toml` 为准）。

手动方式（效果一样）：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py            # 监听地址/端口读 config.toml [server]
```

> **改端口只需要改 `config.toml` 里的 `server.port`**，然后重启服务。临时试一下不想改配置，就用环境变量（优先级高于配置文件）：

```bash
PORT=9000 ./run.sh                                # 换端口
HOST=127.0.0.1 ./run.sh                           # 只监听本机
MONITOR_CONFIG=/etc/monitor-webui.toml ./run.sh   # 用别的配置文件
```

> Debian / Ubuntu 如果报 `ensurepip is not available`，先装 venv 模块：`sudo apt install python3-venv`，
> 然后再跑一次 `./run.sh`（脚本检测到建坏的 `.venv` 会自动重建）。

### 2.2 用 systemd 常驻运行

`deploy/` 下有三份可直接使用的示例文件，每一项都带中文注释：

| 文件 | 用途 |
| --- | --- |
| `deploy/monitor-webui.service` | **系统级**服务，推荐；可指定专用低权限用户运行 |
| `deploy/monitor-webui.user.service` | **用户级**服务（`systemctl --user`），不需要 root |
| `deploy/monitor-webui-rapl.conf` | 可选：`tmpfiles.d` 规则，让非 root 用户也能读取 RAPL 功耗 |

**方式 A：系统级服务（推荐）**

```bash
# 1) 部署代码到 /opt/monitor-webui（也可直接把 service 文件里的路径改成你的项目路径）
sudo mkdir -p /opt/monitor-webui
sudo cp -r . /opt/monitor-webui/
cd /opt/monitor-webui && sudo python3 -m venv .venv && sudo .venv/bin/pip install -r requirements.txt

# 2) 创建专用低权限用户（需要看 Docker 时再加入 docker 组）
sudo useradd --system --no-create-home --shell /usr/sbin/nologin monitor
sudo usermod -aG docker monitor          # 可选；改完需 systemctl restart

# 3) 安装并启动
sudo cp deploy/monitor-webui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now monitor-webui

# 4) 查看状态与日志
systemctl status monitor-webui
journalctl -u monitor-webui -f
```

可以先用 `systemd-analyze verify /etc/systemd/system/monitor-webui.service` 检查语法。几个要点：

- `User=` / `Group=`：运行身份；不创建专用用户时改成你自己的用户名。
- `WorkingDirectory=` / `ExecStart=`：**必须指向实际项目路径**。`ExecStart` 用 `.venv/bin/python app.py`
  （不是 `uvicorn --host/--port`），这样监听地址与端口就从 `config.toml` 的 `[server]` 读取；
  需要临时改端口就写 `Environment=PORT=9000`。
- `Environment=PATH=...`：固定 PATH，确保能找到 `nvidia-smi`、`docker`、`df`。
- `Restart=always` + `RestartSec=3`：崩溃自动重启；`NoNewPrivileges` / `ProtectSystem=full` / `PrivateTmp` 等加固项可按需删减。
- ⚠ 项目放在 `/home` 下时，必须保持 `ProtectHome=no`，否则服务读不到代码。
- 读取 RAPL 功耗要么把 `User` 改成 `root`，要么使用 `deploy/monitor-webui-rapl.conf`：

```bash
ls /sys/class/powercap/                     # 确认 RAPL 域名称
cat /sys/class/powercap/*/name
sudo cp deploy/monitor-webui-rapl.conf /etc/tmpfiles.d/
sudo systemd-tmpfiles --create /etc/tmpfiles.d/monitor-webui-rapl.conf
```

**方式 B：用户级服务（无需 root）**

```bash
mkdir -p ~/.config/systemd/user
cp deploy/monitor-webui.user.service ~/.config/systemd/user/monitor-webui.service
# 文件里的路径默认按 %h/PulseBoard 写好，换路径时自行修改
systemctl --user daemon-reload
systemctl --user enable --now monitor-webui
systemctl --user status monitor-webui
journalctl --user -u monitor-webui -f

# 让服务在未登录时也能开机自启（重要）
sudo loginctl enable-linger "$USER"
```

> 用户级服务默认读不到 RAPL（需要 root），此时功耗显示 `N/A` 属于预期行为。

**管理命令速查**

```bash
systemctl start|stop|restart monitor-webui      # 用户级加 --user
systemctl enable|disable monitor-webui
systemctl status monitor-webui
journalctl -u monitor-webui --since "10 min ago"
sudo systemctl edit monitor-webui               # 不改动原文件地覆盖配置
```

---

## 3. 配置文件 `config.toml`

**所有可配置项都在项目根目录的 `config.toml` 里**，每一项都有中文注释；本文档只说明用法，完整清单以该文件为准。

### 3.1 个人改动请写进 `config.local.toml`（避免 `git pull` 冲突）

`config.toml` 是仓库跟踪的文件，上游会不断更新它。如果你直接改它，下次 `git pull` 就可能报：

```
error: Your local changes to the following files would be overwritten by merge:
    config.toml
```

所以个人配置放在项目根目录的 **`config.local.toml`**（已在 `.gitignore` 里，不会被提交，不存在也没关系）：

- 它会**逐项覆盖** `config.toml`，只写你要改的项即可，没写的照旧走默认/基础值；
- 表与表是**深合并**：只写 `[power.gpu]` 里的 `nvidia_smi_timeout`，不会清掉同段其它项；
- 数组（如 `disk.items` / `[[websites.items]]`）是**整体替换**，写了就以你写的为准；
- 文件写错语法也只会在启动日志里告警并忽略它，服务照常启动；
- 启动日志第一行会明确列出实际生效的文件，例如：
  `生效的配置文件：/opt/monitor-webui/config.toml + /opt/monitor-webui/config.local.toml`

最小示例：

```toml
# config.local.toml —— 只写你要改的项
[server]
port = 9000

[page]
title = "My Server"

[disk]
items = ["/", "/data"]

[network]
items = ["eth0", "wlan0"]

[[websites.items]]
name = "面板"
url = "https://panel.example.com"
```

> 用 `MONITOR_CONFIG=/path/to/xxx.toml` 时**不再叠加** `config.local.toml`（那个文件就是你的全部配置）。

### 3.2 网站图标（favicon）

项目自带默认图标 `static/favicon.svg`（圆角方块 + 脉冲波形，用页面主色），
且**会跟随系统亮/暗主题自动变色**（亮色 Latte lavender 底浅色波形，暗色 Mocha 反之），不配任何东西就能显示。

想换成自己的，在 `[page] favicon` 里三选一：

```toml
[page]
# 以下三种写法任选其一，不要同时写
favicon = ""                              # ① 留空：用内置默认图标
favicon = "my-icon.png"                   # ② 把文件放进 static/ 目录，写文件名
favicon = "https://example.com/icon.png"  # ③ 完整 URL（外链图标）
```

- 支持 `svg` / `png` / `ico` / `jpg` / `jpeg` / `webp` / `gif`，会带上正确的 MIME 类型；
- 图标文件**只能放在 `static/` 目录里**（写 `static/xxx.png` 也行），`../` 之类的路径会被拒绝；
- 文件不存在或路径不合法时**不会报错**，只在启动日志提示一句并回退默认图标：
  `配置提示：page.favicon 指向的图标不存在：nope.png；已回退到默认图标 /static/favicon.svg`
- 浏览器对图标缓存很顽固，换完看不到时先强制刷新（Ctrl+Shift+R）或换个标签页。

### 3.3 容错行为

| 情况 | 行为 |
| --- | --- |
| 某项没写 / 被删掉 | 使用 `config.py` 里的内置默认值 |
| 类型写错（如 `port = "9090"`） | 能转换就自动转换（→ 9090）；不能转换则回退默认值并打印提示 |
| 项名拼错（`log_levl = "DEBUG"`） | 启动日志提示 `未识别的配置项 [server.log_levl]`，其余配置照常生效 |
| 整份文件语法错误 | 回退全部默认值，日志提示 `解析配置文件失败（…）：Invalid value (at line 2, column 8)` |
| 文件不存在 | 回退全部默认值，日志提示 `未找到配置文件 …` |

启动时的自检提示（`配置提示：...`）来自 `config.validate()`，**只提示不阻止启动**。
改完配置重启服务生效（`uvicorn --reload` 时自动生效）；想看全部可用项与默认值，直接看 `config.py` 里的 `_DEFAULTS`。

### 3.4 段落索引

| 段落 | 内容 |
| --- | --- |
| `[server]` | 监听地址/端口、热重载、日志级别 |
| `[page]` | 浏览器标题（同时作为页面大标题）、网站图标 |
| `[frontend]` | 刷新间隔、请求超时、进度条告警阈值、速度单位阈值 |
| `[cpu]` / `[cpu.temp]` | CPU 占用率开关；温度传感器优先级、sysfs 兜底、合理温度区间 |
| `[memory]` / `[uptime]` / `[disk]` | 内存与 Swap；运行时间（`text_template` 模板、`hide_zero_days` 不足一天时隐藏“0天”）；磁盘分区列表与开关 |
| `[network]` | 网卡列表（多张）或汇总、是否含回环、`min_interval_s` 最小采样间隔（太短会让差分读数抖动） |
| `[gpu]` | GPU 占用率：多数据源自动回退（见 6.1） |
| `[power]` / `[power.gpu]` / `[power.cpu]` / `[power.total]` | GPU、CPU(RAPL)、整机三路独立功耗配置（见 6.2） |
| `[websites]` + `[[websites.items]]` | 网站检测参数与网站列表（见 4） |
| `[cli]` + `[cli.commands]` + `[cli.labels]` | CLI 白名单、超时、输出截断、并发数、工作目录、环境变量（见 5） |

配置格式用 TOML：原生支持 `#` 注释、允许尾随逗号、`[[websites.items]]` 很适合网站列表；
Python 3.11+ 用标准库 `tomllib` 解析，**零额外依赖**（3.10 及更早会装 `tomli`，已写进 requirements.txt）。

---

## 4. 如何配置网站列表

编辑 `config.toml` 的 `[websites]` 段，并按需要复制 `[[websites.items]]` 段落（保存后重启服务生效）：

```toml
[websites]
enabled = true

timeout = 5.0        # 全局单站超时（秒）
ok_max_code = 400    # 状态码 < 该值算“正常”

# 网站列表：一组 [[websites.items]] = 一个网站
[[websites.items]]
name = "官网"
url = "https://example.com"

[[websites.items]]
name = "API"
url = "https://example.com/health"

[[websites.items]]
name = "内网面板"
url = "https://10.0.0.5:8443"
timeout = 2.0        # 可选：单独覆盖该站点的超时
```

- 判定规则：HTTP 状态码 `< websites.ok_max_code`（默认 400）为「正常」，否则「异常」。
- 超时、DNS 失败、证书错误等都会显示简短错误原因，不会影响其它指标。
- 所有网站**并发**检测（上限 `websites.max_concurrency`），最坏耗时约等于单个超时时间。
- 自签证书站点：`verify_ssl = false`；需要鉴权：`headers = { Authorization = "Bearer xxx" }`。
- 想临时关闭整块检测：`websites.enabled = false`（页面显示“网站检测已关闭”）。

---

## 5. 如何配置 CLI 命令

编辑 `config.toml` 的 `[cli.commands]` 段。**只有这里预先写好的命令才会被执行**，网页端无法传入任何命令（前端只负责显示结果）。

```toml
[cli]
enabled = true
timeout = 5          # 单条命令超时（秒）
max_output = 3000    # stdout 截断长度

[cli.commands]
disk = ["df", "-h", "/"]
docker = ["docker", "ps", "--format", "{{.Names}}: {{.Status}}"]
services = ["systemctl", "show", "-p", "ActiveState", "nginx"]

[cli.labels]         # 卡片标题，key 要和上面的命令对应
disk = "磁盘"
docker = "Docker"
services = "Nginx"
```

> 注意：命令必须写成**数组**。如果写成 `disk = "df -h /"` 这样的字符串，启动日志会提示
> `cli.commands.disk 不能写成字符串，必须写成数组`，该命令会被标记为失败（不会执行）。

安全与健壮性保障（`collectors/cli.py`）：

| 约束 | 实现 |
| --- | --- |
| 不允许任意命令 | 命令只来自 `config.CLI_COMMANDS`（即 `[cli.commands]`）白名单 |
| 不允许 `shell=True` | `subprocess.run(list(cmd), shell=False)` |
| 必须有超时 | `timeout=cli.timeout`，超时返回「执行超时（>5s）」 |
| 命令不存在 | 捕获 `FileNotFoundError` → 显示「命令不存在：xxx」 |
| 无执行权限 | 捕获 `PermissionError` → 显示「没有执行权限：xxx」 |
| 非零退出码 | 显示 `退出码 N：stderr` |
| 输出过长 | 按 `cli.max_output` 截断并提示「(输出已截断)」，页面用可滚动 `<pre>` 展示 |
| 单项失败 | 只影响该卡片，`/api/status` 仍返回 200 |

其它 `[cli]` 选项：`stderr_max`、`max_concurrency`（同时执行数量）、`cwd`（工作目录）、
`extra_env`（附加环境变量，例如 `extra_env = { LANG = "C" }` 让输出更稳定）、`enabled`（整块开关）。

---

## 6. GPU 占用率与功耗（不同硬件）

### 6.1 GPU 占用率：自动识别，不需要按显卡品牌改配置

按顺序尝试四个数据源，哪个能用就用哪个，都不可用时页面显示「不可用」并给出原因：

| 顺序 | 显卡 | 数据源 | 说明 |
| --- | --- | --- | --- |
| 1 | NVIDIA | `nvidia-smi --query-gpu=utilization.gpu` | 多卡时取平均，来源会标注“N 张卡平均” |
| 2 | AMD（amdgpu） | `/sys/class/drm/card*/device/gpu_busy_percent` | 文件内容就是百分比，免 root |
| 3 | Intel（i915 / xe） | RC6 / GT idle 空闲计数器**差分**（`busy% = 100 − Δidle/Δwall`） | 内核**没有**直接给百分比的文件，只能两次采样求差 |
| 4 | ARM / 嵌入式（Mali） | devfreq 的 `load`（如 `/sys/class/devfreq/ffe40000.gpu/load`，格式 `百分比@频率Hz`） | 需 governor 为 `simple_ondemand` 才有该文件 |

几个实际会碰到的点：

- **Mali / panfrost 读不出占用率**：Mali 是 3D-only 的，不注册 `card` 节点（只有 `renderD128`），
  也没有 `gpu_busy_percent` 那种文件。能拿到占用率的唯一途径是 devfreq 的 `load`；
  如果 `load` 不存在，先看 governor：`cat /sys/class/devfreq/*/governor`，
  必要时用 root 改成 `echo simple_ondemand > /sys/class/devfreq/ffe40000.gpu/governor`。
- **`fastfetch` 能显示 GPU 型号 ≠ 能读占用率**：它读的是设备树/DRM 的型号字符串，不是使用率。
- **差分型数据源（Intel）第一次请求显示「不可用」**，约一个刷新周期后自动出现数值，
  与网络速度、RAPL 功耗的行为一致。多个浏览器同时轮询时沿用上次结果，不会抖动。
- **不想用某个数据源**：把它对应的 glob 写成空数组，例如 `busy_percent_globs = []`。

### 6.2 功耗：三路独立，不会把 GPU 功耗当成整机功耗

| 字段 | 含义 | 数据来源（按优先级回退） |
| --- | --- | --- |
| `power.gpu_watts` | GPU 功耗 | ① `nvidia-smi`（NVIDIA）② `hwmon/power1_average`（AMD/Intel 独显）③ RAPL `uncore` 域（Intel 核显） |
| `power.cpu_watts` | CPU / 封装功耗 | Linux RAPL `intel-rapl` / `amd-rapl` 的 `package` 域 |
| `power.total_watts` | 整机 / 平台功耗 | RAPL `psys` 域，或 `/sys/class/power_supply/*/power_now`（笔记本电源） |

GPU 功耗的三级回退链是自动的：每个数据源存在就被采用，不存在就跳到下一个，三个都没有才显示「不可用」。

| 数据源 | 适用硬件 | 怎么确认可用 |
| --- | --- | --- |
| `nvidia-smi` | NVIDIA 独显 | `nvidia-smi --query-gpu=power.draw --format=csv` |
| `hwmon power1_average` | AMD / Intel 独显（amdgpu、i915 驱动导出，单位微瓦） | `ls /sys/class/drm/card*/device/hwmon/hwmon*/power1_average` |
| RAPL `uncore` 域 | Intel 核显（显卡包含在 uncore 域里） | `cat /sys/class/powercap/intel-rapl:0:*/name` |

- 路径可以在配置里改：`power.gpu.hwmon_power_files`、`power.gpu.rapl_keywords`
  （例如某台机器的独显是 `card1` 而非 `card0`，就写 `/sys/class/drm/card1/device/hwmon/hwmon*/power1_average`）。
- **AMD APU（核显与 CPU 共享一个 `package-0` 域）没有独立的 GPU 功耗节点**，页面会显示
  「CPU xx W（GPU 不可用）」，这是硬件的真实情况。此时 GPU **占用率**仍可读（见 6.1）。
- **Intel CPU（RAPL）**：内核需启用 `CONFIG_INTEL_RAPL`（常见发行版默认开启），
  路径如 `/sys/class/powercap/intel-rapl:0/energy_uj`；权限为 `0400 root` 时需要 root 运行，
  或用 2.2 节的 `deploy/monitor-webui-rapl.conf` 放开读取权限（一行通配规则覆盖所有子域）。
  若文件不存在，尝试 `sudo modprobe intel_rapl_msr`。
- **AMD CPU**：较新内核用 `amd_rapl` / `k10temp`，`zenpower` 可提供更多传感器
  （`sudo apt install lm-sensors && sudo modprobe zenpower`，视发行版而定）；RAPL 域名称同为 `package-0`，采集逻辑兼容。
- **整机功耗**：台式机通常无法通过软件读取（除非有带遥测的 PSU 或智能插座）；
  笔记本可能提供 `/sys/class/power_supply/BAT0/power_now`。读不到时 `total_watts` 为 `null`，页面显示「整机 N/A」。
- **RAPL 是差分计算**：因此**第一次请求** `cpu_watts` / `total_watts` 可能为 `null`，约 3 秒后的第二次请求即正常显示。

### 6.3 CPU 温度：读不到或读错了传感器

先看系统里到底有哪些传感器，再对照 `[cpu.temp]` 改：

```bash
ls /sys/class/hwmon/*/name                # 硬件监控芯片名（对应 cpu.temp.chips）
cat /sys/class/thermal/thermal_zone*/type # 内核热区名（对应 cpu.temp.zone_keywords）
sensors                                   # 装了 lm-sensors 后看具体通道名（对应 label_hints）
```

查找顺序：先按 `chips` 列表找 hwmon 芯片（默认 coretemp / k10temp / zenpower / cpu_thermal / acpitz / it87），
再用 `label_hints`（package / tctl / tdie / cpu / core）挑具体通道；hwmon 里没有时回退到
`zone_glob` + `zone_keywords`。读数不在 `min_c` ~ `max_c` 之间会被当成坏数据丢弃。
虚拟机 / 容器里通常没有温度传感器，显示「不可用」是正常的。

---

## 7. 页面与 API

### 7.1 页面

页面是一张“仪表记录纸”：结构由发丝线与留白建立，**全页没有卡片**；
唯一带内嵌底色的元素是 CLI 命令输出（终端原文本来就是另一种材质）。

```
桌面（≥720px）                                手机（<720px，单栏）
┌──────────────────────────────┬──────────────┐   ┌───────────────┐
│ PulseBoard          ∿ 在线    │              │   │ PulseBoard      │
├──────────────────────────────┼──────────────┤   ├───────────────┤
│ 读数                          │ 网站          │   │ 读数           │
│ CPU 占用            12.9 %   │ 官网 ● 正常   │   │ CPU 占用  12.9%│
│ ════════════════════════════ │ ──────────── │   │ ══════════════ │
│ GPU 占用            37.0 %   │ API  ● 异常   │   │ 网站           │
│ ════════════════════════════ │ ──────────── │   │ 官网 ● 正常    │
│ 内存                 47.9 %  │ 命令行信息    │   │ 命令行信息     │
│ ════════════════════════════ │ + 磁盘 成功   │   │ + 磁盘 成功    │
│ 运行时间      13小时 49分钟   │               │   │                │
└──────────────────────────────┴──────────────┘   └───────────────┘
```

- 内容顺序：读数 → 网站 → 命令行信息（DOM 顺序与移动端视觉顺序一致）
- 读数区顺序固定：CPU 占用 / CPU 温度 / GPU 占用 / 内存 / Swap / 磁盘… → 下载上传… / 功耗 / 运行时间
- 桌面端为**非对称双栏 1.35fr / 1fr**，读数占左栏；手机端单栏、行高 ≥52px
- **标签左对齐，数值右对齐**：数字形成可纵向比较的一列
- **磁盘与网卡的行数由 `config.toml` 决定**：配 N 个分区就多 N 行（标签带分区名/路径），
  配 M 张网卡就多 2M 行（每张网卡的下载/上传相邻成对，标签带网卡名）。
  这些行由 JS 按配置插入（位于「功耗」行之前），已有行只改内容不重建，不会每 3 秒打断读屏
- 每行的底边同时充当该指标的阈值进度条（CPU 占用、GPU 占用、内存、Swap、磁盘）：
  超过 `frontend.bar_warn_percent` 变黄，超过 `bar_crit_percent` 变红
- 命令行信息用原生 `<details>` 折叠，展开状态在刷新之间保留
- 数据不可用显示「不可用」并降调（小号灰色、隐藏单位）；连接失败时标题栏标出「连接失败：原因」，
  同时保留最后一次成功的数据并转灰（提示数据已过期）
- 单个分区/网卡读不到时只那一行显示「不可用」与原因（如 `网卡不存在：enp9s9`），其它行照常刷新

#### 动效

所有动效都是 CSS（`static/style.css` 顶部的 `--dur-*` / `--ease-*` 令牌 + `@keyframes`），
只由「加载一次」「轮询成功」「状态变化」三类事件触发，**不新增请求、不新增定时器、没有 `requestAnimationFrame` 循环**，
对服务端零开销：

1. **加载入场**（仅一次）：报头与分区标题的发丝线像绘图仪一样从左画出，读数行 / 网站行 / 命令行按顺序上升淡入。
   元素只改文本不重建，所以后续刷新不会重播。
2. **心跳**（每次轮询成功）：报头左侧与 favicon 同源的脉冲波形扫过一段 lavender 高亮，说明「这一轮数据到了」；
   连接失败时不再跳动，读数转灰提示已过期。
3. **状态变化**（事件触发）：网站 / 命令行的状态在成功与失败之间翻转时，状态词外扩散一个淡出的环；
   进度条跨过 `bar_warn_percent` / `bar_crit_percent` 阈值时颜色平滑过渡而不是硬切。
4. **加载中**（过渡态）：还没拿到数据时状态词与空状态文案缓慢呼吸，拿到数据即停止。

全部动效都尊重 `prefers-reduced-motion: reduce`，开启后一律关闭且内容完整可见。

### 7.2 主题

令牌名与 shadcn/ui 保持一致（`--background` / `--foreground` / `--primary` / `--muted-foreground` /
`--border` / `--ring` / `--radius` …），取值按
[Catppuccin 官方 Style Guide](https://github.com/catppuccin/catppuccin/blob/main/docs/style-guide.md)
的功能分类映射：浅色用 **Latte**，深色用 **Mocha**，跟随系统 `prefers-color-scheme` 自动切换。
状态色另分 `--success` / `--warning` / `--destructive` 三个语义令牌；改配色只需要改 `static/style.css` 顶部的令牌。

| Style Guide 中的功能 | Latte | Mocha | 本项目令牌 |
| --- | --- | --- | --- |
| Background / Body Copy | `base` / `text` | `base` / `text` | `--background` / `--foreground` |
| Secondary Pane | `mantle` | `mantle` | `--card`（仅 CLI 输出块） |
| Sub-Headlines, Labels | `subtext1` | `subtext1` | `--muted-foreground` |
| Surface Elements | `surface0` / `surface2` | `surface0` / `surface2` | `--border` / `--border-strong` |
| Links / Tags | `lavender` | `lavender` | `--primary`、`--ring` |
| Success / Warnings / Errors | `green` / `yellow` / `red` | 同 | `--success` / `--warning` / `--destructive` |

强调色用 Catppuccin **Lavender**（Latte `#7287fd` / Mocha `#b4befe`）：Mocha 下对 `base` 有 9.17:1；
Latte 下只有 2.81:1，低于非文本图形 3:1 的参考线，因此 lavender 只用在「旁边一定有等值文字」的图形上
（进度条填充、焦点环、心跳高亮），正文与状态词不依赖它。

三处以「可读性优先」为由的偏差：`--muted-foreground` 用 `subtext1` 而非 `subtext0`
（Latte 下 `subtext0` 在 `base` 上只有 4.37:1，小字不够）；Latte 的 `green` / `yellow`
在 `base` 上只有 2.96:1 / 2.31:1，因此状态**文字**色分模式处理（Mocha 用 accent 色，
Latte 的“正常”回退到 `text` 色，颜色交给圆点与进度条，且状态本身另有文字说明）；
Latte lavender 的 2.81:1 如上所述只用于非文本图形。

页面已内置语义化标签、跳转链接、`role="status"`、表格 `caption` / `th[scope]`、
状态“颜色+文字”双重表达、`:focus-visible` 焦点环、`prefers-reduced-motion`
（关闭全部入场、心跳、状态提示与进度条过渡动效）。

### 7.3 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 返回单页前端 |
| GET | `/api/status` | 返回全部当前监控数据 |
| GET | `/api/config` | 返回页面标题、图标、刷新间隔、超时、阈值等前端配置（来自 `config.toml`） |
| GET | `/api/health` | 健康检查（`{"status": "ok"}`） |
| GET | `/docs` | FastAPI 自动生成的接口文档 |

`/api/status` 返回示例（片段）：

```json
{
  "updated_at": "2026-10-01T20:00:00+08:00",
  "webservice": { "status": "ok", "status_text": "在线" },
  "cpu": { "usage": 32.4, "temperature": 52.0, "temperature_available": true, "temperature_reason": null },
  "gpu": { "percent": 37.0, "available": true, "source": "amdgpu gpu_busy_percent", "error": null },
  "gpu_enabled": true,
  "memory": { "used_gb": 5.2, "total_gb": 16.0, "percent": 32.5, "swap_used_gb": 0.0, "swap_percent": 0.0 },
  "networks": [
    { "name": "有线", "interface": "enp3s0", "download_bps": 13002342.4, "upload_bps": 2202009.6, "error": null }
  ],
  "networks_enabled": true,
  "power": { "cpu_watts": null, "gpu_watts": 73.0, "total_watts": null, "notes": ["CPU/整机功耗不可用…"] },
  "uptime": { "seconds": 576000, "text": "6天 16小时 0分钟" },
  "disks": [
    { "name": "系统盘", "path": "/", "used_gb": 42.1, "total_gb": 100.0, "percent": 42.1, "error": null }
  ],
  "disks_enabled": true,
  "websites": [
    { "name": "官网", "url": "https://example.com", "status": "up", "status_text": "正常",
      "http_code": 200, "latency_ms": 124, "error": null }
  ],
  "cli": {
    "disk": { "success": true, "output": "…", "error": null, "label": "磁盘" }
  }
}
```

- 网络速度统一以字节/秒（`*_bps`）给出，由前端在 `KB/s` 与 `MB/s` 之间选择显示。
- `disks` / `networks` 都是**数组**，配几个分区/网卡就有几项，顺序与 `config.toml` 一致；
  某一项读不到（挂载点不存在、网卡已拔掉）时只有该项的 `error` 有值、数值为 `null`，不影响其它项。
- 首次请求中网络速度为 0、RAPL 功耗为 `null` 属于正常现象（需要前一次采样作为基准），
  约 3 秒后的第二次请求即得到真实数值；每张网卡各自维护差分基准，新增网卡不影响已有网卡。

---

## 8. 快速验证

```bash
$ ./run.sh
==> 创建虚拟环境 .venv
==> 安装依赖
==> 启动服务（host/port 取自 config.toml [server]，可用 HOST/PORT 覆盖）
INFO:     Uvicorn running on http://0.0.0.0:8080 (Press CTRL+C to quit)

# 另开一个终端
$ curl -s http://127.0.0.1:8080/api/health
{"status":"ok"}

$ curl -s http://127.0.0.1:8080/api/status | python3 -m json.tool | head -20
```

打开 `http://<服务器IP>:8080` 即可看到每 3 秒自动更新的面板。

---

## 9. 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| CPU 温度显示「不可用」 | 见 6.3：虚拟机/容器一般没有温度传感器；物理机可装 `lm-sensors` 并 `sudo sensors-detect`，再按 `ls /sys/class/hwmon/*/name` 的结果调 `[cpu.temp]` |
| 功耗一直为 `N/A` | 见 6.2：RAPL 需要驱动与读取权限，整机功耗多数机器无法读取 |
| GPU 占用/功耗显示「不可用」 | 见第 6 节；ARM SoC（Mali）没有 RAPL/hwmon，GPU 功耗必然不可用，属于硬件事实 |
| Docker 卡片显示「命令不存在」 | 运行服务的用户不在 `docker` 组，或未安装 Docker；`sudo usermod -aG docker $USER` 后重新登录 |
| 网站全部异常 | 服务器无外网 / 需要代理；可先用 `curl -I <url>` 排查 |
| 页面显示「连接失败」 | 后端未启动或被防火墙拦截；前端会自动重试，数据区域保留上一次的值 |
| 改了 `server.port` 却还在 8080 上监听 | 确认启动方式是 `python app.py`（systemd 的 `ExecStart` 也应是 `.venv/bin/python app.py`），而不是手动传了 `--port` |
| 启动报 `address already in use` | 该端口已被占用。换一个 `server.port`，或找出占用进程：`ss -ltnp \| grep :8080` |
| 改完配置不生效 | 重启服务；并看启动日志第一行“生效的配置文件”是否包含你的 `config.local.toml`。注意 `MONITOR_CONFIG` 一旦设置，`config.local.toml` 就不再叠加 |
| `git pull` 报 `Your local changes to config.toml would be overwritten` | 你直接改了被跟踪的 `config.toml`。见 3.1：把个人改动搬到 `config.local.toml`，然后 `git checkout -- config.toml && git pull` |
| 启动日志出现“未识别的配置项” | `config.toml` 里项名拼错了（或写在了错误的段落里）；对照注释修正即可，其它配置照常生效 |
| systemd 启动失败、日志报权限错误 | 项目在 `/home` 下时 `ProtectHome` 必须为 `no`；同时确认 `User` 有项目目录的读权限 |
| `systemctl status` 显示找不到 `.venv/bin/uvicorn` | `WorkingDirectory` / `ExecStart` 路径写错，或虚拟环境未在该路径创建 |
| 用 `MONITOR_CONFIG` 指向 `/tmp/xxx.toml` 但服务没读到 | service 文件里的 `PrivateTmp=yes` 会给服务一个私有 `/tmp`；把配置文件放到项目目录或 `/etc` 下 |
| `./run.sh` 报 `ensurepip is not available` | 当前 Python 缺 venv 模块：`sudo apt install python3-venv`（Debian/Ubuntu）、`sudo dnf install python3`（Fedora） |

---

## 10. 许可证

[MIT](LICENSE) © 2026 EunoiaCody
