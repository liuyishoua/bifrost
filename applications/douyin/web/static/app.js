import { escapeHTML as e, metric, remaining, sendCountdown, sendNumberLabel } from "./utils.mjs";
import { lineChart, bindLineChart } from "./charts.mjs";
import { workScheduleSummary, editWorkTime, editAccountsBatch } from "./account-settings.mjs";
import { openConversation } from "./conversation.mjs";
import { bindTaskMessageEditor } from "./message-editor.mjs";
import { replyReviewPanel, shouldRefreshTask, taskRepliesPanel, createTaskReplies, replyCheckFields } from "./task-reply-review.mjs";
import { taskFollowupsPanel, createTaskFollowups } from "./task-followups.mjs";
import { recordMessageContent, messageNumberLabel, messageStageOptions } from "./send-record-content.mjs";
import { createAccountOverview } from "./account-performance.mjs";
import { APP_PREFIX, appURL } from "./mount.mjs";
const app = document.querySelector("#app");
const dialog = document.querySelector("#dialog");
let closeConversation = null;
const icons = {
  overview: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  users:
    "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M16 3a4 4 0 0 1 0 8M22 21v-2a4 4 0 0 0-3-3.87M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  "send-details": "M4 3h16v18H4zM8 7h8M8 11h8M8 15h5",
  tasks: "M9 5H5v16h14V5h-4M9 3h6v4H9zM8 12h8M8 16h5",
  tags: "M3 3h8l10 10-8 8L3 11zM7 7h.01",
  "message-templates": "M4 3h16v14H9l-5 4zM8 7h8M8 11h6",
  accounts: "M20 21a8 8 0 0 0-16 0M16 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
  arrow: "M5 12h14M13 6l6 6-6 6",
  chart: "M4 19V5M4 19h16M8 14l4-5 4 3 4-7",
  box: "M3 7l9-4 9 4v12l-9 3-9-3zM3 7l9 4 9-4M12 11v11",
};
const icon = (name) =>
  `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${icons[name] || icons.box}"/></svg>`;
const labels = {
  pending: "未开始",
  running: "进行中",
  paused: "已暂停",
  completed: "已完成",
  interrupted: "已中断",
  failed: "失败",
  sent: "成功",
  skipped: "已跳过",
  sending: "请求中",
  ready: "可用",
  checking: "检查中",
  unbound: "待扫码",
  confirmed: "已登录",
  starting: "正在生成二维码",
  scanned: "已扫码，待确认",
  waiting: "等待扫码",
  verifying: "等待官方验证",
  unknown: "未检查",
  expired: "已失效",
  error: "异常",
  idle: "未扫码",
  search_ready: "可搜索",
};
const badge = (status) =>
  `<span class="badge ${e(status)}"><i class="dot"></i>${e(labels[status] || status || "未检查")}</span>`;
const date = (value) =>
  value
    ? new Date(Number(value) * 1000).toLocaleString("zh-CN", { hour12: false })
    : "—";
const clock = (value) =>
  value
    ? new Date(Number(value) * 1000).toLocaleTimeString("zh-CN", {
        hour12: false,
      })
    : "—";
let meta = { demo: false },
  accounts = [],
  generation = 0,
  timers = new Set(),
  toastTimer;
let lastSearchId = null;
try {
  lastSearchId = sessionStorage.getItem("dy.search.id");
} catch {}
const searchState = {
  id: lastSearchId,
  selected: new Set(),
  filters: {},
  page: 1,
  job: null,
};
const userState = { selected: new Set(), filters: {}, page: 1 };
function toast(message) {
  const node = document.querySelector("#toast");
  node.textContent = message;
  node.classList.add("visible");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("visible"), 4000);
}
async function api(path, method = "GET", body, extra = {}) {
  const options = { method, headers: {}, ...extra };
  if (method !== "GET") {
    options.headers = {
      "Content-Type": "application/json",
      "X-App-Request": "1",
    };
    options.body = JSON.stringify(body ?? {});
  }
  const response = await fetch(appURL(`/api${path}`), options);
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error("服务暂时不可用，请稍后重试");
  }
  if (!response.ok) {
    const error = new Error(data.error || "操作失败");
    error.status = response.status;
    throw error;
  }
  return data;
}
const params = (obj) =>
  new URLSearchParams(
    Object.entries(obj).filter(([, v]) => v !== "" && v != null),
  ).toString();
