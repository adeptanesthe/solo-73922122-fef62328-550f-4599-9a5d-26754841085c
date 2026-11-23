// 封口线候选决策树接管试算平台 —— 前端交互
const $ = (id) => document.getElementById(id);

let pollTimers = new Map();

async function api(url, options = {}) {
  const resp = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await resp.json().catch(() => ({ ok: false, error: "响应不是合法 JSON" }));
  if (!resp.ok || !body.ok) {
    throw new Error(body.error || `请求失败（HTTP ${resp.status}）`);
  }
  return body;
}

function showMsg(el, text, ok = true) {
  el.textContent = text;
  el.className = "msg " + (ok ? "ok" : "err");
}

function parseJsonArea(el, label) {
  try {
    return JSON.parse(el.value);
  } catch (err) {
    throw new Error(`${label}不是合法 JSON：${err.message}`);
  }
}

function parseInterval(text, label) {
  let value;
  try {
    value = JSON.parse(text);
  } catch (err) {
    throw new Error(`${label}不是合法 JSON 数组`);
  }
  if (!Array.isArray(value) || value.length !== 2) {
    throw new Error(`${label}必须是 [下限, 上限] 形式`);
  }
  return value;
}

// ---------- 示例 ----------
async function loadExamples() {
  const data = await api("/api/examples");
  const box = $("examples");
  box.innerHTML = "";
  data.examples.forEach((ex) => {
    const btn = document.createElement("button");
    btn.textContent = ex.title;
    btn.onclick = () => {
      $("original-tree").value = JSON.stringify(ex.original_tree, null, 2);
      $("candidate-tree").value = JSON.stringify(ex.candidate_tree, null, 2);
      $("d-temp").value = JSON.stringify(ex.draft.temperature);
      $("d-front").value = JSON.stringify(ex.draft.front);
      $("d-back").value = JSON.stringify(ex.draft.back);
      $("d-diff").value = JSON.stringify(ex.draft.diff);
      showMsg($("draft-msg"), `已载入「${ex.title}」，请依次保存原树、保存草稿后发起试算`, true);
    };
    box.appendChild(btn);
  });

  // 规则转换示例
  const cbox = $("conv-examples");
  cbox.innerHTML = "";
  data.conversion_examples.forEach((ex) => {
    const btn = document.createElement("button");
    btn.textContent = ex.title;
    btn.title = ex.description || "";
    btn.onclick = () => loadConversionExample(ex);
    cbox.appendChild(btn);
  });
}

function fillCondition(condition) {
  $("c-temp").value = JSON.stringify(condition.temperature);
  $("c-front").value = JSON.stringify(condition.front);
  $("c-back").value = JSON.stringify(condition.back);
  $("c-diff").value = JSON.stringify(condition.diff);
}

