export function escapeHTML(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
}
export function metric(value) {
  return value == null ? "未知" : Number(value).toLocaleString("zh-CN");
}
export function remaining(counts) {
  return (
    (counts.pending || 0) + (counts.sending || 0)
  );
}
export function sendCountdown(availableAt, now = Date.now() / 1000) {
  if (!Number.isFinite(availableAt)) return "等待时间更新";
  const seconds = Math.max(0, Math.ceil(availableAt - now));
  return seconds ? `倒计时发送：${seconds}秒` : "等待调度";
}
export function sendNumberLabel(row) {
  if (row.direction === 'incoming') return '—';
  return Number.isInteger(row.send_number) && row.send_number > 0
    ? `第 ${row.send_number} 次发送` : '发送次序未确认';
}