function formValues(form) {
  return Object.fromEntries(
    [...new FormData(form)].filter(([, v]) => v !== ""),
  );
}
function bind(selector, event, fn, root = document) {
  const node = root.querySelector(selector);
  if (node)
    node.addEventListener(event, async (ev) => {
      try {
        await fn(ev);
      } catch (err) {
        toast(err.message);
      }
    });
}
function onAll(selector, event, fn, root = document) {
  root.querySelectorAll(selector).forEach((node) => {
    node.addEventListener(event, async (ev) => {
      try {
        await fn(ev);
      } catch (err) {
        toast(err.message);
      }
    });
  });
}
async function submit(ev, work) {
  ev.preventDefault();
  const button = ev.submitter;
  if (button) button.disabled = true;
  try {
    await work();
  } finally {
    if (button) button.disabled = false;
  }
}
function poll(work, ms = 1600) {
  const token = generation;
  let stopped = false;
  function schedule() {
    const timer = setTimeout(() => {
      timers.delete(timer);
      tick();
    }, ms);
    timers.add(timer);
  }
  async function tick() {
    if (stopped || token !== generation) return;
    try {
      await work();
    } catch (err) {
      if (token === generation) toast(err.message);
    }
    if (!stopped && token === generation) schedule();
  }
  schedule();
  return () => {
    stopped = true;
  };
}
function navigate(path) {
  history.pushState({}, "", appURL(path));
  render();
}
document.addEventListener("click", (ev) => {
  const link = ev.target.closest("a[data-nav]");
  if (link && !ev.metaKey && !ev.ctrlKey && !ev.shiftKey) {
    ev.preventDefault();
    navigate(link.getAttribute("href"));
  }
});
window.addEventListener("popstate", render);
function mode() {
  return `<span class="mode ${meta.demo ? "" : "live"}"><i class="dot"></i>${meta.demo ? "演示模式 · 模拟数据与发送" : "真实账号模式"}</span>`;
}
function shell(section, title, body) {
  document.body.className = "";
  app.innerHTML = `<div class="shell"><aside class="sidebar"><a href="search" data-nav class="brand"><span class="brandmark">♪</span><span>抖音工作台</span></a><div class="navcaption">WORKSPACE</div>${[
    ["overview", "总览大盘"],
    ["search", "搜索入库"],
    ["users", "用户管理"],
    ["tags", "标签管理"],
    ["message-templates", "话术管理"],
    ["tasks", "任务管理"],
    ["send-details", "对话详情"],
    ["accounts", "账号管理"],
  ]
    .map(
      ([key, label]) =>
        `<a href="${key}" data-nav class="navlink ${section === key ? "active" : ""}">${icon(key)}<span>${label}</span></a>`,
    )
    .join(
      "",
    )}<div class="sidefoot"><b>本地工作空间</b><br>让每一次连接，有迹可循。<br><span>DOUYIN CONSOLE · 01</span></div></aside><main class="main"><header class="topbar"><span>工作空间 <span style="margin:0 12px;color:#c5cbd4">/</span><strong>${e(title)}</strong></span>${mode()}</header><div class="content">${body}</div></main></div>`;
}
function heading(english, title, subtitle, action = "") {
  return `<div class="pagehead"><div><div class="eyebrow">${english}</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div>${action}</div>`;
}
function empty(title, detail = "") {
  return `<div class="empty">${icon("box")}<strong>${title}</strong>${detail}</div>`;
}
function accountOptions(send = false, selected = "") {
  return `<option value="">请选择${send ? "执行" : "查询"}账号</option>${accounts
    .filter((a) => (send ? a.can_send : a.can_search))
    .map(
      (a) =>
        `<option value="${e(a.id)}" ${String(a.id) === String(selected) ? "selected" : ""}>${e(a.name)}</option>`,
    )
    .join("")}`;
}
function person(user) {
  const avatar = /^https?:\/\//i.test(user.avatar_url || "")
    ? `<img src="${e(user.avatar_url)}" alt="" loading="lazy" referrerpolicy="no-referrer">`
    : "";
  return `<div class="person"><span class="avatar">${e((user.nickname || "?").slice(0, 1))}${avatar}</span><div><strong>${e(user.nickname || "未命名用户")}</strong><small>${e(user.douyinhao || user.uid)}</small></div></div>`;
}
function numericFilters(values = {}) {
  return `<label>粉丝数大于<input name="min_followers" type="number" min="0" placeholder="不限" value="${e(values.min_followers ?? "")}"></label><label>累计获赞数大于<input name="min_likes" type="number" min="0" placeholder="不限" value="${e(values.min_likes ?? "")}"></label>`;
}
function verificationFilter(value) {
  return `<label>蓝 V 认证<select name="blue_v">${[
    ["all", "全部"],
    ["yes", "蓝 V（企业认证）"],
    ["no", "非蓝 V"],
    ["unknown", "未知 / 未采集"],
  ]
    .map(
      ([v, label]) =>
        `<option value="${v}" ${value === v ? "selected" : ""}>${label}</option>`,
    )
    .join("")}</select></label>`;
}
function sortSelect(value) {
  return `<label>排序<select name="sort">${[
    ["created_desc", "最新入库"],
    ["followers_desc", "粉丝数从高到低"],
    ["likes_desc", "获赞数从高到低"],
    ["name", "昵称"],
  ]
    .map(
      ([v, l]) =>
        `<option value="${v}" ${value === v ? "selected" : ""}>${l}</option>`,
    )
    .join("")}</select></label>`;
}
function selectionToolbar(state, total, search) {
  return `<div class="toolbar"><div class="selection">已选 <strong data-selected-count>${state.selected.size}</strong> 人<span class="muted">/ 筛选结果 ${total} 人</span><button class="subtle small" data-select-all>选择全部筛选结果</button><button class="subtle small" data-clear>清空</button><span class="row" style="gap:5px">前 <input type="number" min="1" value="${e(state.firstCount ?? 10)}" aria-label="选择前几人" data-first-count> 人 <button class="small" data-select-first>选择</button></span></div><div class="row">${search ? '<div class="import-actions"><button class="primary" data-import>入库选中用户</button><button class="primary" data-import-task>一键入库并创建任务</button></div>' : '<button data-bulk-tags>批量标签</button><button data-bulk-note>批量备注</button><button class="danger" data-delete-users>删除选中</button><button class="primary" data-create-task>创建发送任务</button>'}</div></div>`;
}
function pagination(data) {
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  return `<div class="pager"><span>共 ${data.total} 人 / ${pages} 页</span><div class="row"><button class="small" data-prev ${data.page <= 1 ? "disabled" : ""}>上一页</button><span>第 ${data.page} 页</span><button class="small" data-next ${data.page >= pages ? "disabled" : ""}>下一页</button></div></div>`;
}
function userTable(items, state, search) {
  return `<div class="tablewrap"><table><thead><tr><th><input type="checkbox" aria-label="选择本页用户" data-page-select ${items.length && items.every((u) => state.selected.has(u.uid)) ? "checked" : ""}></th><th>用户 / 抖音号</th><th>粉丝数</th><th>累计获赞</th><th>蓝 V 认证</th>${search ? "<th>简介</th>" : "<th>来源 / 入库时间</th><th>发送状态</th><th>操作</th>"}</tr></thead><tbody>${items.map((u) => `<tr data-uid="${e(u.uid)}"><td><input type="checkbox" aria-label="选择 ${e(u.nickname)}" data-select-uid="${e(u.uid)}" ${state.selected.has(u.uid) ? "checked" : ""}></td><td>${person(u)}${!search ? `<div class="user-tags">${(u.tags || []).map((tag) => `<span class="user-tag">${e(tag.name)}</span>`).join("")}</div>` : ""}${u.sec_uid ? `<a class="hint" href="https://www.douyin.com/user/${encodeURIComponent(u.sec_uid)}" target="_blank" rel="noopener noreferrer">查看主页 ↗</a>` : ""}</td><td>${metric(u.follower_count)}</td><td>${metric(u.received_like_count)}</td><td class="textwrap">${u.is_blue_v === true ? `<span class="badge" style="color:#1671e8;background:#edf4ff">蓝 V · 企业认证</span><div class="hint">${e(u.enterprise_verify_reason || "")}</div>` : u.is_blue_v === false ? '<span class="hint">非蓝 V</span>' : '<span class="hint">未知 / 未采集</span>'}</td>${search ? `<td class="textwrap">${e(u.signature || "—")}</td>` : `<td>${e(u.source_keyword || "—")}<div class="hint">${date(u.created_at)}</div></td><td>${u.replied ? '<span class="badge reply"><i class="dot"></i>用户已回复</span>' : u.sent ? '<span class="badge sent"><i class="dot"></i>已发送</span><div class="hint">以平台发送成功为准</div>' : '<span class="badge">未发送</span>'}${u.replied ? `<div class="hint">${date(u.last_reply_at)}</div>` : u.last_sent_at ? `<div class="hint">${date(u.last_sent_at)}</div>` : ""}</td><td><button class="subtle small" data-tags-user="${e(u.uid)}">标签</button><button class="subtle small" data-edit="${e(u.uid)}">编辑</button><button class="subtle small" data-conversation="${e(u.uid)}">对话</button><button class="subtle small" data-history="${e(u.uid)}">历史</button></td>`}</tr>`).join("")}</tbody></table></div>`;
}
function bindSelection(state, data, base, reload) {
  const update = () => {
    document.querySelector("[data-selected-count]").textContent =
      state.selected.size;
    document
      .querySelectorAll("[data-select-uid]")
      .forEach((n) => (n.checked = state.selected.has(n.dataset.selectUid)));
    const all = document.querySelector("[data-page-select]");
    if (all)
      all.checked =
        data.items.length > 0 &&
        data.items.every((u) => state.selected.has(u.uid));
  };
  onAll("[data-select-uid]", "change", (ev) => {
    const uid = ev.target.dataset.selectUid;
    if (ev.target.checked) state.selected.add(uid);
    else state.selected.delete(uid);
    update();
  });
  bind("[data-page-select]", "change", (ev) => {
    data.items.forEach((u) =>
      ev.target.checked
        ? state.selected.add(u.uid)
        : state.selected.delete(u.uid),
    );
    update();
  });
  bind("[data-clear]", "click", () => {
    state.selected.clear();
    update();
  });
  async function select(limit) {
    const result = await api(`${base}/selection`, "POST", {
      filters: state.filters,
      ...(limit ? { limit } : {}),
    });
    result.uids.forEach((uid) => state.selected.add(uid));
    update();
  }
  bind("[data-select-all]", "click", () => select());
  bind("[data-first-count]", "input", (ev) => {
    state.firstCount = ev.target.value;
  });
  bind("[data-select-first]", "click", () => {
    const n = Number(document.querySelector("[data-first-count]").value);
    if (!Number.isInteger(n) || n < 1) throw new Error("请输入有效的选择人数");
    return select(n);
  });
  bind("[data-prev]", "click", () => {
    state.page--;
    return reload();
  });
  bind("[data-next]", "click", () => {
    state.page++;
    return reload();
  });
}
function showDialog(
  title,
  body,
  footer = '<button type="button" data-close>关闭</button>',
) {
  closeConversation?.();
  closeConversation = null;
  dialog.innerHTML = `<div class="dialoghead"><h2 id="dialog-title">${title}</h2><button type="button" class="subtle" data-close aria-label="关闭弹窗">✕</button></div><div class="dialogbody">${body}</div><div class="dialogfoot">${footer}</div>`;
  onAll("[data-close]", "click", () => dialog.close(), dialog);
  if (!dialog.open) dialog.showModal();
}
async function confirmAction(title, body, action, label = "确认") {
  showDialog(
    title,
    body,
    `<button data-close>取消</button><button class="primary" id="confirm-action">${label}</button>`,
  );
  bind(
    "#confirm-action",
    "click",
    async (ev) => {
      ev.target.disabled = true;
      try {
        await action();
        dialog.close();
      } finally {
        ev.target.disabled = false;
      }
    },
    dialog,
  );
}
async function searchPage() {
  shell(
    "search",
    "搜索入库",
    `${heading("DISCOVER & COLLECT", "发现你的下一次连接", "从关键词开始，把合适的用户留在工作空间。")}<section class="panel"><form id="search-form" class="panelbody"><div class="searchbar"><label>搜索关键词<input name="query" required placeholder="试试：生活方式、咖啡、户外" value="${e(searchState.job?.query || "")}"></label><label>查询账号<select name="account_id" required>${accountOptions()}</select></label><label>查询页数<input name="max_pages" type="number" min="1" max="100" value="3" required></label><button class="primary" type="submit">搜索用户 →</button></div></form><div id="search-progress"></div></section><section class="panel"><div class="paneltitle"><h2>筛选与选择</h2><span class="hint">先筛选，再选择入库</span></div><form class="panelbody" id="search-filters"><div class="filters"><label>关键词模糊查询<input name="query" placeholder="昵称、抖音号或 UID" value="${e(searchState.filters.query || "")}"></label>${verificationFilter(searchState.filters.blue_v)}${numericFilters(searchState.filters)}${sortSelect(searchState.filters.sort)}</div><div class="filterfooter"><span class="hint">门槛严格大于，留空不限；更改筛选会清空已选用户。</span><button type="submit">应用筛选</button></div></form><div id="search-results">${empty("等待搜索结果", "选择可搜索的账号，输入关键词开始。")}</div></section>`,
  );
  const token = generation;
  const progress = (job) => {
    if (token !== generation) return;
    searchState.job = job;
    document.querySelector("#search-progress").innerHTML =
      `<div class="searchprogress"><div class="row spread"><span>${badge(job.status)} <span style="margin-left:9px">已查询 <b>${job.completed_pages}</b> / ${job.max_pages} 页</span></span><span class="muted">原始返回 ${job.returned_count ?? job.total} 人 · 去重后 ${job.total} 人 · 资料不足 ${job.invalid_count || 0} 人</span></div><div class="progress"><i style="width:${Math.min(100, (100 * job.completed_pages) / Math.max(1, job.max_pages))}%"></i></div>${job.error ? `<p class="hint">${e(job.error)}</p>` : ""}</div>`;
  };
  let importing = false;
  async function results() {
    if (!searchState.id) return;
    const data = await api(
      `/searches/${searchState.id}/results?${params({ ...searchState.filters, page: searchState.page })}`,
    );
    if (token !== generation) return;
    document.querySelector("#search-results").innerHTML =
      selectionToolbar(searchState, data.total, true) +
      (data.items.length
        ? userTable(data.items, searchState, true)
        : empty("没有符合条件的用户", "调整筛选门槛后再试试。")) +
      pagination(data);
    bindSelection(searchState, data, `/searches/${searchState.id}`, results);
    document
      .querySelectorAll("[data-import], [data-import-task]")
      .forEach((node) => {
        node.disabled = importing;
      });
    onAll("[data-import], [data-import-task]", "click", async (ev) => {
      if (importing) return;
      const uids = [...searchState.selected];
      const filters = { ...searchState.filters };
      const sid = searchState.id;
      const createTask = ev.currentTarget.hasAttribute("data-import-task");
      if (!uids.length) throw new Error("请先选择要入库的用户");
      importing = true;
      document
        .querySelectorAll("[data-import], [data-import-task]")
        .forEach((node) => {
          node.disabled = true;
        });
      try {
        const chosen = await chooseTagsDialog(
          "入库前选择标签",
          `即将入库 ${uids.length} 位用户，所选标签会添加到每位用户身上。已存在的标签会保留。`,
          null,
          false,
          createTask ? "确认入库并配置任务" : "确认入库",
        );
        if (!chosen || token !== generation) return;
        const result = await api(`/searches/${sid}/import`, "POST", {
          uids,
          tag_ids: chosen.tag_ids,
        });
        if (token !== generation) return;
        toast(
          `入库完成：新增 ${result.inserted} 人，已存在 ${result.updated} 人`,
        );
        if (createTask)
          await createTaskDialog({ selected: new Set(uids), filters }, true);
      } finally {
        importing = false;
        if (token === generation)
          document
            .querySelectorAll("[data-import], [data-import-task]")
            .forEach((node) => {
              node.disabled = false;
            });
      }
    });
  }
  let stopSearch = () => {};
  async function watch() {
    stopSearch();
    const job = await api(`/searches/${searchState.id}`);
    progress(job);
    await results();
    if (job.status === "running") {
      let lastTotal = job.total;
      stopSearch = poll(async () => {
        const next = await api(`/searches/${searchState.id}`);
        progress(next);
        if (next.total !== lastTotal || next.status !== "running") {
          lastTotal = next.total;
          await results();
        }
        if (next.status !== "running") stopSearch();
      });
    }
  }
  bind("#search-form", "submit", (ev) =>
    submit(ev, async () => {
      const values = formValues(ev.target);
      const created = await api("/searches", "POST", {
        ...values,
        max_pages: Number(values.max_pages),
      });
      searchState.id = created.id;
      try {
        sessionStorage.setItem("dy.search.id", created.id);
      } catch {}
      searchState.page = 1;
      searchState.selected.clear();
      await watch();
    }),
  );
  bind("#search-filters", "submit", (ev) =>
    submit(ev, async () => {
      searchState.filters = formValues(ev.target);
      searchState.selected.clear();
      searchState.page = 1;
      await results();
    }),
  );
  if (searchState.id) {
    try {
      await watch();
    } catch (error) {
      searchState.id = null;
      try {
        sessionStorage.removeItem("dy.search.id");
      } catch {}
      toast(`上次搜索暂不可用，可重新搜索：${error.message}`);
    }
  }
}
async function usersPage() {
  const pageToken = generation;
  const tags = (await api("/tags")).items;
  if (pageToken !== generation) return;
  const f = userState.filters;
  shell(
    "users",
    "用户管理",
    `${heading("YOUR AUDIENCE", "每一位用户，都有记录", "筛选、管理与连接，从一个清晰的用户库开始。", '<button class="primary" data-top-create>创建发送任务</button>')}<section class="panel"><form id="user-filters" class="panelbody"><div class="filters"><label>昵称 / 抖音号 / 来源<input name="query" placeholder="搜索昵称、抖音号或来源关键词" value="${e(f.query || "")}"></label><label>发送状态<select name="sent">${[
      ["all", "全部用户"],
      ["unsent", "未发送"],
      ["sent", "已发送"],
      ["replied", "用户已回复"],
    ]
      .map(
        ([v, l]) =>
          `<option value="${v}" ${f.sent === v ? "selected" : ""}>${l}</option>`,
      )
      .join(
        "",
      )}</select></label>${sortSelect(f.sort)}${verificationFilter(f.blue_v)}${numericFilters(f)}<label>入库日期从<input type="date" name="created_from" value="${e(f.created_from || "")}"></label><label>入库日期至<input type="date" name="created_to" value="${e(f.created_to || "")}"></label><label>发送账号<select name="account_id"><option value="">全部账号</option>${accounts.map((a) => `<option value="${e(a.id)}" ${String(f.account_id) === String(a.id) ? "selected" : ""}>${e(a.name)}</option>`).join("")}</select></label><label>发送日期从<input type="date" name="sent_from" value="${e(f.sent_from || "")}"></label><label>发送日期至<input type="date" name="sent_to" value="${e(f.sent_to || "")}"></label></div><details class="tag-filter"><summary>按标签筛选${f.tag_ids ? ` · 已选 ${String(f.tag_ids).split(",").length} 个` : ""}</summary>${tagChoices(
      tags,
      String(f.tag_ids || "")
        .split(",")
        .filter(Boolean),
    )}<label>标签匹配方式<select name="tag_mode"><option value="any" ${f.tag_mode !== "all" ? "selected" : ""}>匹配任一标签</option><option value="all" ${f.tag_mode === "all" ? "selected" : ""}>同时具备全部标签</option></select></label></details><div class="filterfooter"><span class="hint">数值条件严格大于；翻页保留选择，更改筛选后清空。</span><div class="row"><button type="button" id="reset-filters">重置</button><button type="submit" class="primary">筛选用户</button></div></div></form><div id="users-results"></div></section>`,
  );
  const token = generation;
  async function load() {
    const data = await api(
      `/users?${params({ ...userState.filters, page: userState.page })}`,
    );
    if (token !== generation) return;
    document.querySelector("#users-results").innerHTML =
      selectionToolbar(userState, data.total, false) +
      (data.items.length
        ? userTable(data.items, userState, false)
        : empty("用户库还是空的", "前往搜索入库，选择你希望管理的用户。")) +
      pagination(data) +
      (data.summary
        ? `<div class="user-summary" aria-label="用户库统计"><span class="hint">全部用户库</span><span>用户总数 <strong>${metric(data.summary.total)}</strong></span><span>已发送用户数 <strong>${metric(data.summary.sent)}</strong></span><span>未发送用户数 <strong>${metric(data.summary.unsent)}</strong></span></div>`
        : "");
    bindSelection(userState, data, "/users", load);
    bind("[data-create-task]", "click", () => createTaskDialog());
    async function editTags(uids, selected, bulk) {
      if (!uids.length) throw new Error("请先选择用户");
      const chosen = await chooseTagsDialog(
        bulk ? "批量维护标签" : "编辑用户标签",
        `本次修改 ${uids.length} 位用户的标签。`,
        selected,
        bulk,
      );
      if (!chosen || token !== generation) return;
      await api("/users/tags", "POST", { uids, ...chosen });
      toast("用户标签已更新");
      await load();
    }
    bind("[data-bulk-tags]", "click", () =>
      editTags([...userState.selected], [], true),
    );
    onAll("[data-tags-user]", "click", (ev) => {
      const user = data.items.find(
        (u) => u.uid === ev.currentTarget.dataset.tagsUser,
      );
      return editTags(
        [user.uid],
        (user.tags || []).map((tag) => tag.id),
        false,
      );
    });
    bind("[data-bulk-note]", "click", () => {
      const uids = [...userState.selected];
      if (!uids.length) throw new Error("请先选择用户");
      showDialog(
        "批量修改备注",
        `<form id="bulk-note-form"><p class="hint">为已选的 ${uids.length} 位用户统一替换本地备注。留空保存将清空备注，平台资料保持不变。</p><label>本地备注<textarea name="note" rows="5" placeholder="输入统一备注"></textarea></label></form>`,
        '<button data-close>取消</button><button class="primary" type="submit" form="bulk-note-form">保存备注</button>',
      );
      bind(
        "#bulk-note-form",
        "submit",
        (ev) =>
          submit(ev, async () => {
            const note = new FormData(ev.target).get("note");
            const result = await api("/users/bulk-edit", "POST", {
              uids,
              note,
            });
            dialog.close();
            toast(`已更新 ${result.updated} 位用户的备注`);
            await load();
          }),
        dialog,
      );
    });
    bind("[data-delete-users]", "click", () => {
      if (!userState.selected.size) throw new Error("请先选择用户");
      const uids = [...userState.selected];
      return confirmAction(
        "删除选中用户",
        `<p>将从用户库删除 <strong>${uids.length}</strong> 位用户。已有任务与发送历史会保留。</p><p class="hint">执行中的任务目标不能删除。</p>`,
        async () => {
          const result = await api("/users/bulk-delete", "POST", { uids });
          userState.selected.clear();
          toast(`已删除 ${result.deleted} 人`);
          await load();
        },
        "确认删除",
      );
    });
    onAll("[data-edit]", "click", (ev) => {
      const u = data.items.find(
        (item) => item.uid === ev.currentTarget.dataset.edit,
      );
      showDialog(
        "编辑用户资料",
        `<form id="edit-user" class="formgrid"><label class="wide">昵称<input name="nickname" required value="${e(u.nickname)}"></label><label class="wide">简介<textarea name="signature">${e(u.signature)}</textarea></label><label class="wide">本地备注<textarea name="note" placeholder="仅保存在本地工作空间">${e(u.note)}</textarea></label><p class="hint wide">资料更新时间：${date(u.updated_at)}<br>UID 与平台统计保持不变；修改仅用于本地管理。</p></form>`,
        `<button data-close>取消</button><button type="submit" form="edit-user" class="primary">保存修改</button>`,
      );
      bind(
        "#edit-user",
        "submit",
        (ev) =>
          submit(ev, async () => {
            await api(
              `/users/${encodeURIComponent(u.uid)}`,
              "PATCH",
              Object.fromEntries(new FormData(ev.target)),
            );
            dialog.close();
            toast("资料已保存");
            await load();
          }),
        dialog,
      );
    });
    onAll("[data-conversation]", "click", (ev) => {
      const user = data.items.find(item => item.uid === ev.currentTarget.dataset.conversation);
      closeConversation = openConversation({dialog,showDialog,api,user,
        onClose:() => {if(token===generation)load().catch(err=>toast(err.message));}});
    });
    onAll("[data-history]", "click", (ev) => {
      navigate(`/send-details?uid=${encodeURIComponent(ev.currentTarget.dataset.history)}&category=all`);
    });
  }
  bind("#user-filters", "submit", (ev) =>
    submit(ev, async () => {
      userState.filters = {
        ...formValues(ev.target),
        tag_ids: new FormData(ev.target).getAll("tag_ids").join(","),
      };
      userState.selected.clear();
      userState.page = 1;
      await load();
    }),
  );
  bind("#reset-filters", "click", () => {
    userState.filters = {};
    userState.selected.clear();
    userState.page = 1;
    return usersPage();
  });
  bind("[data-top-create]", "click", () => createTaskDialog());
  await load();
}
function executionFields(task = null) {
  const mode = task?.execution_mode || "auto";
  const selected = task?.account_ids || [];
  return `<label class="wide">执行模式<select name="execution_mode"><option value="auto" ${mode === "auto" ? "selected" : ""}>自动分配账号</option><option value="specified" ${mode === "specified" ? "selected" : ""}>指定账号</option></select></label><fieldset class="wide account-choices" data-specified-accounts><legend>指定执行账号（可多选）</legend>${accounts.map(a => `<label class="account-choice"><input type="checkbox" name="account_ids" value="${e(a.id)}" ${selected.includes(a.id) ? "checked" : ""} ${!a.can_send && !selected.includes(a.id) ? 'data-unavailable="1"' : ""}><span>${e(a.name)}<small>${a.can_send ? "发送凭证就绪" : "需重新鉴权"}</small></span></label>`).join("") || '<p class="hint">请先在账号管理添加账号并完成鉴权。</p>'}</fieldset><p class="wide hint" data-execution-hint></p>`;
}
function bindExecutionFields() {
  const select = dialog.querySelector('[name="execution_mode"]');
  const update = () => {
    const automatic = select.value === "auto";
    dialog.querySelector('[data-specified-accounts]').hidden = automatic;
    dialog.querySelectorAll('[name="account_ids"]').forEach(input => { input.disabled = automatic || input.dataset.unavailable === "1"; });
    dialog.querySelector('[data-execution-hint]').textContent = automatic
      ? "所有未借调的可用账号自动参与共享池执行。发送节奏与错误率保护使用各账号的配置。"
      : "任务进行中会借调所选账号，暂时退出共享池；任务暂停或结束后归还。账号等待与休息仍然生效。";
  };
  select.addEventListener("change", update);
  update();
}
function executionValues(form) {
  const values = new FormData(form);
  return { execution_mode: values.get("execution_mode"), account_ids: values.getAll("account_ids") };
}
function taskLimitFields(task = null) {
  return `<label>允许累计失败次数<input name="max_errors" type="number" min="0" max="10000" value="${e(task?.max_errors ?? 1000)}" required></label><label>允许连续失败次数<input name="max_consecutive_errors" type="number" min="0" max="10000" value="${e(task?.max_consecutive_errors ?? 1000)}" required></label><p class="wide hint">两项都超过上限才暂停任务；成功只清零连续失败；4002、8101、21003、31003 及满足成功条件但未返回业务码的响应计成功；其他非零业务码、超时及不完整响应计失败。账号错误率休息独立生效。</p>`;
}

