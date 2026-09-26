import { escapeHTML as e, metric } from "./utils.mjs";

const states = {
  pending: ["pending", "等待回查"],
  running: ["running", "回查中"],
  completed: ["completed", "回查完成"],
  partial: ["failed", "部分回查未完成"],
};

export function shouldRefreshTask(task) {
  return task.status !== "completed" ||
    ["pending", "running"].includes(task.reply_review?.status);
}

export function replyReviewPanel(review, date, task = null) {
  if (task?.reply_messages?.length===0) return '<section class="panel"><div class="paneltitle"><h2>回复回查</h2></div><div class="panelbody hint">未配置后续文字或图片，不进行周期回查或结束补查。已保存的回复仍可查看。</div></section>';
  if (!review) {
    return '<section class="panel"><div class="paneltitle"><h2>回复回查</h2></div><div class="panelbody hint">尚未触发回查。周期回查按任务配置执行，发送结束后补查一轮；旧的已完成任务不补查。<a href="#task-replies">查看已回复用户</a></div></section>';
  }
  const [style, label] = states[review.status] || ["pending", "状态待更新"];
  const periodic = review.kind === "periodic";
  const c = review.counts;
  const counts = [["已核查", c.completed], ["待查", c.pending + c.running],
    ["回查未完成", c.failed], [periodic ? "本轮发现回复" : "已发现回复", c.replied]];
  return `<section class="panel" aria-label="回复回查"><div class="paneltitle"><h2>回复回查</h2><span class="badge ${style}"><i class="dot"></i>${label}</span></div><div class="panelbody"><dl class="detailrows"><dt>${periodic ? "本轮回查时间" : "发送结束锚点"}</dt><dd>${e(date(review.anchor_at))}</dd><dt>回查结束时间</dt><dd>${e(date(review.finished_at))}</dd>${periodic ? `<dt>下次回查时间</dt><dd>${e(date(review.next_check_at))}</dd>` : ""}</dl><p class="hint">本轮共 ${e(metric(c.total))} 个账号与联系人组合，同一联系人由不同账号发送时分别核查。</p><div class="stats">${counts.map(([name, count]) => `<${name.endsWith("发现回复") ? 'a href="#task-replies"' : "div"} class="stat"><span>${name}</span><strong>${e(metric(count))}</strong></${name.endsWith("发现回复") ? "a" : "div"}>`).join("")}</div>${review.errors.length ? `<details><summary>查看未完成原因（${e(metric(c.failed))}，最多展示 20 条）</summary><ul class="hint">${review.errors.map(item => `<li>${e(item.account_name)} · UID ${e(item.uid)}：${e(item.error)}</li>`).join("")}</ul></details>` : ""}<p class="hint">${periodic ? "周期回查只查成功未回复的用户，已回复用户退出后续周期回查。回查失败不等于未回复。" : "最终回查补查全部成功发送的用户。回查失败不等于未回复。结果仅覆盖各会话实际查询时平台可获取的记录，不覆盖本轮回查之后的新回复。"}</p></div></section>`;
}

export function taskRepliesPanel(data, date) {
  const heading = '<div class="paneltitle"><h2>已回复用户</h2></div><p class="hint panelbody">本任务成功联系过的用户及其已保存回复；同一用户由不同账号联系时分别展示。查看列表不会查询平台。</p>';
  if (!data) return `${heading}<div class="panelbody hint">正在加载回复…</div>`;
  if (data.error) return `${heading}<div class="panelbody errorbox">${e(data.error)}</div>`;
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  const pager = `<div class="pager"><span>共 ${e(data.total)} 个账号与联系人组合 / ${e(pages)} 页</span><div class="row"><button class="small" data-replies-page="${e(data.page - 1)}" ${data.page <= 1 ? "disabled" : ""}>上一页</button><span>第 ${e(data.page)} 页</span><button class="small" data-replies-page="${e(data.page + 1)}" ${data.page >= pages ? "disabled" : ""}>下一页</button></div></div>`;
  const body = data.items.length
    ? `<div class="tablewrap"><table><thead><tr><th>联系人</th><th>发送账号</th><th>最近回复</th><th>回复时间</th><th>回复条数</th><th>操作</th></tr></thead><tbody>${data.items.map(item => {
      const query = new URLSearchParams({category: "incoming", uid: item.uid, account_id: item.account_id});
      return `<tr><td><strong>${e(item.nickname || item.uid)}</strong><div class="hint">UID ${e(item.uid)}${item.douyinhao ? ` · 抖音号 ${e(item.douyinhao)}` : ""}${item.user_deleted ? " · 用户资料已删除" : ""}</div></td><td>${e(item.account_name || item.account_id)}</td><td class="textwrap" style="white-space:pre-wrap">${e(item.last_reply_text || "[非文本消息]")}</td><td>${e(date(item.last_reply_at))}</td><td>${e(metric(item.reply_count))}</td><td><a data-nav href="/send-details?${e(query.toString())}">查看全部回复</a></td></tr>`;
    }).join("")}</tbody></table></div>`
    : '<div class="panelbody hint">本地尚无符合条件的回复记录，不代表用户未回复。</div>';
  return heading + body + pager;
}

export function createTaskReplies({taskId, api, render, isCurrent}) {
  let page = 1, requestVersion = 0;
  async function refresh() {
    const version = ++requestVersion;
    try {
      const data = await api(`/tasks/${encodeURIComponent(taskId)}/replies?page=${page}&page_size=20`);
      if (isCurrent() && version === requestVersion) render(data);
    } catch (error) {
      if (isCurrent() && version === requestVersion) render({error: error.message});
    }
  }
  return {refresh, setPage(value) { page = value; return refresh(); }};
}

export function replyCheckFields(task = null) {
  const minutes = task == null ? 30 : task.reply_check_interval_minutes ?? 0;
  return `<label>回复回查间隔（分钟）<input name="reply_check_interval_minutes" type="number" min="0" max="10080" step="1" value="${e(minutes)}" required><span class="hint">仅配置后续文字或图片时生效；0 关闭周期回查但任务结束仍回查一轮。后续消息为空时，两种回查均不执行。</span></label>`;
}