async function waitForTrial(jobId) {
  for (let i = 0; i < 100; i += 1) {
    const data = await api(`/api/trials/${jobId}`);
    if (!["pending", "running"].includes(data.job.status)) {
      return data.job;
    }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("试算任务超时未完成");
}

// 转换示例：冲突示例需要先自动走一遍 保存→试算→确认，使确认方案生效。
async function loadConversionExample(ex) {
  try {
    $("btn-convert").disabled = true;
    showMsg($("conv-msg"), `正在准备「${ex.title}」…`, true);
    $("original-tree").value = JSON.stringify(ex.original_tree, null, 2);
    await api("/api/original-tree", {
      method: "POST",
      body: JSON.stringify({ tree: ex.original_tree }),
    });

    if (ex.needs_confirmation) {
      $("candidate-tree").value = JSON.stringify(ex.candidate_tree, null, 2);
      const draft = ex.draft;
      $("d-temp").value = JSON.stringify(draft.temperature);
      $("d-front").value = JSON.stringify(draft.front);
      $("d-back").value = JSON.stringify(draft.back);
      $("d-diff").value = JSON.stringify(draft.diff);
      await api("/api/draft", {
        method: "POST",
        body: JSON.stringify({ candidate_tree: ex.candidate_tree, draft }),
      });
      const trial = await api("/api/trials", { method: "POST" });
      const job = await waitForTrial(trial.job_id);
      if (job.status !== "solved") {
        throw new Error(`示例试算未成功（${job.status}）：${job.result?.reason ?? job.error ?? ""}`);
      }
      await api("/api/confirmations", {
        method: "POST",
        body: JSON.stringify({ trial_id: job.id }),
      });
    }

    fillCondition(ex.condition);
    showMsg($("conv-msg"), `已载入「${ex.title}」，点击“开始转换”查看结果`, true);
    await refreshState();
  } catch (err) {
    showMsg($("conv-msg"), err.message, false);
  } finally {
    $("btn-convert").disabled = false;
  }
}

// ---------- 保存原树 ----------
$("btn-save-original").onclick = async () => {
  try {
    const tree = parseJsonArea($("original-tree"), "原树");
    const data = await api("/api/original-tree", {
      method: "POST",
      body: JSON.stringify({ tree }),
    });
    showMsg($("original-msg"), `原树已保存（revision=${data.revision}），旧试算均已过期`, true);
    await refreshState();
  } catch (err) {
    showMsg($("original-msg"), err.message, false);
  }
};

// ---------- 保存候选树 + 草稿 ----------
$("btn-save-draft").onclick = async () => {
  try {
    const candidate_tree = parseJsonArea($("candidate-tree"), "候选树");
    const draft = {
      temperature: parseInterval($("d-temp").value, "温度区间"),
      front: parseInterval($("d-front").value, "前压区间"),
      back: parseInterval($("d-back").value, "后压区间"),
      diff: parseInterval($("d-diff").value, "压差区间"),
    };
    const data = await api("/api/draft", {
      method: "POST",
      body: JSON.stringify({ candidate_tree, draft }),
    });
    showMsg($("draft-msg"), `候选树与工况草稿已保存（revision=${data.revision}），试算期间也可继续保存`, true);
    await refreshState();
  } catch (err) {
    showMsg($("draft-msg"), err.message, false);
  }
};

// ---------- 发起试算 ----------
$("btn-trial").onclick = async () => {
  try {
    const data = await api("/api/trials", { method: "POST" });
    showMsg($("draft-msg"), `试算已发起（任务 ${data.job_id.slice(0, 8)}…，基于 revision=${data.revision}）`, true);
    pollJob(data.job_id);
    setTimeout(refreshState, 50);
  } catch (err) {
    showMsg($("draft-msg"), err.message, false);
  }
};

function pollJob(jobId) {
  if (pollTimers.has(jobId)) return;
  const timer = setInterval(async () => {
    try {
      const data = await api(`/api/trials/${jobId}`);
      await renderJobs();
      if (!["pending", "running"].includes(data.job.status)) {
        clearInterval(timer);
        pollTimers.delete(jobId);
        await refreshState();
      }
    } catch (err) {
      clearInterval(timer);
      pollTimers.delete(jobId);
    }
  }, 400);
  pollTimers.set(jobId, timer);
}

// ---------- 渲染任务列表 ----------
async function renderJobs() {
  const state = await api("/api/state");
  const box = $("jobs");
  if (!state.jobs.length) {
    box.innerHTML = '<p class="hint">暂无试算任务。</p>';
    return;
  }
  box.innerHTML = '<div class="jobs"></div>';
  const list = box.querySelector(".jobs");
  // 新任务在前
  state.jobs.slice().reverse().forEach((job) => {
    const div = document.createElement("div");
    div.className = "job";
    const statusText = {
      pending: "排队中", running: "试算中", solved: "成功",
      no_solution: "无解", error: "失败",
    }[job.status] || job.status;
    let detail = "";
    if (job.status === "solved" && job.result) {
      detail = `<div class="zone">最长安全温区：[${job.result.zone[0]}, ${job.result.zone[1]}]，长度 ${job.result.length}</div>
                <div class="reason">${job.result.reason}</div>`;
    } else if (job.status === "no_solution" && job.result) {
      detail = `<div class="reason">${job.result.reason}</div>`;
    } else if (job.status === "error") {
      detail = `<div class="err-text">试算失败：${job.error ?? "未知错误"}；当前分流保持不变（沿用原树）。</div>`;
    } else {
      detail = `<div class="reason">任务进行中，请稍候…</div>`;
    }
    const staleTag = job.stale
      ? '<span class="stale">已过期（草稿已修改，确认将被拒绝）</span>'
      : '<span class="pill">当前版本</span>';
    const confirmBtn = (job.status === "solved" && !job.stale)
      ? `<button data-confirm="${job.id}">确认按此试算接管</button>` : "";
    div.innerHTML = `
      <div class="job-head">
        <span class="pill ${job.status}">${statusText}</span>
        <span>任务 ${job.id.slice(0, 8)}…</span>
        <span>基于 revision=${job.revision}</span>
        ${staleTag}
        ${confirmBtn}
      </div>${detail}`;
    list.appendChild(div);
  });
  list.querySelectorAll("button[data-confirm]").forEach((btn) => {
    btn.onclick = () => confirmTrial(btn.dataset.confirm);
  });
}

async function confirmTrial(jobId) {
  try {
    const data = await api("/api/confirmations", {
      method: "POST",
      body: JSON.stringify({ trial_id: jobId }),
    });
    const c = data.confirmation;
    showMsg($("draft-msg"),
      `已确认：温区 [${c.zone[0]},${c.zone[1]}] 内且符合获准工况的读数改用候选树（revision=${data.revision}）`, true);
    await refreshState();
  } catch (err) {
    showMsg($("draft-msg"), "确认被拒：" + err.message, false);
    await refreshState();
  }
}

// ---------- 确认信息 ----------
function renderConfirmation(state) {
  const el = $("confirmation");
  const badge = $("confirm-badge");
  const c = state.confirmation;
  if (!c) {
    el.className = "confirmation empty";
    el.textContent = "尚无确认方案，当前所有读数均由原树分流。";
    badge.classList.add("hidden");
    return;
  }
  badge.classList.remove("hidden");
  el.className = "confirmation";
  const d = c.draft;
  el.textContent =
    `确认时间：${c.confirmed_at}\n` +
    `接管温区（整数闭区间）：[${c.zone[0]}, ${c.zone[1]}]\n` +
    `获准工况 —— 前压 [${d.front[0]}, ${d.front[1]}]，` +
    `后压 [${d.back[0]}, ${d.back[1]}]，` +
    `后压−前压 [${d.diff[0]}, ${d.diff[1]}]\n` +
    `来源试算：${c.trial_id.slice(0, 8)}…（确认时 revision=${c.revision}）\n` +
    `规则：只有温区内且同时满足上述获准工况的读数使用候选树，其余读数一律使用原树。`;
}

// ---------- 读数查询 ----------
$("btn-classify").onclick = async () => {
  try {
    const body = {
      temperature: Number($("r-temp").value),
      front: Number($("r-front").value),
      back: Number($("r-back").value),
    };
    const data = await api("/api/classify", { method: "POST", body: JSON.stringify(body) });
    $("classify-result").textContent = JSON.stringify({
      读数: data.readings,
      使用的树: data.tree_used === "candidate" ? "候选树（已确认接管）" : "原树",
      分拣类别: data.category,
      判定依据: data.reason,
    }, null, 2);
  } catch (err) {
    $("classify-result").textContent = "查询失败：" + err.message;
  }
};

// ---------- 总刷新 ----------
async function refreshState() {
  const state = await api("/api/state");
  $("revision").textContent = state.revision;
  renderConfirmation(state);
  await renderJobs();
}

// ---------- 备用控制器规则转换 ----------
let lastConversion = null;

function readCondition() {
  return {
    temperature: parseInterval($("c-temp").value, "温度区间"),
    front: parseInterval($("c-front").value, "前压区间"),
    back: parseInterval($("c-back").value, "后压区间"),
    diff: parseInterval($("c-diff").value, "压差区间"),
  };
}

function renderConversion(data) {
  const ok = data.download_allowed;
  showMsg($("conv-msg"), data.reason, ok);
  const summary = {
    转换状态: { solved: "成功（可下载）", conflict: "存在冲突（已禁用下载）", no_solution: "工况无解" }[data.status] || data.status,
    生效分流: data.routing_basis === "candidate_confirmation" ? "已确认方案（获准读数走候选树，其余走原树）" : "原树（无确认方案）",
    工况: data.condition,
    规则条数: data.rule_count,
    冲突组数: data.conflict_count,
    统计: data.stats,
  };
  let detail = "";
  if (ok) {
    detail = "\n\n规则列表（每条：类别 + 温度/前压码/后压码/码差 四个闭区间）：\n"
      + JSON.stringify(data.rules, null, 2);
  } else if (data.status === "conflict") {
    detail = "\n\n冲突明细（同温度、同前后压力码但类别不同的两组真值）：\n"
      + JSON.stringify(data.conflicts, null, 2);
  }
  $("conv-result").textContent = JSON.stringify(summary, null, 2) + detail;

  const dl = $("btn-download");
  dl.disabled = !ok;
  if (ok) {
    lastConversion = data;
  } else {
    lastConversion = null;
  }
}

$("btn-convert").onclick = async () => {
  try {
    const condition = readCondition();
    $("btn-convert").disabled = true;
    const data = await api("/api/rule-conversions", {
      method: "POST",
      body: JSON.stringify(condition),
    });
    renderConversion(data);
  } catch (err) {
    showMsg($("conv-msg"), "转换失败：" + err.message, false);
    $("btn-download").disabled = true;
    lastConversion = null;
  } finally {
    $("btn-convert").disabled = false;
  }
};

$("btn-download").onclick = () => {
  if (!lastConversion || !lastConversion.download_allowed) return;
  const payload = {
    generated_by: "封口线备用控制器规则转换",
    routing_basis: lastConversion.routing_basis,
    condition: lastConversion.condition,
    rule_count: lastConversion.rule_count,
    rules: lastConversion.rules.map((r) => ({
      category: r.category,
      temperature: r.temperature,
      front_code: r.front_code,
      back_code: r.back_code,
      code_diff: r.code_diff,
    })),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "backup-controller-rules.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
};

loadExamples().catch((err) => showMsg($("draft-msg"), err.message, false));
refreshState().catch((err) => showMsg($("draft-msg"), err.message, false));
setInterval(renderJobs, 2000); // 轮询刷新任务状态与过期标记