function templatePicker(templates) {
  return `<div class="wide template-picker"><strong>首次发送话术（可多选）</strong><div class="targets">${templates.map(item=>`<label class="targetchip"><input type="checkbox" data-first-template value="${e(item.id)}">${e(item.title)}</label>`).join('')}</div><p class="hint" id="template-description">${templates.length?'勾选多个话术后点击使用，每个联系人随机发送一条。':'暂无话术，可在话术管理中新增，也可在下方直接填写。'}</p><button type="button" id="apply-template" disabled>使用选中文案</button></div>`;
}
function bindTemplatePicker(templates, messageEditor) {
  const selected=()=>templates.filter(item=>[...dialog.querySelectorAll('[data-first-template]:checked')].some(input=>input.value===item.id));
  dialog.querySelectorAll('[data-first-template]').forEach(input=>input.addEventListener('change',()=>{
    const items=selected();
    dialog.querySelector('#apply-template').disabled=!items.length;
    dialog.querySelector('#template-description').textContent=items.length?`已选择 ${items.length} 条；每个联系人随机发送其中一条。`:'请选择首次发送文案。';
  }));
  bind('#apply-template','click',()=>{
    try {messageEditor.setFirstTexts(selected().map(item=>item.content));toast('候选文案已填入，可继续修改');}
    catch(error){toast(error.message);}
  },dialog);
}

async function createTaskDialog(selection = userState, imported = false) {
  const uids = [...selection.selected];
  if (!uids.length) throw new Error("请先选择要发送的用户");
  const token = generation;
  const [templateData, accountData] = await Promise.all([api("/message-templates"), api("/accounts")]);
  const templates = templateData.items;
  accounts = accountData.items;
  if (token !== generation) return;
  showDialog(
    "创建发送任务",
    `<form id="create-task" class="formgrid"><div class="wide callout" style="margin:0">目标已固定：<strong>${uids.length} 人</strong>，${imported ? "来自本次搜索选中并入库的用户" : "来自用户库跨页选择"}。<br>创建后为「未开始」，点击开始才会执行。${imported ? "<br>用户已入库；取消创建不会撤回入库。" : ""}</div><details class="wide"><summary class="hint">查看接收者 UID 与筛选范围</summary><div class="targets">${uids.map((uid) => `<span class="targetchip">${e(uid)}</span>`).join("")}</div><div class="hint">${e(filterSummary(selection.filters))}</div></details><label class="wide">任务名称<input name="name" required maxlength="120" placeholder="例如：秋日生活方式合作"></label>${executionFields()}${templatePicker(templates)}<div class="wide" id="task-message-editor"></div>${taskLimitFields()}${replyCheckFields()}<label class="wide">预约时间（可选）<input name="scheduled_at" type="datetime-local"><span class="hint">手动开始后等待预约时间；留空则点击开始后立即进入执行。</span></label><details class="wide"><summary class="hint">可选发送门槛 · 不满足的目标会标记跳过</summary><div class="formgrid" style="margin-top:15px">${numericFilters()}</div></details></form>`,
    `<button data-close>取消</button><span class="row" id="create-task-actions"><button class="primary" type="submit" form="create-task">创建任务</button></span>`,
  );
  bindExecutionFields();
  const messageEditor = bindTaskMessageEditor(dialog.querySelector("#task-message-editor"), {api, onError: toast});
  bindTemplatePicker(templates, messageEditor);
  const requestId = crypto.randomUUID();
  let creating = false;
  bind(
    "#create-task",
    "submit",
    (ev) =>
      submit(ev, async () => {
        if (creating) return;
        creating = true;
        const buttons = [...dialog.querySelectorAll('[form="create-task"]')];
        buttons.forEach((button) => (button.disabled = true));
        try {
          const values = formValues(ev.target);
          const filters = {};
          for (const key of ["min_followers", "min_likes"])
            if (values[key] != null) filters[key] = Number(values[key]);
          const result = await api("/tasks", "POST", {
            request_id: requestId,
            name: values.name,
            ...executionValues(ev.target),
            uids,
            ...messageEditor.values(),
            max_errors: Number(values.max_errors),
            max_consecutive_errors: Number(values.max_consecutive_errors),
            reply_check_interval_minutes: Number(values.reply_check_interval_minutes),
            scheduled_at: values.scheduled_at
              ? new Date(values.scheduled_at).toISOString()
              : null,
            filters,
          });
          dialog.close();
          if (imported) searchState.selected.clear();
          else selection.selected.clear();
          navigate(`/tasks/${result.id}`);
        } finally {
          creating = false;
          buttons.forEach((button) => (button.disabled = false));
        }
      }),
    dialog,
  );
}
function filterSummary(filters) {
  const names = {
    query: "搜索",
    tag_ids: "标签 ID",
    tag_mode: "标签匹配",
    min_followers: "粉丝大于",
    min_likes: "获赞大于",
    blue_v: "蓝 V 认证",
    sent: "发送状态",
    created_from: "入库起",
    created_to: "入库止",
    sent_from: "发送起",
    sent_to: "发送止",
    account_id: "发送账号",
    sort: "排序",
  };
  return (
    Object.entries(filters)
      .filter(([, v]) => v !== "" && v !== "all")
      .map(([k, v]) => `${names[k] || k}：${v}`)
      .join("；") || "未设置筛选条件"
  );
}
function sendRecordDetail(row) {
  if (row.message_source === "conversation") {
    showDialog(row.direction === "incoming" ? "用户回复" : "已发送消息",
      `<dl class="detailrows"><dt>执行账号</dt><dd>${e(row.account_name)}</dd><dt>用户</dt><dd>${e(row.nickname || row.uid)} · UID ${e(row.uid)}</dd><dt>方向</dt><dd>${row.direction === "incoming" ? "用户 → 执行账号" : "执行账号 → 用户"}</dd><dt>消息归属</dt><dd>${e(messageNumberLabel(row))}</dd><dt>消息时间</dt><dd>${date(row.attempted_at)}</dd></dl>${recordMessageContent(row,true)}<p class="hint">从平台会话历史同步</p>`,
      '<button data-close>关闭</button>');
    return;
  }
  const codeNames = {business_code:"业务码",http_status:"HTTP 状态",outer_status:"外层状态",send_status:"发送状态码",check_code:"校验码",response_code:"会话响应码",elapsed_ms:"耗时（毫秒）"};
  showDialog("对话详情",
    `<div class="row spread">${badge(row.status)}<span class="hint">${e(row.task_name)}${row.task_deleted ? " · 任务已删除" : ""}</span></div><dl class="detailrows"><dt>发送账号</dt><dd>${e(row.account_name)}${row.account_uid ? ` · UID ${e(row.account_uid)}` : ""}</dd><dt>接收者</dt><dd>${e(row.nickname || row.uid)}${row.is_blue_v === true ? ` <span class="badge" style="color:#1671e8;background:#edf4ff" title="${e(row.enterprise_verify_reason || "")}">蓝 V · 企业认证</span>` : ""} · UID ${e(row.uid)}</dd><dt>消息归属</dt><dd>${e(messageNumberLabel(row))}</dd><dt>发起时间</dt><dd>${date(row.attempted_at)}</dd><dt>结果记录时间</dt><dd>${date(row.finished_at || row.sent_at)}</dd></dl><p class="hint">${["snapshot","manual"].includes(row.message_source) ? "实际发送正文快照" : row.message_source === "legacy_task" ? "旧记录未保存逐条正文，以下为保留的旧任务正文" : "旧记录未保存正文"}</p>${recordMessageContent(row,true)}${row.error ? `<div class="errorbox">${e(row.error)}</div>` : ""}${row.result_note ? `<p class="hint">${e(row.result_note)}</p>` : ""}<dl class="detailrows">${Object.entries(codeNames).map(([key,label]) => `<dt>${label}</dt><dd>${e(row.codes[key] ?? "未记录")}</dd>`).join("")}</dl><p class="hint">${row.has_diagnostic ? "仅展示已记录的响应字段；业务码未记录不等于 0。" : "该旧记录没有响应诊断，无法还原当时的状态码。"} 成功按当前统计口径记录，不保证实际送达或已读。</p>`,
    '<button data-close>关闭</button>');
}
async function sendDetailsPage() {
  shell("send-details", "对话详情",
    `${heading("SEND HISTORY", "每一次交流，都有记录", "查看发送记录与用户回复。已删除任务的历史仍保留。")}<div class="record-tabs" role="tablist" aria-label="消息分类"><button type="button" role="tab" data-record-category="all">全部</button><button type="button" role="tab" data-record-category="outgoing">已发送</button><button type="button" role="tab" data-record-category="incoming">用户回复</button></div><section class="panel"><form id="send-record-filters" class="panelbody"><input type="hidden" name="category" value="outgoing"><div class="filters"><label>发送账号<select name="account_id"><option value="">全部账号</option></select></label><label>所属任务<select name="task_id"><option value="">全部任务</option></select></label><label>消息归属<select name="message_stage"><option value="">全部次数与方向</option></select></label><label>发送统计口径<select name="source_scope"><option value="">全部会话记录</option><option value="local">本系统全部发送</option><option value="first_touch">仅首次触达</option></select></label><label>发送结果<select name="status"><option value="">全部结果</option><option value="sent">成功</option><option value="failed">失败</option></select></label><label>业务码<select name="business_code"><option value="">全部业务码</option><option value="missing">未记录业务码</option></select></label><label>用户 / 消息关键词<input name="query" placeholder="昵称、抖音号、UID 或正文"></label><label>用户 UID<input name="uid" placeholder="精确筛选 UID"></label><label>消息日期从<input type="date" name="sent_from"></label><label>消息日期至<input type="date" name="sent_to"></label></div><div class="filterfooter"><span class="hint">按消息时间筛选，默认最新在前。已发送包含首次及后续发送（成功和失败均保留）。同一账号与同一用户的发送、回复分别累计，文字和图片每条算一次；历史按本地已记录消息编号，未确认次序单独标注。</span><div class="row"><button type="button" id="reset-send-records">重置</button><button class="primary" type="submit">筛选记录</button></div></div></form></section><div id="send-record-summary" class="stats" style="margin-top:20px;grid-template-columns:repeat(3,minmax(0,1fr))"></div><section class="panel"><div id="send-record-list"></div></section>`);
  const token = generation, form = document.querySelector("#send-record-filters");
  const url = new URLSearchParams(location.search);
  let filters = Object.fromEntries([...url].filter(([key]) => form.elements.namedItem(key))), page = 1, version = 0;
  for (const [key,value] of Object.entries(filters)) if (form.elements[key].tagName !== "SELECT") form.elements[key].value = value;
  filters.category = filters.category || "outgoing";
  form.elements.category.value = filters.category;
  form.elements.status.value = filters.status || "";
  form.elements.source_scope.value = filters.source_scope || "";
  let first = true;
  function options(name, values, initial) {
    const node = form.elements[name], selected = first ? filters[name] || "" : node.value;
    const markup = initial + values.map(item => `<option value="${e(item.id)}">${e(item.name)}</option>`).join("");
    if (node.innerHTML !== markup) {
      node.innerHTML = markup;
      if (selected && ![...node.options].some(o => o.value === selected)) node.add(new Option(name === "account_id" ? accounts.find(a => a.id === selected)?.name || selected : selected, selected));
      node.value = selected;
    }
  }
  async function load() {
    const current = ++version;
    const data = await api(`/send-records?${params({...filters,page})}`);
    if (token !== generation || current !== version) return;
    options("account_id", data.options.accounts, '<option value="">全部账号</option>');
    options("task_id", data.options.tasks.map(t => ({id:t.id,name:t.name+(t.deleted ? "（已删除）" : "")})), '<option value="">全部任务</option>');
    options("business_code", data.options.business_codes.map(code => ({id:String(code),name:String(code)})), '<option value="">全部业务码</option><option value="missing">未记录业务码</option>');
    options("message_stage", messageStageOptions([...(data.options.message_stages || []),...(filters.message_stage ? [filters.message_stage] : [])]), '<option value="">全部次数与方向</option>');
    first = false;
    document.querySelectorAll("[data-record-category]").forEach(button => {
      const selected = button.dataset.recordCategory === filters.category;
      button.setAttribute("aria-selected", String(selected));
    });
    const stats = filters.category === "incoming" ? [["用户回复",data.total]]
      : filters.category === "all" ? [["匹配记录",data.total],["已发送",data.summary.sent+data.summary.failed],["用户回复",data.reply_count || 0]]
      : [["匹配记录",data.total],["成功",data.summary.sent],["失败",data.summary.failed]];
    document.querySelector("#send-record-summary").innerHTML = stats.map(([label,value]) => `<div class="stat"><span>${label}</span><strong>${metric(value)}</strong></div>`).join("");
    document.querySelector("#send-record-list").innerHTML = data.items.length
      ? `<div class="tablewrap"><table><thead><tr><th>消息时间</th><th>消息归属</th><th>执行账号 / 任务</th><th>用户</th><th>消息内容</th><th>结果 / 业务码</th><th>原因</th><th>操作</th></tr></thead><tbody>${data.items.map((row,index) => `<tr><td>${date(row.attempted_at)}</td><td>${e(messageNumberLabel(row))}</td><td><strong>${e(row.account_name)}</strong><div class="hint">${e(row.task_name)}${row.task_deleted ? " · 已删除" : ""}</div></td><td>${e(row.nickname || row.uid)}${row.is_blue_v === true ? ` <span class="badge" style="color:#1671e8;background:#edf4ff" title="${e(row.enterprise_verify_reason || "")}">蓝 V · 企业认证</span>` : ""}<div class="hint">UID ${e(row.uid)}</div></td><td class="textwrap"><div class="record-content">${recordMessageContent(row)}</div>${row.message_source === "legacy_task" ? '<div class="hint">旧任务正文 · 非逐条快照</div>' : ""}</td><td>${row.direction === "incoming" ? '<span class="badge reply">用户回复</span>' : badge(row.status)}${row.message_source === "conversation" ? '<div class="hint">会话消息</div>' : `<div class="hint">业务码 ${e(row.business_code ?? "未记录")}</div>`}</td><td class="textwrap">${e(row.error || (row.direction === "incoming" ? "已收到回复" : row.message_source === "conversation" ? "已记录发送" : "发送成功"))}${row.result_note ? `<div class="hint">${e(row.result_note)}</div>` : ""}</td><td><div class="row"><button class="subtle small" data-send-record="${index}">查看详情</button><button class="subtle small" data-record-conversation="${index}" ${row.account_id ? "" : 'disabled title="旧记录未记录发送账号，无法定位对话"'}>对话</button></div></td></tr>`).join("")}</tbody></table></div>${pagination(data).replace(" 人 /", " 条记录 /")}`
      : empty("没有匹配的消息记录", "可调整分类和筛选条件。");
    onAll("[data-send-record]", "click", ev => sendRecordDetail(data.items[Number(ev.currentTarget.dataset.sendRecord)]));
    onAll("[data-record-conversation]", "click", ev => {
      const row = data.items[Number(ev.currentTarget.dataset.recordConversation)];
      closeConversation = openConversation({dialog,showDialog,api,user:row,accountId:row.account_id,
        onClose:() => {if(token===generation)load().catch(err=>toast(err.message));}});
    });
    bind("[data-prev]", "click", () => {page--;return load();});
    bind("[data-next]", "click", () => {page++;return load();});
  }
  bind("#send-record-filters", "submit", ev => submit(ev, async () => {
    filters = formValues(ev.target);page = 1;
    if (filters.message_stage) {
      filters.category = filters.message_stage.split(":")[0];
      form.elements.category.value = filters.category;
      if (filters.category === "incoming") for (const key of ["status","business_code","task_id","source_scope"]) {delete filters[key];form.elements[key].value = "";}
    }
    history.replaceState({}, "", appURL("/send-details"+(params(filters) ? "?"+params(filters) : "")));
    await load();
  }));
  bind("#reset-send-records", "click", async () => {form.reset();filters={category:"outgoing"};page=1;history.replaceState({},"",appURL("/send-details"));await load();});
  onAll("[data-record-category]", "click", async ev => {
    filters.category = ev.currentTarget.dataset.recordCategory;
    form.elements.category.value = filters.category;
    if (filters.category === "incoming") {delete filters.source_scope;form.elements.source_scope.value="";}
    if (filters.category !== "all" && filters.message_stage && !filters.message_stage.startsWith(filters.category+":")) {delete filters.message_stage;form.elements.message_stage.value = "";}
    for (const key of ["status","business_code","task_id"]) {delete filters[key];form.elements[key].value = "";}
    page = 1;
    history.replaceState({}, "", appURL("/send-details?"+params(filters)));
    await load();
  });
  await load();
  poll(load, 5000);
}

