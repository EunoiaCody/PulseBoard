/* =============================================================================
   前端逻辑：每 N 秒拉取一次 /api/status，只更新变化的文本节点。
   设计约束：
     * 不动画、不重建 DOM（网站/命令列表只在条目变化时重建，避免读屏被打断）
     * 数值不可用时写“不可用”，并交给样式做降调处理
     * 连接失败时保留最后一次成功的数据，并明确标注数据已过期
   ========================================================================== */
(function () {
  "use strict";

  var NA = "不可用";

  var settings = {
    refreshInterval: 3000,
    fetchTimeout: 8000,
    barWarn: 75,
    barCrit: 90,
    speedMbThreshold: 1048576
  };

  var timer = null;
  var hasData = false;
  var sitesKey = null;
  var siteRefs = [];
  var cliKey = null;
  var cliRefs = {};

  /* --------------------------------- 工具 --------------------------------- */

  function $(id) {
    return document.getElementById(id);
  }

  function isNum(value) {
    return typeof value === "number" && isFinite(value);
  }

  function fixed(value, digits) {
    if (!isNum(value)) return null;
    return value.toFixed(digits === undefined ? 1 : digits);
  }

  /** 只在内容变化时写入，避免无意义的 DOM 变更 */
  function setText(el, text) {
    if (!el) return;
    var next = text === null || text === undefined ? "" : String(text);
    if (el.textContent !== next) el.textContent = next;
  }

  /** 数值：不可用时写“不可用”，并让样式降调 */
  function setFigure(el, value, digits) {
    if (!el) return;
    var text = fixed(value, digits);
    if (text === null) {
      setText(el, NA);
      el.setAttribute("data-na", "true");
    } else {
      setText(el, text);
      el.removeAttribute("data-na");
    }
  }

  /** 文本型数值（如运行时间），同样支持“不可用”状态 */
  function setTextValue(el, text) {
    if (!el) return;
    var value = text ? String(text) : NA;
    setText(el, value);
    if (value === NA) el.setAttribute("data-na", "true");
    else el.removeAttribute("data-na");
  }

  /** 次要说明行：空字符串表示不需要，直接隐藏以保持留白 */
  function setNote(el, text) {
    if (!el) return;
    var value = text ? String(text) : "";
    setText(el, value);
    el.hidden = value === "";
  }

  function setUnit(el, unit) {
    if (!el) return;
    setText(el, unit || "");
    el.hidden = !unit;
  }

  /** 阈值进度：填充画在行底边上，颜色只在越过 config.toml 的阈值后才改变 */
  function setGauge(fillEl, percent) {
    if (!fillEl) return;
    var value = isNum(percent) ? Math.max(0, Math.min(100, percent)) : 0;
    fillEl.style.width = value + "%";
    var level = value >= settings.barCrit ? "crit" : value >= settings.barWarn ? "warn" : "ok";
    if (fillEl.getAttribute("data-level") !== level) fillEl.setAttribute("data-level", level);
  }

  /** 字节/秒 -> KB/s 或 MB/s（阈值来自配置） */
  function formatSpeed(bps) {
    if (!isNum(bps)) return { value: null, unit: "" };
    if (bps >= settings.speedMbThreshold) {
      return { value: (bps / 1048576).toFixed(2), unit: "MB/s" };
    }
    return { value: (bps / 1024).toFixed(1), unit: "KB/s" };
  }

  /** 连接状态：只在状态变化时改写 role="status" 的内容，避免每 3 秒播报 */
  function setConnection(state, message) {
    var dot = $("service-dot");
    var text = $("service-state");
    var main = $("main");

    if (dot) dot.setAttribute("data-state", state);
    if (text) {
      text.setAttribute("data-state", state);
      setText(text, message);
    }
    // 有过成功数据后又连接失败：读数转灰，提示已过期
    var stale = state === "down" && hasData;
    if (main) main.setAttribute("data-stale", stale ? "true" : "false");
  }

  /* --------------------------------- 读数 --------------------------------- */

  function renderCpu(data) {
    var cpu = data.cpu || {};
    setFigure($("cpu-usage"), cpu.usage, 1);
    setGauge($("cpu-usage-gauge"), cpu.usage);
    setNote($("cpu-usage-note"), isNum(cpu.usage) ? "多核平均占用率" : "");

    setFigure($("cpu-temp"), cpu.temperature, 1);
    setNote($("cpu-temp-note"), isNum(cpu.temperature) ? "" : (cpu.temperature_reason || ""));
  }

  function renderMemory(data) {
    var mem = data.memory || {};

    setFigure($("mem-percent"), mem.percent, 1);
    setGauge($("mem-gauge"), mem.percent);
    setNote(
      $("mem-note"),
      isNum(mem.used_gb) && isNum(mem.total_gb)
        ? fixed(mem.used_gb, 1) + " ⁄ " + fixed(mem.total_gb, 1) + " GB 已用"
        : ""
    );

    var swapTotal = mem.swap_total_gb;
    if (isNum(swapTotal) && swapTotal > 0) {
      setFigure($("swap-percent"), mem.swap_percent, 1);
      setUnit($("swap-unit"), isNum(mem.swap_percent) ? "%" : "");
      setGauge($("swap-gauge"), mem.swap_percent);
      setNote($("swap-note"), fixed(mem.swap_used_gb, 2) + " ⁄ " + fixed(swapTotal, 2) + " GB");
    } else {
      setTextValue($("swap-percent"), swapTotal === 0 ? "未启用" : "");
      if (swapTotal === 0) $("swap-percent").setAttribute("data-na", "true");
      setUnit($("swap-unit"), "");
      setGauge($("swap-gauge"), null);
      setNote($("swap-note"), "");
    }
  }

  /* -------------------- 磁盘 / 网卡：行数可变的读数行 --------------------
     行数取决于 config.toml，所以整行由 JS 建；已存在的行只改内容，
     避免每 3 秒重建 DOM 打断读屏。 */

  var rowRefs = {};   // key -> { el, sub, figure, unit, note, gauge }
  var rowOrder = null;

  function buildReadoutRow(spec) {
    var row = document.createElement("div");
    row.className = "readout";

    var label = document.createElement("dt");
    label.className = "readout__label";
    label.appendChild(document.createTextNode(spec.label));
    var sub = document.createElement("span");
    sub.className = "readout__sub";
    label.appendChild(sub);

    var value = document.createElement("dd");
    value.className = "readout__value";
    var figure = document.createElement("span");
    figure.className = spec.text ? "readout__figure readout__figure--text" : "readout__figure";
    figure.setAttribute("data-na", "true");
    var unit = document.createElement("span");
    unit.className = "readout__unit";
    var note = document.createElement("span");
    note.className = "readout__note";
    note.hidden = true;
    value.append(figure, unit, note);

    var gauge = null;
    if (spec.gauge) {
      gauge = document.createElement("span");
      gauge.className = "gauge__fill";
      gauge.setAttribute("aria-hidden", "true");
      value.appendChild(gauge);
    }

    row.append(label, value);
    return { el: row, sub: sub, figure: figure, unit: unit, note: note, gauge: gauge };
  }

  /** 把 specs 同步成真实行：新增、更新、删除都只动必要的节点 */
  function syncReadoutRows(specs) {
    var dl = $("readouts");
    var anchor = $("power-row");
    if (!dl || !anchor) return;

    var keys = specs.map(function (spec) { return spec.key; });
    var order = keys.join("|");

    // 1) 删掉配置里已不存在的行（改了 config.toml 后无需刷新页面）
    Object.keys(rowRefs).forEach(function (key) {
      if (keys.indexOf(key) === -1) {
        rowRefs[key].el.remove();
        delete rowRefs[key];
      }
    });

    // 2) 新建或更新
    specs.forEach(function (spec) {
      var refs = rowRefs[spec.key];
      if (!refs) {
        refs = buildReadoutRow(spec);
        rowRefs[spec.key] = refs;
        dl.insertBefore(refs.el, anchor);
      }
      setText(refs.sub, spec.sub ? "（" + spec.sub + "）" : "");
      if (spec.text) setTextValue(refs.figure, spec.value);
      else setFigure(refs.figure, spec.value, spec.digits);
      setUnit(refs.unit, spec.unit);
      setNote(refs.note, spec.note);
      if (refs.gauge) setGauge(refs.gauge, spec.percent);
      if (spec.break) refs.el.setAttribute("data-break", "");
      else refs.el.removeAttribute("data-break");
    });

    // 3) 顺序变了才重排（npm install 位置：依次插到功耗行之前）
    if (order !== rowOrder) {
      specs.forEach(function (spec) {
        dl.insertBefore(rowRefs[spec.key].el, anchor);
      });
      rowOrder = order;
    }
  }

  function diskSpecs(data) {
    var items = Array.isArray(data.disks) ? data.disks : [];
    return items.map(function (disk, index) {
      var note = "";
      if (disk.error) note = String(disk.error);
      else if (isNum(disk.used_gb) && isNum(disk.total_gb)) {
        note = fixed(disk.used_gb, 1) + " ⁄ " + fixed(disk.total_gb, 1) + " GB 已用";
      }
      return {
        key: "disk:" + (disk.path || index),
        label: "磁盘",
        sub: disk.name || disk.path || "",
        value: disk.percent,
        digits: 1,
        unit: "%",
        percent: disk.percent,
        gauge: true,
        note: note,
        break: index === items.length - 1
      };
    });
  }

  function networkSpecs(data) {
    var items = Array.isArray(data.networks) ? data.networks : [];
    var specs = [];
    items.forEach(function (net, index) {
      var tag = net.name || net.interface || "所有网卡";
      var down = formatSpeed(net.download_bps);
      var up = formatSpeed(net.upload_bps);
      var last = index === items.length - 1;
      specs.push({
        key: "net:" + index + ":down",
        label: "下载",
        sub: tag,
        value: down.value,
        unit: down.unit,
        text: true,
        note: net.error ? String(net.error) : ""
      });
      specs.push({
        key: "net:" + index + ":up",
        label: "上传",
        sub: tag,
        value: up.value,
        unit: up.unit,
        text: true,
        break: last
      });
    });
    return specs;
  }

  function renderGpu(data) {
    var gpu = data.gpu || {};
    setFigure($("gpu-usage"), gpu.percent, 1);
    setGauge($("gpu-gauge"), gpu.percent);
    // 有值时说明数据来源（诚实标注），没值时说明原因
    setNote($("gpu-note"), gpu.source ? String(gpu.source) : gpu.error ? String(gpu.error) : "");
  }

  function renderDisksAndNetworks(data) {
    syncReadoutRows(diskSpecs(data).concat(networkSpecs(data)));
  }

  function renderPower(data) {
    var power = data.power || {};
    var cpu = power.cpu_watts;
    var gpu = power.gpu_watts;

    setFigure($("power-total"), power.total_watts, 1);

    var parts = [];
    if (isNum(cpu)) parts.push("CPU " + fixed(cpu, 1) + " W");
    if (isNum(gpu)) parts.push("GPU " + fixed(gpu, 1) + " W");
    if (parts.length) setNote($("power-note"), parts.join("　"));
    else if (isNum(power.total_watts)) setNote($("power-note"), "整机读数");
    else setNote($("power-note"), "这台机器没有可读取的功耗接口");
  }

  function renderUptime(data) {
    var uptime = data.uptime || {};
    setTextValue($("uptime-text"), uptime.text);
  }

  /* --------------------------------- 网站 --------------------------------- */

  function sitesSignature(sites) {
    return sites
      .map(function (site) {
        return (site.name || "") + "\u0000" + (site.url || "");
      })
      .join("\u0001");
  }

  function buildSites(sites) {
    var body = $("sites-body");
    if (!body) return;

    body.textContent = "";
    siteRefs = sites.map(function (site) {
      var row = document.createElement("tr");

      var nameCell = document.createElement("td");
      var name = document.createElement("span");
      name.className = "sites__name";
      name.textContent = site.name || "未命名";
      var url = document.createElement("span");
      url.className = "sites__url";
      url.textContent = site.url || "";
      var error = document.createElement("span");
      error.className = "sites__error";
      nameCell.append(name, url, error);

      var stateCell = document.createElement("td");
      var state = document.createElement("span");
      state.className = "state";
      var dot = document.createElement("span");
      dot.className = "dot";
      dot.setAttribute("aria-hidden", "true");
      var stateText = document.createElement("span");
      state.append(dot, stateText);
      stateCell.appendChild(state);

      var codeCell = document.createElement("td");
      codeCell.className = "sites__code";
      var latencyCell = document.createElement("td");
      latencyCell.className = "sites__latency";

      row.append(nameCell, stateCell, codeCell, latencyCell);
      body.appendChild(row);

      return { state: state, stateText: stateText, code: codeCell, latency: latencyCell, error: error };
    });
  }

  function updateSites(sites) {
    sites.forEach(function (site, index) {
      var ref = siteRefs[index];
      if (!ref) return;
      var up = site.status === "up";
      ref.state.setAttribute("data-state", up ? "up" : "down");
      setText(ref.stateText, site.status_text || (up ? "正常" : "异常"));
      setText(ref.code, isNum(site.http_code) ? site.http_code : "—");
      setText(ref.latency, isNum(site.latency_ms) ? site.latency_ms + " ms" : "—");
      setText(ref.error, site.error || "");
    });
  }

  function renderSites(data) {
    var sites = Array.isArray(data.websites) ? data.websites : [];
    var enabled = data.websites_enabled !== false;
    var table = $("sites-table");
    var empty = $("sites-empty");

    if (!sites.length) {
      if (table) table.hidden = true;
      if (empty) {
        empty.hidden = false;
        setText(
          empty,
          enabled
            ? "还没有配置网站。在 config.toml 的 [[websites.items]] 里加上第一条，就能看到它的可用性与延迟。"
            : "网站检测已在 config.toml 中关闭（websites.enabled = false）。"
        );
      }
      sitesKey = null;
      siteRefs = [];
      return;
    }

    if (table) table.hidden = false;
    if (empty) empty.hidden = true;

    var signature = sitesSignature(sites);
    if (signature !== sitesKey) {
      buildSites(sites);
      sitesKey = signature;
    }
    updateSites(sites);
  }

  /* -------------------------------- 命令行 -------------------------------- */

  function buildCli(cli) {
    var list = $("cli-list");
    if (!list) return;

    list.textContent = "";
    cliRefs = {};

    Object.keys(cli).forEach(function (key) {
      var item = cli[key] || {};

      var details = document.createElement("details");
      details.className = "cli";

      var summary = document.createElement("summary");
      var mark = document.createElement("span");
      mark.className = "cli__mark";
      mark.setAttribute("aria-hidden", "true");
      var name = document.createElement("span");
      name.className = "cli__name";
      name.textContent = item.label || key;
      var state = document.createElement("span");
      state.className = "cli__state";
      summary.append(mark, name, state);

      var output = document.createElement("pre");
      output.className = "cli__output";

      details.append(summary, output);
      list.appendChild(details);

      cliRefs[key] = { state: state, output: output };
    });
  }

  function updateCli(cli) {
    Object.keys(cli).forEach(function (key) {
      var ref = cliRefs[key];
      if (!ref) return;
      var item = cli[key] || {};
      var ok = item.success === true;

      ref.state.setAttribute("data-state", ok ? "ok" : "fail");
      setText(ref.state, ok ? "成功" : "失败");

      var text = item.output || "";
      if (item.error) text = text ? text + "\n\n" + item.error : item.error;
      setText(ref.output, text || "没有输出");
    });
  }

  function renderCli(data) {
    var cli = data.cli || {};
    var keys = Object.keys(cli);
    var list = $("cli-list");
    var empty = $("cli-empty");

    if (!keys.length) {
      if (list) list.textContent = "";
      if (empty) {
        empty.hidden = false;
        setText(
          empty,
          data.cli_enabled === false
            ? "命令行采集已在 config.toml 中关闭（cli.enabled = false）。"
            : "还没有配置命令。在 config.toml 的 [cli.commands] 里加上一条，就能在这里查看它的输出。"
        );
      }
      cliKey = null;
      cliRefs = {};
      return;
    }

    if (empty) empty.hidden = true;

    var signature = keys.join("\u0001");
    if (signature !== cliKey) {
      buildCli(cli);
      cliKey = signature;
    }
    updateCli(cli);
  }

  /* --------------------------------- 渲染 --------------------------------- */

  function render(data) {
    hasData = true;
    setConnection("up", "在线");
    renderCpu(data);
    renderGpu(data);
    renderMemory(data);
    renderDisksAndNetworks(data);
    renderPower(data);
    renderUptime(data);
    renderSites(data);
    renderCli(data);
  }

  function renderFailure(reason) {
    setConnection("down", "连接失败：" + reason);
  }

  /* --------------------------------- 轮询 --------------------------------- */

  function fetchStatus() {
    var controller = typeof AbortController === "function" ? new AbortController() : null;
    var timeoutId = controller
      ? setTimeout(function () {
          controller.abort();
        }, settings.fetchTimeout)
      : null;

    fetch("/api/status", {
      cache: "no-store",
      headers: { Accept: "application/json" },
      signal: controller ? controller.signal : undefined
    })
      .then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      })
      .then(function (data) {
        if (timeoutId) clearTimeout(timeoutId);
        render(data);
      })
      .catch(function (error) {
        if (timeoutId) clearTimeout(timeoutId);
        var reason =
          error && error.name === "AbortError"
            ? "请求超时"
            : error && error.message
              ? error.message
              : "未知错误";
        renderFailure(reason);
      });
  }

  function applyConfig(cfg) {
    if (!cfg) return;
    if (isNum(cfg.refresh_interval_ms)) settings.refreshInterval = Math.max(1000, cfg.refresh_interval_ms);
    if (isNum(cfg.fetch_timeout_ms)) settings.fetchTimeout = Math.max(1000, cfg.fetch_timeout_ms);
    if (isNum(cfg.bar_warn_percent)) settings.barWarn = cfg.bar_warn_percent;
    if (isNum(cfg.bar_crit_percent)) settings.barCrit = cfg.bar_crit_percent;
    if (isNum(cfg.speed_mb_threshold_bps)) settings.speedMbThreshold = cfg.speed_mb_threshold_bps;

    if (cfg.page_title) {
      document.title = String(cfg.page_title);
      setText($("page-title"), cfg.page_title);
    }

    // favicon：默认是静态 <link> 指向 /static/favicon.svg；
    // 只有 /api/config 显式给了不同的地址才换（换成 URL 或项目里的文件）
    const faviconHref = (cfg.favicon && typeof cfg.favicon.href === "string") ? cfg.favicon.href : "";
    if (faviconHref && faviconHref !== "/static/favicon.svg") {
      let link = document.querySelector('link[rel="icon"]');
      if (!link) {
        link = document.createElement("link");
        link.rel = "icon";
        document.head.appendChild(link);
      }
      const media = (cfg.favicon && typeof cfg.favicon.media_type === "string") ? cfg.favicon.media_type : "";
      if (media) link.type = media;
      link.href = faviconHref;
    }
  }

  function start() {
    fetch("/api/config", { cache: "no-store" })
      .then(function (response) {
        return response.ok ? response.json() : null;
      })
      .then(applyConfig)
      .catch(function () {
        /* 用内置默认值继续 */
      })
      .then(function () {
        fetchStatus();
        if (timer) clearInterval(timer);
        timer = setInterval(fetchStatus, settings.refreshInterval);
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
