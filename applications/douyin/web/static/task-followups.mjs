import { escapeHTML as e, sendNumberLabel } from "./utils.mjs";
import { localImageURL } from "./message-editor.mjs";

export function taskFollowupsPanel(data, date) {
  const heading = '<div class="paneltitle"><h2>后续发送记录</h2></div><p class="hint panelbody">本任务首次成功联系后，同一账号对该用户的后续发送，包括本任务、其他任务、手动对话及已同步的会话记录。仅展示本地已保存记录。</p>';
  if (!data) return `${heading}<div class="panelbody hint">正在加载后续发送记录…</div>`;
  if (data.error) return `${heading}<div class="panelbody errorbox">${e(data.error)}</div>`;
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  const pager = `<div class="pager"><span>共 ${e(data.total)} 条记录 / ${e(pages)} 页</span><div class="row"><button class="small" data-followups-page="${e(data.page - 1)}" ${data.page <= 1 ? "disabled" : ""}>上一页</button><span>第 ${e(data.page)} 页</span><button class="small" data-followups-page="${e(data.page + 1)}" ${data.page >= pages ? "disabled" : ""}>下一页</button></div></div>`;
  const body = data.items.length
    ? `<div class="tablewrap"><table><thead><tr><th>联系人</th><th>发送账号 / 来源</th><th>发送次序</th><th>发送内容</th><th>发送时间</th><th>结果 / 业务码</th><th>操作</th></tr></thead><tbody>${data.items.map(item => {
      const query = new URLSearchParams({category: "outgoing", uid: item.uid, account_id: item.account_id});
      const imageURL = item.message_kind === "image" ? localImageURL(item.image_url) : "";
      const source = item.task_name || (item.task_id ? "任务记录" : "会话记录");
      return `<tr><td><strong>${e(item.nickname || item.uid)}</strong><div class="hint">UID ${e(item.uid)}</div></td><td>${e(item.account_name || item.account_id)}<div class="hint">${item.task_id && !item.task_deleted ? `<a data-nav href="/tasks/${e(encodeURIComponent(item.task_id))}">${e(source)}</a>` : e(source)}${item.task_deleted ? " · 任务已删除" : ""}</div></td><td>${e(sendNumberLabel(item))}</td><td class="textwrap"><div class="template-preview" style="min-width:180px;max-width:300px">${imageURL ? `<img class="message-image-preview" src="${e(imageURL)}" alt="后续发送图片" loading="lazy">` : e(item.message ?? (item.message_kind === "image" ? "[图片]" : "未记录正文"))}</div></td><td>${e(date(item.attempted_at))}</td><td class="textwrap"><span class="badge ${item.status === "sent" ? "sent" : "failed"}"><i class="dot"></i>${item.status === "sent" ? "成功" : "失败"}</span><div class="hint">业务码 ${e(item.business_code ?? "未记录")}</div>${item.error ? `<div>${e(item.error)}</div>` : ""}${item.result_note ? `<div class="hint">${e(item.result_note)}</div>` : ""}</td><td><a data-nav href="/send-details?${e(query.toString())}">对话详情</a></td></tr>`;
    }).join("")}</tbody></table></div>`
    : '<div class="panelbody hint">本地暂无后续发送记录。</div>';
  return heading + body + pager;
}

export function createTaskFollowups({taskId, api, render, isCurrent}) {
  let page = 1, requestVersion = 0;
  async function refresh() {
    const version = ++requestVersion;
    try {
      const data = await api(`/tasks/${encodeURIComponent(taskId)}/followups?page=${page}&page_size=20`);
      if (isCurrent() && version === requestVersion) render(data);
    } catch (error) {
      if (isCurrent() && version === requestVersion) render({error: error.message});
    }
  }
  return {refresh, setPage(value) { page = value; return refresh(); }};
}