async function tasksPage() {
  shell(
    "tasks",
    "任务管理",
    `${heading("OUTREACH TASKS", "有节奏地，建立连接", "每一次执行，都从明确的目标和你的确认开始。", '<a class="primary" style="padding:11px 15px;border-radius:9px;font-size:12px" href="users" data-nav>从用户库创建任务</a>')}<section class="panel"><form id="task-filters" class="panelbody"><div class="filters"><label>任务名称<input name="query" placeholder="搜索任务"></label><label>任务状态<select name="status"><option value="">全部状态</option>${["pending", "running", "paused", "completed"].map((s) => `<option value="${s}">${labels[s]}</option>`).join("")}</select></label><label>创建日期从<input name="created_from" type="date"></label><label>创建日期至<input name="created_to" type="date"></label></div><div class="filterfooter"><span class="hint">已完成表示全部目标处理结束，包含失败或跳过。</span><button class="primary">筛选任务</button></div></form><div id="task-list"></div></section>`,
  );
  const token = generation;
  let filters = {},
    page = 1,
    requestVersion = 0;
  async function load() {
    const version = ++requestVersion;
    const data = await api(`/tasks?${params({ ...filters, page })}`);
    if (token !== generation || version !== requestVersion) return;
    if (!data.items.length && page > 1) {
      page--;
      return load();
    }
    document.querySelector("#task-list").innerHTML = data.items.length
      ? `<div class="tablewrap"><table><thead><tr><th>任务名称</th><th>执行账号</th><th>状态</th><th>目标人数</th><th>确认 / 失败 / 剩余</th><th>创建时间</th><th>执行时间</th><th>操作</th></tr></thead><tbody>${data.items.map((t) => `<tr data-task-row="${e(t.id)}"><td><a data-nav href="tasks/${encodeURIComponent(t.id)}">${e(t.name)}</a></td><td>${e(t.account_name)}<div class="hint">${t.execution_mode === "auto" ? "自动分配账号" : "指定账号"}</div></td><td>${badge(t.status)}${t.wait_reason ? `<div class="hint">${e(t.wait_reason)}</div>` : ""}</td><td>${metric(t.counts?.total)}</td><td>${t.counts?.sent || 0} <span class="muted">/</span> ${t.counts?.failed || 0} <span class="muted">/</span> ${remaining(t.counts || {})}</td><td>${date(t.created_at)}</td><td><div class="hint">预约 ${date(t.scheduled_at)}</div><div class="hint">开始 ${date(t.started_at)}</div><div class="hint">结束 ${date(t.finished_at)}</div></td><td><div class="task-list-actions">${["pending", "running", "paused"].includes(t.status) ? '<button class="small" data-edit-timing>编辑配置</button>' : ""}${t.status === "pending" ? '<button class="small primary" data-task-action="start">开始任务</button>' : t.status === "running" ? '<button class="small" data-task-action="pause">暂停任务</button>' : t.status === "paused" ? '<button class="small primary" data-task-action="resume">恢复任务</button>' : ""}<a data-nav href="tasks/${encodeURIComponent(t.id)}/dashboard">可视化大盘 ↗</a><button class="subtle small danger" data-delete-task ${t.status === "running" || t.counts.sending ? 'disabled title="请先暂停，并等待当前发送请求结束"' : ""}>删除任务</button></div></td></tr>`).join("")}</tbody></table></div>${pagination({ ...data }).replace(" 人 /", " 个任务 /")}`
      : empty(
          "准备好开启第一次连接了吗",
          "在用户管理中选中用户，创建一个发送任务。",
        );
    document.querySelectorAll("[data-task-row]").forEach(row => {
      const task = data.items.find(t => t.id === row.dataset.taskRow);
      bindTaskActions(task, load, row, load);
    });
    bind("[data-prev]", "click", () => {
      page--;
      return load();
    });
    bind("[data-next]", "click", () => {
      page++;
      return load();
    });
  }
  bind("#task-filters", "submit", (ev) =>
    submit(ev, async () => {
      filters = formValues(ev.target);
      page = 1;
      await load();
    }),
  );
  await load();
  poll(load, 5000);
}
function deleteTaskDialog(task, afterDelete) {
  return confirmAction(
    "删除任务",
    `<p>确定删除「${e(task.name)}」？任务会从列表移除，剩余目标不再执行。</p><p class="hint">用户资料及已有发送历史保留。</p>`,
    async () => {
      await api(`/tasks/${encodeURIComponent(task.id)}`, "DELETE", {});
      toast("任务已删除");
      await afterDelete();
    },
    "确认删除",
  );
}
function taskActions(task, dashboard = false) {
  return `<div class="row">${dashboard ? `<a data-nav href="tasks/${encodeURIComponent(task.id)}" class="dashsmall">任务详情 ↗</a>` : `<a data-nav href="tasks/${encodeURIComponent(task.id)}/dashboard" style="font-size:12px">${icon("chart")} 可视化大盘 ↗</a>`}${dashboard && ["pending", "running", "paused"].includes(task.status) ? '<button data-edit-timing>调整执行配置</button>' : ""}${task.status === "pending" ? '<button class="primary" data-task-action="start">开始任务</button>' : task.status === "running" ? '<button data-task-action="pause">暂停任务</button>' : ["paused", "interrupted"].includes(task.status) ? '<button class="primary" data-task-action="resume">恢复任务</button>' : ""}${task.status !== "running" ? '<button class="danger" data-delete-task>删除任务</button>' : ""}</div>`;
}
function bindTaskActions(task, reload, root = document, afterDelete = () => navigate("/tasks")) {
  bind("[data-edit-timing]", "click", () => editTaskDialog(task, reload), root);
  bind("[data-delete-task]", "click", () =>
    deleteTaskDialog(task, afterDelete), root,
  );
  onAll("[data-task-action]", "click", async (ev) => {
    const button = ev.currentTarget;
    const action = button.dataset.taskAction;
    button.disabled = true;
    try {
      await api(`/tasks/${task.id}/${action}`, "POST", {});
      toast(
        action === "start"
          ? "任务已开始"
          : action === "pause"
            ? "任务已暂停"
            : "任务已恢复",
      );
      await reload();
    } finally {
      button.disabled = false;
    }
  }, root);
}
async function editTaskDialog(task, reload) {
  const token = generation;
  const [accountData, currentTask, templateData] = await Promise.all([api("/accounts"), api(`/tasks/${task.id}`), api("/message-templates")]);
  if (token !== generation) return;
  task = currentTask;
  accounts = accountData.items;
  const timingOnly = task.status !== "pending";
  const localDate = task.scheduled_at
    ? new Date(
        Number(task.scheduled_at) * 1000 -
          new Date().getTimezoneOffset() * 60000,
      )
        .toISOString()
        .slice(0, 16)
    : "";
  showDialog(
    timingOnly ? "调整执行配置" : "编辑任务配置",
    `<form class="formgrid" id="edit-task">${executionFields(task)}${timingOnly ? "" : `<label class="wide">任务名称<input name="name" required value="${e(task.name)}"></label>`}${templatePicker(templateData.items)}<div class="wide" id="task-message-editor"></div>${taskLimitFields(task)}${replyCheckFields(task)}${timingOnly ? "" : `<label class="wide">预约时间（可选）<input name="scheduled_at" type="datetime-local" value="${e(localDate)}"></label>`}<p class="hint wide">保存不自动开始或恢复任务；账号与话术变更仅影响后续领取，正在发送的消息与历史正文保留。</p></form>`,
    `<button data-close>取消</button><button form="edit-task" type="submit" class="primary">保存配置</button>`,
  );
  bindExecutionFields();
  const messageEditor = bindTaskMessageEditor(dialog.querySelector("#task-message-editor"), {api, task, onError: toast});
  bindTemplatePicker(templateData.items, messageEditor);
  bind(
    "#edit-task",
    "submit",
    (ev) =>
      submit(ev, async () => {
        const values = {...Object.fromEntries(new FormData(ev.target)), ...executionValues(ev.target), ...messageEditor.values()};
        delete values.message;
        values.max_errors = Number(values.max_errors);
        values.max_consecutive_errors = Number(values.max_consecutive_errors);
        values.reply_check_interval_minutes = Number(values.reply_check_interval_minutes);
        if (!timingOnly)
          values.scheduled_at = values.scheduled_at
            ? new Date(values.scheduled_at).toISOString()
            : null;
        await api(`/tasks/${task.id}`, "PATCH", values);
        dialog.close();
        toast("配置已保存");
        await reload();
      }),
    dialog,
  );
}
async function taskPage(id) {
  shell(
    "tasks",
    "任务详情",
    `<div id="task-detail"></div><section class="panel" id="task-replies" aria-label="已回复用户"></section><section class="panel"><div class="paneltitle"><h2>接收者与结果</h2><select id="recipient-status" aria-label="筛选接收者结果" style="width:145px"><option value="">全部结果</option>${["pending", "sending", "sent", "failed", "skipped"].map((s) => `<option value="${s}">${labels[s]}</option>`).join("")}</select></div><div id="recipient-list"></div></section><section class="panel" id="task-followups" aria-label="后续发送记录"></section>`,
  );
  const token = generation;
  let page = 1,
    status = "",
    refreshDetails = true;
  const repliesRoot = document.querySelector("#task-replies");
  repliesRoot.innerHTML = taskRepliesPanel(null, date);
  const replies = createTaskReplies({
    taskId: id, api, isCurrent: () => token === generation,
    render: data => {
      repliesRoot.innerHTML = taskRepliesPanel(data, date);
      onAll("[data-replies-page]", "click", ev =>
        replies.setPage(Number(ev.currentTarget.dataset.repliesPage)), repliesRoot);
    },
  });
  const followupsRoot = document.querySelector("#task-followups");
  followupsRoot.innerHTML = taskFollowupsPanel(null, date);
  const followups = createTaskFollowups({
    taskId: id, api, isCurrent: () => token === generation,
    render: data => {
      followupsRoot.innerHTML = taskFollowupsPanel(data, date);
      onAll("[data-followups-page]", "click", ev =>
        followups.setPage(Number(ev.currentTarget.dataset.followupsPage)), followupsRoot);
    },
  });
  async function recipients() {
    const data = await api(
      `/tasks/${id}/recipients?${params({ page, status })}`,
    );
    if (token !== generation) return;
    document.querySelector("#recipient-list").innerHTML = data.items.length
      ? `<div class="tablewrap"><table><thead><tr><th>接收者</th><th>结果</th><th>发送账号</th><th>请求时间</th><th>确认时间</th><th>说明</th></tr></thead><tbody>${data.items.map((u) => `<tr><td>${person(u)}</td><td>${badge(u.status)}</td><td>${e(u.sender_account_name || "—")}</td><td>${date(u.attempted_at)}</td><td>${date(u.sent_at)}</td><td class="textwrap">${e(u.error || "")}${u.result_note ? `<div class="hint">${e(u.result_note)}</div>` : u.error ? "" : "—"}</td></tr>`).join("")}</tbody></table></div>${pagination(data)}`
      : empty("没有符合条件的接收者");
    bind("[data-prev]", "click", () => {
      page--;
      return recipients();
    });
    bind("[data-next]", "click", () => {
      page++;
      return recipients();
    });
  }
  async function load() {
    const t = await api(`/tasks/${id}`);
    if (token !== generation) return;
    const c = t.counts;
    document.querySelector("#task-detail").innerHTML =
      `${heading("TASK DETAIL", e(t.name), `任务 #${e(t.id)} · 创建于 ${date(t.created_at)}`, taskActions(t))}${t.error ? `<div class="errorbox">${e(t.error)}</div>` : ""}${t.status === "pending" ? '<div class="callout">任务已保存，尚未开始。确认账号、消息与接收者后，点击「开始任务」。</div>' : ""}<div class="stats"><div class="stat"><span>全部接收者</span><strong>${c.total}</strong></div><div class="stat"><span>发送成功</span><strong style="color:#39a482">${c.sent}</strong></div><div class="stat"><span>失败 / 跳过</span><strong>${c.failed} <span style="display:inline;font-size:18px">/ ${c.skipped}</span></strong></div><div class="stat"><span>待处理</span><strong>${remaining(c)}</strong></div></div><div class="detailgrid"><section class="panel"><div class="paneltitle"><h2>消息与配置</h2>${["pending", "running", "paused"].includes(t.status) ? `<button class="subtle small" id="edit-task-config">${t.status === "pending" ? "编辑配置" : "调整执行配置"}</button>` : ""}</div><div class="panelbody"><div class="row spread"><span class="hint">执行账号 · ${e(t.account_name)}</span>${badge(t.status)}</div><div class="message">${e(t.message)}</div>${t.first_messages?.length>1?`<p class="hint">首次发送：${t.first_messages.length} 条候选文案，逐联系人随机一条。</p>`:""}<p class="hint">发送成功不代表接收端送达或已读。</p></div></section><section class="panel"><div class="paneltitle"><h2>执行安排</h2></div><div class="panelbody"><dl class="detailrows"><dt>执行模式</dt><dd>${t.execution_mode === "auto" ? "自动分配账号" : "指定账号"}</dd><dt>发送节奏</dt><dd>按各账号配置执行 · <a href="accounts" data-nav>账号管理 ↗</a></dd><dt>允许累计失败</dt><dd>${t.max_errors ?? 1000} 次（当前 ${c.failed} 次）</dd><dt>允许连续失败</dt><dd>${t.max_consecutive_errors ?? 1000} 次（当前 ${t.consecutive_errors ?? 0} 次）</dd><dt>回复后跟进</dt><dd>独立排队，同组图文连续发送；不等待首发冷却或休息，暂停与工作时间仍生效</dd><dt>回复回查间隔</dt><dd>${t.reply_messages?.length===0 ? "未配置后续文字或图片，不回查回复" : `${t.reply_check_interval_minutes ? `每 ${e(t.reply_check_interval_minutes)} 分钟回查成功未回复用户` : "周期回查已关闭"} · 结束后补查一轮`}</dd><dt>下次回复回查</dt><dd>${date(t.reply_check_next_at)}</dd><dt>自动暂停条件</dt><dd>累计与连续失败均超过上限</dd><dt>预约时间</dt><dd>${date(t.scheduled_at)}</dd><dt>开始时间</dt><dd>${date(t.started_at)}</dd><dt>${t.execution_mode === "auto" ? "调度状态" : "最早可调度时间"}</dt><dd>${t.status === "running" ? t.execution_mode === "auto" ? e(t.wait_reason || "正在发送，后续按账号分配") : date(t.next_send_at) : "—"}</dd><dt>结束时间</dt><dd>${date(t.finished_at)}</dd></dl></div></section></div>`;
    document.querySelector("#task-detail").insertAdjacentHTML("beforeend", replyReviewPanel(t.reply_review, date, t));
    bindTaskActions(t, load);
    bind("#edit-task-config", "click", () => editTaskDialog(t, load));
    await Promise.all([recipients(), replies.refresh(), followups.refresh()]);
    refreshDetails = shouldRefreshTask(t);
  }
  bind("#recipient-status", "change", (ev) => {
    status = ev.target.value;
    page = 1;
    return recipients();
  });
  await load();
  if (token === generation) poll(() => refreshDetails ? load() : Promise.all([replies.refresh(), followups.refresh()]), 2500);
}
function tagChoices(tags, selected = []) {
  return tags.length
    ? `<div class="tag-choices">${tags.map((tag) => `<label class="tag-choice" data-tag-search="${e((tag.name + " " + (tag.description || "")).toLocaleLowerCase())}"><input type="checkbox" name="tag_ids" value="${e(tag.id)}" ${selected.includes(tag.id) ? "checked" : ""}><span><strong>${e(tag.name)}</strong>${tag.description ? `<small>${e(tag.description)}</small>` : ""}</span></label>`).join("")}</div>`
    : '<p class="hint">暂无标签，可先到标签管理中新增。</p>';
}
async function chooseTagsDialog(
  title,
  hint,
  selected = null,
  bulk = false,
  label = "保存标签",
) {
  const token = generation;
  const tags = (await api("/tags")).items;
  if (token !== generation) return null;
  if (selected === null)
    selected = tags.filter((tag) => tag.is_default).map((tag) => tag.id);
  showDialog(
    title,
    `<form id="choose-tags"><p class="hint">${e(hint)}</p>${bulk ? '<label>操作方式<select name="action"><option value="add">添加所选标签，保留原标签</option><option value="remove">移除所选标签，保留其他标签</option></select></label>' : ""}<label>查找标签<input id="find-tags" placeholder="搜索标签名称或描述"></label>${tagChoices(tags, selected)}<p class="hint">可多选。${bulk ? "仅修改选中的标签。" : "可以取消全部勾选。"}</p></form>`,
    `<button data-close>取消</button><button class="primary" type="submit" form="choose-tags">${e(label)}</button>`,
  );
  bind(
    "#find-tags",
    "input",
    (ev) => {
      const query = ev.target.value.trim().toLocaleLowerCase();
      dialog.querySelectorAll("[data-tag-search]").forEach((node) => {
        node.hidden = !node.dataset.tagSearch.includes(query);
      });
    },
    dialog,
  );
  return new Promise((resolve) => {
    dialog.addEventListener("close", () => resolve(null), { once: true });
    bind(
      "#choose-tags",
      "submit",
      (ev) => {
        ev.preventDefault();
        const values = new FormData(ev.target);
        const ids = values.getAll("tag_ids");
        if (bulk && !ids.length) throw new Error("请至少选择一个标签");
        resolve({
          tag_ids: ids,
          action: bulk ? values.get("action") : "replace",
        });
        dialog.close();
      },
      dialog,
    );
  });
}
async function libraryPage(kind) {
  const isTags = kind === "tags";
  const title = isTags ? "标签管理" : "话术管理";
  shell(
    kind,
    title,
    `${heading(isTags ? "AUDIENCE LABELS" : "MESSAGE LIBRARY", isTags ? "让每一类用户，更清晰" : "把好的表达，留待下次使用", isTags ? "用标签组织用户，再按标签筛选与创建任务。" : "维护常用话术，创建任务时选择并按需调整。", `<button class="primary" id="add-library">＋ ${isTags ? "新增标签" : "新增话术"}</button>`)}<section class="panel"><div class="panelbody"><label>${isTags ? "查找标签" : "查找话术"}<input id="library-query" placeholder="按${isTags ? "名称" : "标题"}或描述搜索"></label></div><div id="library-list"></div></section>`,
  );
  const token = generation;
  let items = [];
  function paint() {
    const query = document
      .querySelector("#library-query")
      .value.trim()
      .toLocaleLowerCase();
    const shown = items.filter((item) =>
      [item.name || item.title, item.description].some((v) =>
        String(v || "")
          .toLocaleLowerCase()
          .includes(query),
      ),
    );
    document.querySelector("#library-list").innerHTML = shown.length
      ? `<div class="tablewrap"><table><thead><tr><th>${isTags ? "标签名称" : "话术标题"}</th><th>描述</th><th>${isTags ? "用户数" : "话术内容"}</th><th>操作</th></tr></thead><tbody>${shown.map((item) => `<tr><td><strong>${e(item.name || item.title)}</strong>${item.is_default ? '<div class="hint">入库默认选中</div>' : ""}</td><td class="textwrap">${e(item.description || "—")}</td><td class="textwrap">${isTags ? metric(item.user_count) : `<div class="template-preview">${e(item.content)}</div>`}</td><td><button class="subtle small" data-library-edit="${e(item.id)}">编辑</button><button class="subtle small danger" data-library-delete="${e(item.id)}">删除</button></td></tr>`).join("")}</tbody></table></div>`
      : empty(
          isTags ? "暂无匹配标签" : "暂无匹配话术",
          "可调整搜索条件，或点击上方按钮新增。",
        );
    onAll("[data-library-edit]", "click", (ev) =>
      edit(
        items.find((item) => item.id === ev.currentTarget.dataset.libraryEdit),
      ),
    );
    onAll("[data-library-delete]", "click", (ev) => {
      const item = items.find(
        (item) => item.id === ev.currentTarget.dataset.libraryDelete,
      );
      return confirmAction(
        isTags ? "删除标签" : "删除话术",
        `<p>确定删除「${e(item.name || item.title)}」？</p><p class="hint">${isTags ? `将解除用户与该标签的关联，用户和任务保留。当前关联 ${metric(item.user_count)} 位用户。` : "已创建任务中的消息正文保留。"}</p>`,
        async () => {
          await api(`/${kind}/${encodeURIComponent(item.id)}`, "DELETE", {});
          toast("已删除");
          await load();
        },
        "确认删除",
      );
    });
  }
  async function load() {
    const result = await api(`/${kind}`);
    if (token !== generation) return;
    items = result.items;
    paint();
  }
  function edit(item = {}) {
    showDialog(
      item.id
        ? isTags
          ? "编辑标签"
          : "编辑话术"
        : isTags
          ? "新增标签"
          : "新增话术",
      `<form id="library-form" class="formgrid"><label class="wide">${isTags ? "标签名称" : "话术标题"}<input name="${isTags ? "name" : "title"}" required maxlength="${isTags ? 64 : 120}" value="${e(item.name || item.title || "")}" placeholder="${isTags ? "例如：高价值用户" : "例如：二手车合作邀约"}"></label><label class="wide">${isTags ? "标签描述" : "话术描述"}<textarea name="description" rows="3" maxlength="500" placeholder="说明用途，便于选择">${e(item.description || "")}</textarea></label>${isTags ? "" : `<label class="wide">话术内容<textarea name="content" required rows="7" maxlength="2000" placeholder="输入可复用的消息正文">${e(item.content || "")}</textarea><span class="hint">最多 2000 字，创建任务时仍可修改。</span></label>`}</form>`,
      '<button data-close>取消</button><button class="primary" type="submit" form="library-form">保存</button>',
    );
    bind(
      "#library-form",
      "submit",
      (ev) =>
        submit(ev, async () => {
          await api(
            `/${kind}${item.id ? "/" + encodeURIComponent(item.id) : ""}`,
            item.id ? "PATCH" : "POST",
            Object.fromEntries(new FormData(ev.target)),
          );
          dialog.close();
          toast("已保存");
          await load();
        }),
      dialog,
    );
  }
  bind("#add-library", "click", () => edit());
  bind("#library-query", "input", paint);
  await load();
}

const dispatchLabels = {available:"可分配", sending:"发送中", cooling:"首发冷却", resting:"首发休息", paused:"已暂停调度", off_hours:"非工作时间", unavailable:"等待鉴权"};
function accountDispatchStatus(a) {
  return `<div class="account-dispatch"><strong class="dispatch-state ${e(a.dispatch_state)}"><i class="dot" aria-hidden="true"></i>${dispatchLabels[a.dispatch_state] || "等待状态"}</strong>${["cooling", "resting", "off_hours"].includes(a.dispatch_state) ? `<span class="dispatch-countdown" data-account-countdown="${e(a.available_at)}" title="等待结束后参与任务调度">${sendCountdown(a.available_at)}</span>` : ""}</div>`;
}
function accountSendingStatus(a) {
  const p = a.send_policy, r = a.recent;
  const rate = r.error_rate == null ? "暂无发送结果" : `${(r.error_rate*100).toLocaleString("zh-CN", {maximumFractionDigits:1})}%`;
  return `<section class="account-sending"><div class="row spread">${accountDispatchStatus(a)}<span class="hint">${a.borrowed_task_ids.length ? `已借调 · ${a.borrowed_task_ids.length} 项指定任务` : "默认参与共享池"}</span></div><div class="account-send-metrics"><div><span>近30分钟首次触达</span><strong>${r.attempted}<small> 次</small></strong></div><div><span>首发错误率</span><strong class="${r.error_rate == null ? "metric-empty" : ""}">${rate}</strong></div><div><span>首发失败</span><strong>${r.failed}<small> 次</small></strong></div></div><p class="hint">间隔 ${p.interval_seconds} 秒 + 随机 0～${p.random_extra_seconds} 秒 · 每批 1～${p.max_batch_size} 条消息<br>仅管控任务首次触达，已回复对话不受首发冷却和休息限制<br>至少首发 ${p.min_attempts} 次且首发错误率 &gt; ${p.error_rate_percent}%，休息 ${p.rest_minutes} 分钟</p>${a.dispatch_state === "resting" ? `<p class="hint danger">${e(a.rest_reason)}<br>预计恢复：${date(a.rest_until)}</p>` : a.dispatch_state === "cooling" ? `<p class="hint">下次可首发：${date(a.next_send_at)}</p>` : ""}<div class="account-work-summary"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7v5l3 2"/></svg><div><span>工作时间${a.work_schedule ? " · 北京时间" : ""}</span><strong>${e(workScheduleSummary(a.work_schedule))}</strong></div><button class="small subtle" data-work-time="${e(a.id)}" aria-label="设置 ${e(a.name)} 的工作时间">设置</button></div><div class="row account-send-actions"><button class="small" data-sending-config="${e(a.id)}">发送配置</button><button class="small" data-account-send-records="${e(a.id)}">对话详情</button><button class="small subtle" data-toggle-sending="${e(a.id)}">${a.send_enabled ? "暂停账号" : "恢复账号"}</button></div></section>`;
}
function editAccountSending(a, reload) {
  const p = a.send_policy, sources = accounts.filter(source => source.id !== a.id);
  const fields = ["interval_seconds", "random_extra_seconds", "max_batch_size", "min_attempts", "error_rate_percent", "rest_minutes"];
  showDialog("发送配置 · "+e(a.name),
    `<form class="formgrid" id="account-sending-form"><div class="wide policy-import"><label>使用其他账号的发送配置<select id="policy-source" ${sources.length ? "" : "disabled"}><option value="">${sources.length ? "请选择来源账号" : "暂无其他账号可导入"}</option>${sources.map(source => `<option value="${e(source.id)}">${e(source.name)}${source.uid ? ` · UID ${e(source.uid)}` : ""}</option>`).join("")}</select></label><button type="button" id="import-policy" disabled>导入</button><p class="hint" id="policy-import-hint" aria-live="polite">导入会覆盖下方 6 项参数，点击「保存配置」后生效。</p></div><p class="wide hint">可分配账号默认参与共享池；指定任务进行中会借调账号，暂停或结束后归还。发送节奏跨任务累计，保存不会清空已有冷却或休息。</p><label>发送间隔（秒）<input type="number" name="interval_seconds" min="0" max="86400" required value="${p.interval_seconds}"></label><label>额外随机等待上限（秒）<input type="number" name="random_extra_seconds" min="0" max="86400" required value="${p.random_extra_seconds}"></label><label class="wide">每次随机发送消息条数上限 X<input type="number" name="max_batch_size" min="1" max="10000" required value="${p.max_batch_size}"><span class="hint">每批随机 1～上限条消息，逐条发送；每个账号同一时刻只发一条。</span></label><div class="wide form-section-title">近 30 分钟首发错误率保护</div><label>至少首发次数<input type="number" name="min_attempts" min="1" max="10000" required value="${p.min_attempts}"></label><label>错误率大于（%）<input type="number" name="error_rate_percent" min="0" max="100" required value="${p.error_rate_percent}"></label><label>触发后休息（分钟）<input type="number" name="rest_minutes" min="1" max="1440" required value="${p.rest_minutes}"></label><p class="hint wide">仅统计任务首次触达，排除回复后跟进及手动对话。首发失败后的再次尝试仍计入。次数包含已结束和正在发送的首发请求；首发错误率 = 首发失败 ÷（首发成功＋首发失败）；超时及响应缺失计失败，正在发送不参与比例。达到发送次数且错误率严格大于阈值时休息，到期恢复后重新累计判断样本。</p></form>`,
    '<button data-close>取消</button><button class="primary" form="account-sending-form" type="submit">保存配置</button>');
  const form = dialog.querySelector("#account-sending-form"), sourceSelect = form.querySelector("#policy-source");
  const importButton = form.querySelector("#import-policy"), saveButton = dialog.querySelector('[form="account-sending-form"]');
  let importing = false;
  bind("#policy-source", "change", () => { importButton.disabled = !sourceSelect.value; }, dialog);
  bind("#import-policy", "click", async () => {
    if (importing || !sourceSelect.value) return;
    importing = true;
    importButton.disabled = sourceSelect.disabled = saveButton.disabled = true;
    try {
      const data = await api("/accounts");
      if (!dialog.open || !dialog.contains(form)) return;
      const source = data.items.find(account => account.id === sourceSelect.value);
      if (!source) throw new Error("来源账号已不存在，请重新打开发送配置");
      for (const key of fields) form.elements.namedItem(key).value = source.send_policy[key];
      form.querySelector("#policy-import-hint").textContent = `已导入「${source.name}」的配置，可继续修改，保存后生效。`;
    } finally {
      importing = false;
      importButton.disabled = !sourceSelect.value;
      sourceSelect.disabled = saveButton.disabled = false;
    }
  }, dialog);
  bind("#account-sending-form", "submit", ev => submit(ev, async () => {
    if (importing) return;
    const form = new FormData(ev.target);
    const body = {};
    for (const key of fields) body[key] = Number(form.get(key));
    await api(`/accounts/${a.id}/sending`, "PATCH", body);
    dialog.close(); toast("账号发送配置已保存"); await reload();
  }), dialog);
}

async function accountsPage() {
  shell(
    "accounts",
    "账号管理",
    `${heading("CONNECTED ACCOUNTS", "管理你的执行身份", "搜索与发送凭证分别检查，每个账号独立使用。", '<button class="primary" id="add-account">＋ 添加账号</button>')}<div class="callout">1. 添加账号并显示二维码　 →　 2. 账号本人使用抖音 App 扫码确认　 →　 3. 自动识别账号并保存登录状态<br>已有账号需要重新登录时，直接点击「重新扫码登录」。${meta.demo ? "<br>当前为演示模式。账号、二维码确认和发送均为本地模拟，不联系真实用户。" : ""}</div><div id="account-bulk" class="account-bulk"></div><div id="accounts-grid"></div>`,
  );
  const token = generation;
  let qrAccount = null, version = 0, bulkBusy = false;
  const selected = new Set();
  function paintBulk() {
    const all = accounts.length > 0 && accounts.every(a => selected.has(a.id));
    document.querySelector("#account-bulk").classList.toggle("has-selection",selected.size > 0);
    document.querySelector("#account-bulk").innerHTML = `<div class="account-bulk-selection"><label class="check-label"><input type="checkbox" id="select-all-accounts" ${all ? "checked" : ""} ${bulkBusy || !accounts.length ? "disabled" : ""}>全选</label><span class="account-selection-count">${selected.size ? `已选 <strong>${selected.size}</strong> 个账号` : `共 ${accounts.length} 个账号<span class="bulk-select-hint"> · 勾选后可批量操作</span>`}</span></div><div class="row"><button class="${selected.size ? "primary" : ""}" id="batch-edit-accounts" ${bulkBusy || !selected.size ? "disabled" : ""}>批量编辑</button><button class="subtle" data-batch-sending="false" ${bulkBusy || !selected.size ? "disabled" : ""}>暂停调度</button><button class="subtle" data-batch-sending="true" ${bulkBusy || !selected.size ? "disabled" : ""}>恢复调度</button></div>`;
    const allBox = document.querySelector("#select-all-accounts");
    allBox.indeterminate = selected.size > 0 && !all;
    bind("#select-all-accounts", "change", ev => {
      selected.clear(); if(ev.target.checked)accounts.forEach(a => selected.add(a.id)); paintBulk();
    });
    bind("#batch-edit-accounts", "click", () => editAccountsBatch({selected:accounts.filter(a => selected.has(a.id)),dialog,showDialog,api,reload:load,toast}));
    onAll("[data-batch-sending]", "click", async ev => {
      if(bulkBusy || !selected.size)return;
      const ids = [...selected], enabled = ev.currentTarget.dataset.batchSending === "true";
      bulkBusy = true; paintBulk();
      try {
        const result = await api("/accounts/batch/sending", "PATCH", {account_ids:ids,changes:{send_enabled:enabled}});
        toast(`已${enabled ? "恢复" : "暂停"} ${result.updated} 个账号调度`);await load();
      } finally {bulkBusy = false;if(token === generation)paintBulk();}
    });
    document.querySelectorAll("[data-account-select]").forEach(box => {
      box.checked = selected.has(box.dataset.accountSelect);box.disabled = bulkBusy;
      box.closest(".accountcard").classList.toggle("is-selected",box.checked);
    });
  }
  async function load() {
    const request = ++version;
    const data = await api("/accounts");
    if (token !== generation || request !== version) return;
    accounts = data.items;
    for(const id of selected)if(!accounts.some(a => a.id === id))selected.delete(id);
    document.querySelector("#accounts-grid").innerHTML = accounts.length
      ? `<div class="accountgrid">${accounts.map((a) => `<article class="accountcard"><div class="row spread"><div class="person"><input type="checkbox" class="account-select" data-account-select="${e(a.id)}" aria-label="选择账号 ${e(a.name)}"><span class="avatar">${e((a.name || "?").slice(0, 1))}</span><div><h2>${e(a.name)}</h2><span class="hint">UID ${e(a.uid || "扫码后自动识别")}</span></div></div>${badge(a.status)}</div><div class="accountstate"><div><span>登录 / 搜索能力</span><span class="badge ${a.can_search ? "sent" : "pending"}">${a.can_search ? "可搜索" : "待校验"}</span></div><div><span>扫码 / 发送凭证</span><span class="badge ${a.can_send ? "sent" : "waiting"}">${a.can_send ? "凭证就绪" : a.qr_status === "confirmed" ? "发送待检查" : "需要扫码"}</span></div><div><span>最近检查</span><span class="hint">${date(a.checked_at)}</span></div></div>${a.error ? `<div class="hint danger">${e(a.error)}</div>` : ""}<div class="accountbottom"><button class="small" data-qr="${e(a.id)}" ${a.status === "checking" ? "disabled" : ""}>${a.uid ? "重新扫码登录" : "扫码登录"}</button><button class="small subtle" data-check="${e(a.id)}" ${a.uid ? "" : "disabled"}>检查状态</button><button class="small subtle danger" data-delete-account="${e(a.id)}">删除</button></div>${accountSendingStatus(a)}</article>`).join("")}</div>`
      : `<section class="panel">${empty("添加第一个执行账号", "直接扫码登录，工作台会自动识别账号并保存会话。")}</section>`;
    paintBulk();
    onAll("[data-account-select]", "change", ev => {
      const id = ev.currentTarget.dataset.accountSelect;
      if(ev.currentTarget.checked)selected.add(id);else selected.delete(id);
      paintBulk();
    });
    onAll("[data-work-time]", "click", ev => editWorkTime({account:accounts.find(a => a.id === ev.currentTarget.dataset.workTime),dialog,showDialog,api,reload:load,toast}));
    onAll("[data-sending-config]", "click", ev => editAccountSending(accounts.find(a => a.id === ev.currentTarget.dataset.sendingConfig), load));
    onAll("[data-account-send-records]", "click", ev => navigate(`/send-details?account_id=${encodeURIComponent(ev.currentTarget.dataset.accountSendRecords)}`));
    onAll("[data-toggle-sending]", "click", async ev => {
      const a = accounts.find(a => a.id === ev.currentTarget.dataset.toggleSending);
      await api(`/accounts/${a.id}/sending`, "PATCH", {send_enabled:!a.send_enabled});
      toast(a.send_enabled ? "已暂停账号，当前请求结束后停止领取" : "已恢复账号，继续遵守冷却和休息");
      await load();
    });
    onAll("[data-qr]", "click", (ev) => openQr(ev.currentTarget.dataset.qr));
    onAll("[data-check]", "click", async (ev) => {
      await api(
        `/accounts/${ev.currentTarget.dataset.check}/check`,
        "POST",
        {},
      );
      toast("已开始检查账号状态");
      await load();
    });
    onAll("[data-delete-account]", "click", (ev) => {
      const id = ev.currentTarget.dataset.deleteAccount;
      return confirmAction(
        "删除账号",
        "<p>删除此账号与本地保存的凭证。存在未完成任务时无法删除。</p>",
        async () => {
          await api(`/accounts/${id}`, "DELETE", {});
          toast("账号已删除");
          await load();
        },
        "确认删除",
      );
    });
    if (qrAccount && dialog.open && dialog.querySelector("#qr-content")) {
      const a = accounts.find((item) => String(item.id) === String(qrAccount));
      if (a) {
        const qr = dialog.querySelector("#qr-content");
        const status = JSON.stringify([
          a.qr_status,
          a.qr_url,
          a.can_send,
          a.error,
        ]);
        if (qr.dataset.state !== status) {
          qr.dataset.state = status;
          qr.innerHTML = `<div style="text-align:center"><p>${a.qr_status === "confirmed" ? `账号已绑定（UID ${e(a.uid)}），登录状态已保存。` : a.uid ? `请使用已绑定的抖音账号「${e(a.name)}」扫码（UID ${e(a.uid)}），确保身份一致。` : `请账号本人使用抖音 App 扫码确认，登录后自动绑定到「${e(a.name)}」。`}</p>${a.qr_status === "verifying" ? `<div class="qrplaceholder">平台要求二次验证<br><a class="primary" target="_blank" rel="noopener" href="static/verification.html?account=${encodeURIComponent(a.id)}">打开官方验证</a><p class="hint">完成后将继续本次登录，无需重复扫码。</p></div>` : a.qr_status === "confirmed" ? `<div class="qrplaceholder" style="color:#3da884">✓ 登录已完成<br>${a.can_send ? "凭证已保存，可用于搜索和发送" : "会话已保存，发送状态待检查"}</div>` : a.qr_url ? `<img class="qrcode" alt="账号授权二维码" src="${APP_PREFIX}/api/accounts/${encodeURIComponent(a.id)}/qr-image">` : `<div class="qrplaceholder">${a.qr_status === "failed" ? "扫码登录失败，请重新发起" : a.qr_status === "interrupted" ? "扫码已中断，请重新发起" : meta.demo ? "演示二维码<br>请点击下方模拟扫码确认" : "正在获取二维码…"}</div>`}${badge(a.qr_status)}${a.error ? `<p class="danger">${e(a.error)}</p>` : ""}</div>`;
          const simulate = dialog.querySelector("#simulate-qr");
          if (simulate) simulate.hidden = a.qr_status !== "waiting";
        }
      }
    }
  }
  async function openQr(id) {
    qrAccount = id;
    await api(`/accounts/${qrAccount}/qr`, "POST", {});
    showDialog(
      "扫码登录",
      `<div id="qr-content"></div>`,
      `<button data-close>关闭</button>${meta.demo ? '<button class="primary" id="simulate-qr">模拟扫码确认</button>' : ""}`,
    );
    bind(
      "#simulate-qr",
      "click",
      async (ev) => {
        ev.currentTarget.disabled = true;
        try {
          await api(`/accounts/${qrAccount}/qr`, "POST", {});
          await load();
        } finally {
          ev.target.disabled = false;
        }
      },
      dialog,
    );
    await load();
  }
  bind("#add-account", "click", () => {
    showDialog(
      "添加执行账号",
      `<form id="account-form" class="formgrid"><label class="wide">账号名称<input name="name" required maxlength="80" placeholder="例如：品牌合作账号"></label><label class="wide">添加方式<select id="account-login-mode"><option value="qr">直接扫码登录（推荐）</option><option value="cookie">导入 Cookie</option></select></label><label class="wide" id="account-cookie-field" hidden>账号 Cookie<textarea name="cookie" disabled rows="5" maxlength="50000" autocomplete="off" spellcheck="false" placeholder="粘贴账号 Cookie"></textarea></label><p class="hint wide" id="account-login-hint">无需提前登录网页或获取 Cookie。下一步显示二维码，由账号本人扫码确认；登录状态会加密保存，后续自动复用。</p></form>`,
      `<button data-close>取消</button><button class="primary" form="account-form" type="submit">下一步：扫码登录</button>`,
    );
    const form = dialog.querySelector("#account-form");
    bind("#account-login-mode", "change", event => {
      const useCookie = event.target.value === "cookie";
      dialog.querySelector("#account-cookie-field").hidden = !useCookie;
      form.elements.cookie.disabled = !useCookie;
      form.elements.cookie.required = useCookie;
      dialog.querySelector('[form="account-form"][type="submit"]').textContent = useCookie ? "保存并校验" : "下一步：扫码登录";
      dialog.querySelector("#account-login-hint").textContent = useCookie ? "Cookie 导入为可选方式；需要发送时可能仍需扫码补全凭证。" : "无需提前登录网页或获取 Cookie。下一步显示二维码，由账号本人扫码确认；登录状态会加密保存，后续自动复用。";
    }, dialog);
    bind("#account-form", "submit", ev => submit(ev, async () => {
      const scan = dialog.querySelector("#account-login-mode").value === "qr";
      const account = await api("/accounts", "POST", formValues(ev.target));
      form.reset();
      dialog.close();
      toast("账号已添加");
      await load();
      if (scan && token === generation) await openQr(account.id);
    }), dialog);
  });
  await load();
  poll(load, 1800);
  poll(() => {
    document.querySelectorAll("[data-account-countdown]").forEach(node => {
      node.textContent = sendCountdown(node.dataset.accountCountdown ? Number(node.dataset.accountCountdown) : null);
    });
  }, 1000);
}
function trendChart(trend) {
  if (!trend.length)
    return '<div class="chartempty">等待第一条真实执行记录</div>';
  return lineChart(trend.map(p => ({...p, label: date(p.minute), tick: trendTime(p.minute)})), [
    {key: "sent", label: "确认", color: "#44b5ff"},
    {key: "failed", label: "失败", color: "#d77d94"},
    {key: "attempted", label: "请求", color: "#8298b8"},
  ], "task-trend", "每分钟请求、发送成功与失败数量趋势");
}

function trendTime(value) {
  if (typeof value === "number") return clock(value).slice(0, 5);
  return String(value || "").slice(-5);
}
async function dashboardPage(id) {
  document.body.className = "dashboard";
  app.innerHTML = `<main class="dash"><header class="dashheader"><a class="dashbrand" href="tasks" data-nav><span class="brandmark">♪</span><b>抖音工作台</b><span>/ EXECUTION LIVE</span></a><div class="row">${mode()}<span class="connection" id="connection"><i class="dot"></i>连接中</span></div></header><div id="dash-content"></div><footer class="dashfooter"><span>LOCAL WORKSPACE / 任务由后台执行，关闭页面不会停止任务</span><span>发送成功 ≠ 实际送达 · 无已读数据</span></footer></main>`;
  const token = generation;
  let latest = null,
    receivedAt = 0,
    events = [],
    lastEvent = 0,
    trendKey = "";
  async function load() {
    try {
      const d = await api(`/tasks/${id}/dashboard?after_event_id=${lastEvent}`);
      if (token !== generation) return;
      const hasNewEvents = d.events.length > 0;
      events = [...events, ...d.events];
      if (d.events.length) lastEvent = d.events.at(-1).id;
      d.events = events;
      latest = d;
      receivedAt = Date.now();
      const t = d.task,
        c = t.counts;
      const processed = c.sent + c.failed + c.skipped,
        percent = c.total ? Math.round((processed / c.total) * 100) : 0;
      const circumference = 2 * Math.PI * 80;
      const oldTrend = document.querySelector('[data-chart-id="task-trend"]');
      const previousTrend = oldTrend ? {label: oldTrend.dataset.activeLabel,
        focused: oldTrend.contains(document.activeElement)} : null;
      const nextTrendKey = JSON.stringify(d.trend);
      document.querySelector("#connection").className = "connection";
      document.querySelector("#connection").innerHTML =
        '<i class="dot"></i>实时同步';
      const eventList = document.querySelector(".events");
      const eventScrollTop = eventList?.scrollTop || 0;
      const eventScrollHeight = eventList?.scrollHeight || 0;
      const pageScroll = { left: window.scrollX, top: window.scrollY };
      document.querySelector("#dash-content").innerHTML =
        `<div class="dashheading"><div><div class="eyebrow">MISSION CONTROL · TASK ${e(t.id)}</div><h1>${e(t.name)}</h1><p class="subtitle">执行账号 ${e(t.account_name)} <span style="margin:0 10px;color:#375071">/</span> ${t.execution_mode === "auto" ? "自动分配账号" : "指定账号"} · 发送节奏按账号配置 · 累计允许失败 ${t.max_errors ?? 1000} 次、连续允许 ${t.max_consecutive_errors ?? 1000} 次 · 两项都超过才暂停（当前连续 ${t.consecutive_errors ?? 0} 次）</p></div>${taskActions(t, true)}</div>${t.error ? `<div class="errorbox">${e(t.error)}</div>` : ""}<div class="dashgrid"><section class="dashpanel mission"><div class="missiontop"><span class="dashlabel">整体执行进度 / PROGRESS</span>${badge(t.status)}</div><div class="missionmain"><div class="orbit"><svg viewBox="0 0 180 180" aria-label="已处理 ${percent}%"><defs><linearGradient id="ring-gradient"><stop stop-color="#2372ee"/><stop offset="1" stop-color="#65e2ff"/></linearGradient></defs><circle cx="90" cy="90" r="80"/><circle class="arc" cx="90" cy="90" r="80" stroke-dasharray="${(circumference * percent) / 100} ${circumference}"/><circle cx="90" cy="90" r="68" style="stroke-width:1;stroke-dasharray:2 8"/></svg><div class="orbittext"><strong>${percent}<small style="font-size:18px">%</small></strong><span>已处理目标</span></div></div><div class="missionnumbers"><div class="dashlabel" style="margin-bottom:12px">发送成功 / 全部接收者</div><div class="big">${c.sent}<small>/ ${c.total}</small></div><p>${t.status === "completed" ? "所有目标已处理，请查看各项结果" : t.status === "pending" ? "尚未开始，等待你确认执行" : t.status === "paused" ? "任务已暂停，等待恢复" : t.status === "interrupted" ? "执行已中断，请核对状态" : t.phase === "sending" ? "正在向平台提交消息请求" : (t.wait_reason || "后台按计划等待下一次执行")}</p><div class="missionlegend"><span><i class="dot" style="color:#51c7b4"></i> 确认<b>${c.sent}</b></span><span><i class="dot" style="color:#e796a2"></i> 失败<b>${c.failed}</b></span><span><i class="dot" style="color:#b2a0e3"></i> 跳过<b>${c.skipped}</b></span></div></div></div><div class="mission-next"><div><span>${t.execution_mode === "auto" ? "共享池调度" : "最早可调度时间"}</span><small>${t.status === "running" && t.phase === "sending" ? "当前请求完成后按账号安排" : t.status === "running" && t.next_send_at ? date(t.next_send_at) : t.status === "paused" ? "恢复任务后继续" : (t.wait_reason || "暂无执行计划")}</small></div><strong class="countdown" id="countdown">—</strong></div></section><section class="dashpanel targetpanel"><div class="row spread"><span class="dashlabel">当前接收者 / RECIPIENT</span><span class="dashsmall">${d.current?.status === "sending" ? "请求进行中" : d.current ? "下一位接收者" : "等待下一次请求"}</span></div>${d.current_recipients?.length ? `<div class="parallel-targets">${d.current_recipients.map(u => `<div><span class="dashsmall">${e(u.account_name)}</span>${person(u)}</div>`).join("")}</div>` : d.current ? person(d.current) : `<div class="person"><span class="avatar">—</span><div><strong>${t.status === "completed" ? "本次任务已结束" : "当前没有发送中的用户"}</strong><small>${t.status === "pending" ? "开始后展示实际接收者" : "仅在请求执行时显示当前目标"}</small></div></div>`}<div class="message">${e(t.message)}</div></section></div><div class="dashstats"><div class="dashstat success"><span><i class="dot"></i> 发送成功</span><strong>${c.sent}</strong></div><div class="dashstat failed"><span><i class="dot"></i> 执行失败</span><strong>${c.failed}</strong></div><div class="dashstat skipped"><span><i class="dot"></i> 已跳过</span><strong>${c.skipped}</strong></div><div class="dashstat pending"><span><i class="dot"></i> 剩余目标</span><strong>${remaining(c)}</strong></div></div><div class="dashgrid"><section class="dashpanel chartpanel"><div class="chartheader"><h2>实际发送节奏</h2><span>按分钟 · <b style="color:#44b5ff;font-weight:400">确认</b> / <b style="color:#d77d94;font-weight:400">失败</b> / 请求</span></div>${trendChart(d.trend || [])}</section><section class="dashpanel eventpanel"><div class="chartheader"><h2>执行事件流</h2><span>最新 ${d.events.length} 条记录</span></div><div class="events">${
          d.events.length
            ? [...d.events]
                .reverse()
                .map(
                  (event) =>
                    `<div class="event"><time>${clock(event.time)}</time><i class="dot"></i><p>${e(event.detail || event.type)}${event.uid ? `<br><span class="dashsmall">UID ${e(event.uid)}</span>` : ""}</p></div>`,
                )
                .join("")
            : '<div class="chartempty">任务开始后显示真实事件</div>'
        }</div></section></div>`;
      if (eventList) {
        const updatedEvents = document.querySelector(".events");
        if (hasNewEvents) eventList.innerHTML = updatedEvents.innerHTML;
        updatedEvents.replaceWith(eventList);
        // Newest events are prepended; keep the reader on the same older row.
        eventList.scrollTop = eventScrollTop > 0
          ? eventScrollTop + eventList.scrollHeight - eventScrollHeight
          : 0;
      }
      const newTrend = document.querySelector('[data-chart-id="task-trend"]');
      if (oldTrend && newTrend && trendKey === nextTrendKey) {
        newTrend.replaceWith(oldTrend);
        if (previousTrend.focused) {
          const selected = [...oldTrend.querySelectorAll(".chart-hit")].find(node => node.dataset.label === previousTrend.label);
          selected?.focus({preventScroll: true});
        }
      } else if (newTrend) bindLineChart(newTrend, previousTrend);
      trendKey = nextTrendKey;
      bindTaskActions(t, load);
      countdown();
      if (eventList) window.scrollTo({ ...pageScroll, behavior: "instant" });
    } catch (err) {
      if (token !== generation) return;
      document.querySelector("#connection").className = "connection offline";
      document.querySelector("#connection").innerHTML =
        '<i class="dot"></i>同步中断，正在重连';
      if (!latest)
        document.querySelector("#dash-content").innerHTML =
          `<div class="errorbox" style="margin-top:30px">${e(err.message)}</div>`;
    }
  }
  function countdown() {
    if (!latest || token !== generation) return;
    const node = document.querySelector("#countdown");
    if (!node) return;
    const task = latest.task;
    if (task.status === "running" && task.phase === "sending") {
      node.classList.add("is-text");
      node.textContent = "正在发送";
      return;
    }
    if (task.status !== "running" || !task.next_send_at) {
      node.classList.add("is-text");
      node.textContent = task.status === "paused" ? "已暂停" : task.status === "running"
        ? (task.scheduled_at || 0) > latest.now ? "等待预约" : task.execution_mode === "auto" ? "等待分配" : "等待账号" : "—";
      return;
    }
    const now = latest.now + (Date.now() - receivedAt) / 1000;
    const seconds = Math.max(0, Math.ceil(task.next_send_at - now));
    node.classList.toggle("is-text", !seconds);
    node.textContent = seconds
      ? `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`
      : "等待后台执行";
  }
  await load();
  poll(load, 1400);
  poll(countdown, 500);
}
function historyCurve(points, key, color, id, description) {
  return lineChart(points.map(p => ({...p, label: p.date, tick: p.date.slice(5)})), [
    {key, label: key === "sent_people" ? "发送人数" : "完成任务", color,
      unit: key === "sent_people" ? " 人" : " 项"},
  ], id, description);
}
function businessCodeChart(data) {
  if (!data) return '<div class="account-error-empty">业务码统计待服务更新</div>';
  if (!data.total) return '<div class="account-error-empty">所选时间内暂无已结束的发送记录</div>';
  const maxCount = Math.max(...data.items.map(row => row.count));
  return `<div class="code-distribution">${data.items.map(row => {
    const label = row.code == null ? "未记录业务码" : `业务码 ${row.code}`;
    const share = (row.count / data.total * 100).toLocaleString("zh-CN", {maximumFractionDigits:1});
    return `<div class="code-row"><strong>${label}</strong><div class="code-track" role="img" aria-label="${label}：${row.count} 条，成功 ${row.sent} 条，失败 ${row.failed} 条，占比 ${share}%" title="成功 ${row.sent} 条 · 失败 ${row.failed} 条"><i class="code-sent" style="width:${row.sent/maxCount*100}%"></i><i class="code-failed" style="width:${row.failed/maxCount*100}%"></i></div><span><b>${metric(row.count)}</b> 条 <small>${share}%</small></span></div>`;
  }).join("")}</div>`;
}
function taskTimeline(data) {
  if (!data.items.length)
    return '<div class="chartempty">这个时间范围内暂无任务记录</div>';
  const position = (time) =>
    Math.max(
      0,
      Math.min(100, (100 * (time - data.start)) / (data.end - data.start)),
    );
  const stamp = (time) =>
    new Date(time * 1000).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "Asia/Shanghai",
    });
  return `<div class="timeline-scroll"><div class="timeline-grid"><div class="timeline-axis-label">任务 / 执行账号</div><div class="timeline-axis">${[0, 1, 2, 3, 4].map((n) => `<span style="left:${n * 25}%">${stamp(data.start + ((data.end - data.start) * n) / 4)}</span>`).join("")}</div>${data.items.map((t) => `<div class="timeline-name">${t.deleted ? `<strong>${e(t.name)}</strong>` : `<a href="tasks/${encodeURIComponent(t.id)}" data-nav>${e(t.name)}</a>`}<small>${e(t.account_name)} · ${t.deleted ? "已删除" : labels[t.status] || e(t.status)}</small></div><div class="timeline-track"><span class="timeline-bar ${e(t.status)}" style="left:${position(t.began)}%;width:${Math.max(0.3, position(t.ended) - position(t.began))}%" title="${e(t.name)} · ${date(t.began)} → ${date(t.ended)}">${t.status === "running" ? "<i></i>" : ""}</span></div>`).join("")}</div></div><div class="timeline-pagination"><span>共 ${data.total} 项历史记录 · 第 ${data.page} / ${Math.max(1, Math.ceil(data.total / data.page_size))} 页</span><div class="row"><button class="small" data-timeline-prev ${data.page <= 1 ? "disabled" : ""}>上一页</button><button class="small" data-timeline-next ${data.page * data.page_size >= data.total ? "disabled" : ""}>下一页</button></div></div>`;
}
async function overviewPage() {
  document.body.className = "dashboard";
  app.innerHTML = `<main class="dash overview"><header class="dashheader"><a class="dashbrand" href="search" data-nav><span class="brandmark">♪</span><b>抖音工作台</b><span>/ WORKSPACE LIVE</span></a><div class="row"><a href="tasks" data-nav>任务管理 ↗</a>${mode()}<span class="connection" id="overview-connection"><i class="dot"></i>连接中</span></div></header><div class="dashheading"><div><div class="eyebrow">WORKSPACE OVERVIEW</div><h1>每一次执行，尽在眼前。</h1><p class="subtitle">实时任务、历史触达与执行时间轴</p></div><div class="range-switch" role="group" aria-label="历史统计范围"><button data-days="7" aria-pressed="true">近 7 天</button><button data-days="30" aria-pressed="false">近 30 天</button></div></div><div id="overview-summary"></div><section class="dashpanel overview-live"><div class="chartheader"><h2><i class="dot"></i> 此刻正在运行</h2><span id="overview-live-count">—</span></div><div id="overview-running"></div></section><section class="dashpanel overview-errors"><div class="chartheader"><h2>账号发送概览</h2><span>实时首发 · 累计发送</span></div><div id="overview-account-errors"></div><p class="chart-note">首发错误率 = 首发失败 ÷（首发成功 + 首发失败）。按发起时间统计该账号跨任务的首次触达，不含回复后跟进及手动对话；4002、8101、21003、31003 及满足成功条件但未返回业务码的响应计成功；其他非零业务码、超时及不完整响应计失败。累计发送包含全部本系统任务及手动发送；展开走势跟随页面近 7／30 天选择，累计数不受影响。</p></section><section class="dashpanel overview-codes"><div class="chartheader"><h2>发送业务码分布</h2><span id="overview-code-total"></span></div><div class="code-legend"><span><i class="code-sent"></i>成功</span><span><i class="code-failed"></i>失败</span></div><div id="overview-business-codes"></div><p class="chart-note">按所选时间内的发起时间统计成功、失败记录，包含已删除任务。每条发送取最新业务码，缺失单列；颜色按已记录的发送结果区分，4002、8101 保留原码。缺少发起时间的旧记录未纳入。</p></section><div class="overview-charts"><section class="dashpanel chartpanel"><div class="chartheader"><h2>历史发送人数</h2><span id="people-range-total"></span></div><div id="overview-people-curve"></div><p class="chart-note">每日按 UID 去重；跨日重复用户会在各日出现。仅统计发送成功。</p></section><section class="dashpanel chartpanel"><div class="chartheader"><h2>历史完成任务</h2><span id="tasks-range-total"></span></div><div id="overview-tasks-curve"></div><p class="chart-note">按实际完成日期统计；任务完成可包含失败和未执行的跳过记录。</p></section></div><section class="dashpanel overview-timeline"><div class="chartheader"><h2>任务执行时间轴</h2><span>按所选范围内的实际记录缩放 · 上海时间</span></div><div class="timeline-legend"><span class="running">进行中</span><span class="completed">已完成</span><span class="paused">已暂停</span><span class="pending">未开始</span><span class="deleted">已删除</span></div><div id="overview-timeline"></div><p class="chart-note">跨度从激活（未开始则为创建）到结束或当前，包含预约等待、发送间隔与暂停；颜色表示当前状态。未开始任务显示为时间点。</p></section><footer class="dashfooter"><span>累计数据包含已删除任务的历史记录 · 每 2 秒同步</span><span>发送成功 ≠ 实际送达 · 无已读数据</span></footer></main>`;
  const token = generation;
  const accountOverview = createAccountOverview(document.querySelector("#overview-account-errors"), accountDispatchStatus);
  let days = 7,
    timelinePage = 1,
    receivedAt = 0,
    serverNow = 0,
    requestVersion = 0,
    chartKey = "";
  function countdown() {
    if (token !== generation) return;
    const now = serverNow + (Date.now() - receivedAt) / 1000;
    document.querySelectorAll("[data-account-countdown]").forEach(node => {
      node.textContent = sendCountdown(node.dataset.accountCountdown ? Number(node.dataset.accountCountdown) : null, now);
    });
    document.querySelectorAll("[data-overview-countdown]").forEach((node) => {
      const seconds = Math.max(
        0,
        Math.ceil(Number(node.dataset.overviewCountdown) - now),
      );
      node.textContent = seconds
        ? `${Math.floor(seconds / 60)}分 ${String(seconds % 60).padStart(2, "0")}秒`
        : "等待调度";
    });
  }
  async function load() {
    const version = ++requestVersion;
    try {
      const [d, accountHistory] = await Promise.all([
        api(`/overview?days=${days}&timeline_page=${timelinePage}`),
        api(`/overview/accounts?range=${days}&scope=all`).catch(() => null),
      ]);
      if (token !== generation || version !== requestVersion) return;
      receivedAt = Date.now();
      serverNow = d.now;
      const s = d.summary;
      document.querySelector("#overview-connection").className = "connection";
      document.querySelector("#overview-connection").innerHTML =
        '<i class="dot"></i>实时同步';
      document.querySelector("#overview-summary").innerHTML =
        `<div class="overview-stats">${[
          [
            "正在运行",
            s.running_tasks,
            `${s.pending_tasks} 未开始 · ${s.paused_tasks} 已暂停`,
            "cyan",
          ],
          [
            "累计完成任务",
            s.completed_tasks,
            `累计创建 ${s.total_tasks} 项任务`,
            "purple",
          ],
          [
            "累计发送人数",
            s.sent_people,
            "按 UID 去重 · 发送成功",
            "green",
          ],
          [
            "累计确认消息",
            s.sent_messages,
            `${s.failed_messages} 失败`,
            "blue",
          ],
        ]
          .map(
            ([label, value, note, color]) =>
              `<article class="overview-stat ${color}"><span>${label}</span><strong>${metric(value)}</strong><small>${note}</small></article>`,
          )
          .join("")}</div>`;
      document.querySelector("#overview-live-count").textContent =
        `${d.running.length} 项任务 · 不同账号独立执行`;
      document.querySelector("#overview-running").innerHTML = d.running.length
        ? `<div class="live-grid">${d.running
            .map((t) => {
              const c = t.counts,
                processed = c.total - remaining(c),
                percent = c.total ? Math.round((processed / c.total) * 100) : 0;
              const activity = {
                sending: "正在发送",
                scheduled: "等待预约时间",
                waiting: "等待发送间隔",
                queued: "等待可用账号",
              }[t.activity];
              return `<article class="live-task ${t.activity === "sending" ? "is-sending" : ""}"><div class="row spread"><span class="live-account">${e(t.account_name)}</span><span class="live-activity"><i class="dot"></i>${activity}</span></div><a class="live-title" href="tasks/${encodeURIComponent(t.id)}/dashboard" data-nav>${e(t.name)} ↗</a><div class="live-target"><span>${t.activity === "sending" ? "当前接收者" : "下一位接收者"}</span><strong>${t.current_recipients?.length ? t.current_recipients.map(u => `${e(u.account_name)} → ${e(u.nickname || u.uid)}`).join("<br>") : e(t.current?.nickname || "正在结束任务")}</strong><small>${e(t.wait_reason || t.current?.douyinhao || t.current?.uid || "")}</small></div><p class="live-message" title="${e(t.message)}">${e(t.message)}</p><div class="live-progress"><i style="width:${percent}%"></i></div><div class="row spread live-meta"><span>已处理 ${processed} / ${c.total} · 成功 ${c.sent} · 失败 ${c.failed}</span>${t.activity === "sending" ? "<strong>请求进行中</strong>" : t.execution_mode === "auto" ? `<strong>${t.activity === "scheduled" ? "等待预约" : "等待账号分配"}</strong>` : t.next_send_at ? `<strong data-overview-countdown="${t.next_send_at}"></strong>` : "<strong>等待可用账号</strong>"}</div></article>`;
            })
            .join("")}</div>`
        : '<div class="overview-idle"><span>◉</span><strong>当前没有运行中的任务</strong><p>任务开始后，这里会展示每个账号的执行进度。</p><a href="tasks" data-nav>前往任务管理 →</a></div>';
      accountOverview.update(d.account_errors, accountHistory, days);
      document.querySelector("#overview-business-codes").innerHTML = businessCodeChart(d.business_codes);
      document.querySelector("#overview-code-total").textContent = d.business_codes
        ? `近 ${d.days} 天 · ${metric(d.business_codes.total)} 条发送记录` : "";
      const nextKey = JSON.stringify(d.daily);
      if (nextKey !== chartKey) {
        const selections = new Map([...document.querySelectorAll("#overview-people-curve .interactive-chart, #overview-tasks-curve .interactive-chart")].map(root => [
          root.dataset.chartId, {label: root.dataset.activeLabel, focused: root.contains(document.activeElement)},
        ]));
        document.querySelector("#overview-people-curve").innerHTML =
          historyCurve(
            d.daily,
            "sent_people",
            "#50d6c0",
            "people-fill",
            "每日发送成功人数曲线",
          );
        document.querySelector("#overview-tasks-curve").innerHTML =
          historyCurve(
            d.daily,
            "completed_tasks",
            "#a39afb",
            "tasks-fill",
            "每日完成任务数量曲线",
          );
        document.querySelectorAll("#overview-people-curve .interactive-chart, #overview-tasks-curve .interactive-chart").forEach(root => bindLineChart(root, selections.get(root.dataset.chartId)));
        chartKey = nextKey;
      }
      document.querySelector("#people-range-total").textContent =
        `近 ${d.days} 天去重 ${d.range_totals.sent_people} 人`;
      document.querySelector("#tasks-range-total").textContent =
        `近 ${d.days} 天完成 ${d.range_totals.completed_tasks} 项`;
      document.querySelector("#overview-timeline").innerHTML = taskTimeline(
        d.timeline,
      );
      bind("[data-timeline-prev]", "click", () => {
        timelinePage--;
        return load();
      });
      bind("[data-timeline-next]", "click", () => {
        timelinePage++;
        return load();
      });
      countdown();
    } catch (error) {
      if (token !== generation || version !== requestVersion) return;
      document.querySelector("#overview-connection").className =
        "connection offline";
      document.querySelector("#overview-connection").innerHTML =
        '<i class="dot"></i>同步中断，正在重连';
      if (!receivedAt)
        document.querySelector("#overview-summary").innerHTML =
          `<div class="errorbox">${e(error.message)}</div>`;
    }
  }
  onAll("[data-days]", "click", (ev) => {
    days = Number(ev.currentTarget.dataset.days);
    timelinePage = 1;
    document
      .querySelectorAll("[data-days]")
      .forEach((node) =>
        node.setAttribute(
          "aria-pressed",
          String(Number(node.dataset.days) === days),
        ),
      );
    return load();
  });
  await load();
  poll(load, 2000);
  poll(countdown, 500);
}

async function render() {
  generation++;
  timers.forEach(clearTimeout);
  timers.clear();
  dialog.close();
  const token = generation;
  try {
    accounts = (await api("/accounts")).items;
    if (token !== generation) return;
    const path = location.pathname.slice(APP_PREFIX.length) || "/";
    const dash = path.match(/^\/tasks\/([^/]+)\/dashboard$/);
    const task = path.match(/^\/tasks\/([^/]+)$/);
    if (dash)
      return await dashboardPage(
        encodeURIComponent(decodeURIComponent(dash[1])),
      );
    if (task)
      return await taskPage(encodeURIComponent(decodeURIComponent(task[1])));
    if (path === "/overview") return await overviewPage();
    if (path === "/users") return await usersPage();
    if (path === "/tasks") return await tasksPage();
    if (path === "/send-details") return await sendDetailsPage();
    if (path === "/accounts") return await accountsPage();
    if (path === "/tags") return await libraryPage("tags");
    if (path === "/message-templates")
      return await libraryPage("message-templates");
    await searchPage();
  } catch (err) {
    if (token === generation)
      shell(
        "search",
        "加载失败",
        `<div class="errorbox">${e(err.message)}</div><button id="retry">重新加载</button>`,
      );
    bind("#retry", "click", render);
  }
}
try {
  meta = await api("/meta");
  await render();
} catch (err) {
  shell(
    "search",
    "连接失败",
    `<div class="errorbox">${e(err.message)}</div><button id="retry-connection">重新连接</button>`,
  );
  bind("#retry-connection", "click", () => location.reload());
}
