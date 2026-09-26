import { escapeHTML as e, metric } from './utils.mjs';
import { bindLineChart } from './charts.mjs';

const percent = rate => rate == null ? '—' : `${(rate*100).toLocaleString('zh-CN',{maximumFractionDigits:1})}%`;

export function errorRateChart(buckets, series) {
  if (!series.some(p => p.error_rate != null)) return '<div class="chartempty">暂无发送结果</div>';
  const left = 40, right = 625;
  const top = 20, bottom = 175;
  const x = i => left + i*(right-left)/Math.max(1,series.length-1);
  const y = rate => bottom-rate*(bottom-top);
  const groups = [];
  let group = [];
  series.forEach((point,i) => {
    if (point.error_rate == null) { if (group.length) groups.push(group); group = []; }
    else group.push(`${x(i)},${y(point.error_rate)}`);
  });
  if (group.length) groups.push(group);
  const paths = groups.map(points => `<polyline points="${points.join(' ')}" fill="none" stroke="#f0a0ae" stroke-width="2.5" stroke-linejoin="round"/>`).join('');
  const dots = series.map((p,i) => p.error_rate == null ? '' : `<circle cx="${x(i)}" cy="${y(p.error_rate)}" r="3" fill="#f0a0ae"/>`).join('');
  return `<div class="interactive-chart" data-chart-id="account-performance-trend"><svg class="history-curve chart" viewBox="0 0 650 210" role="group" aria-label="账号错误率趋势，纵轴0至100%">${[0,.5,1].map(rate => `<line x1="${left}" x2="${right}" y1="${y(rate)}" y2="${y(rate)}"/><text x="2" y="${y(rate)+4}">${rate*100}%</text>`).join('')}${paths}${dots}<g class="line-marker" visibility="hidden"><line y1="${top}" y2="${bottom}"/><circle r="5" fill="#f0a0ae" stroke="#ecf8ff" stroke-width="2"/></g>${series.map((p,i) => {
    if (p.error_rate == null) return '';
    const begin = i ? (x(i-1)+x(i))/2 : left;
    const end = i<series.length-1 ? (x(i)+x(i+1))/2 : right;
    const tooltip = `${buckets[i].label}\n成功：${metric(p.sent)}\n失败：${metric(p.failed)}\n错误率：${percent(p.error_rate)}`;
    return `<rect class="chart-hit" tabindex="0" x="${begin}" y="${top}" width="${end-begin}" height="${bottom-top}" fill="transparent" data-x="${x(i)}" data-ys="${e(JSON.stringify([y(p.error_rate)]))}" data-label="${e(buckets[i].label)}" data-tooltip="${e(tooltip)}" aria-label="${e(tooltip)}"/>`;
  }).join('')}${[...new Set([0,Math.floor((buckets.length-1)/2),buckets.length-1])].map(i => `<text x="${x(i)}" y="202" text-anchor="${i===0 ? 'start' : i===buckets.length-1 ? 'end' : 'middle'}">${e(buckets[i].tick)}</text>`).join('')}</svg><div class="line-tooltip" hidden></div></div>`;
}

export function lifetimeDetailURL(data, row, status) {
  const filters = {...data.lifetime_detail_filters, status};
  if (row.account_id) filters.account_id = row.account_id;
  return '/send-details?'+new URLSearchParams(filters);
}

export function renderAccountOverview(recent, history, state = {}, dispatchStatus = () => '') {
  const active = recent?.items || [];
  const available = Boolean(history?.summary?.lifetime);
  const others = available ? history.items.filter(row=>!active.some(account=>account.account_id===row.account_id)) : [];
  const totals = active.reduce((sum,row)=>({sent:sum.sent+row.sent,failed:sum.failed+row.failed}),{sent:0,failed:0});
  const days = state.days || 7;
  function cumulative(row, summary = false) {
    if (!row?.lifetime) return '<p class="account-cumulative-unavailable">累计数据暂不可用</p>';
    const count = row.lifetime, key = summary ? 'all' : row.account_id;
    return `<div class="account-cumulative"><span class="account-cumulative-label">${summary ? '全部账号累计' : '累计发送'}</span><a class="account-cumulative-sent" href="${e(lifetimeDetailURL(history,row,'sent'))}" data-nav aria-label="${e(row.account_name)}累计成功 ${count.sent} 条，查看明细">成功 <b>${metric(count.sent)}</b></a><a class="account-cumulative-failed" href="${e(lifetimeDetailURL(history,row,'failed'))}" data-nav aria-label="${e(row.account_name)}累计失败 ${count.failed} 条，查看明细">失败 <b>${metric(count.failed)}</b></a><span>错误率 <b>${percent(count.error_rate)}</b></span><button class="account-trend-toggle" data-account-trend="${e(key)}" aria-expanded="${state.openTrend===key}" aria-label="${e(row.account_name)}${state.openTrend===key ? '收起' : '查看'}走势">${state.openTrend===key ? '收起走势' : summary ? '查看整体走势' : '查看走势'} <span aria-hidden="true">${state.openTrend===key ? '⌃' : '⌄'}</span></button></div>`;
  }
  function trend(row, key) {
    if (!row || state.openTrend!==key) return '';
    return `<div class="account-inline-trend"><div class="account-inline-trend-heading"><strong>${e(row.account_name)} · 近 ${days} 天错误率</strong><span>本系统全部发送 · 按天</span></div>${errorRateChart(history.buckets,row.series)}<p class="chart-note">鼠标悬停或点选查看当天成功、失败数。无结果时段留空。${row.lifetime.undated ? `累计包含 ${row.lifetime.undated} 条缺时间的旧记录，未计入走势。` : ''}</p></div>`;
  }
  function accountRow(account, running) {
    const historyRow = available ? history.items.find(row=>row.account_id===account.account_id) : null;
    const tasks = account.tasks || [];
    const rate = account.error_rate == null ? null : account.error_rate*100;
    const label = rate == null ? '暂无发送结果' : percent(account.error_rate);
    return `<article class="account-error-row"><div class="account-error-name"><strong>${e(account.account_name)}</strong>${running ? tasks.map(t=>`<a href="/tasks/${encodeURIComponent(t.id)}/dashboard" data-nav>${e(t.name)} ↗</a>`).join('')+dispatchStatus(account) : '<small>未关联运行任务</small>'}</div>${running ? `<div class="account-error-measure"><div class="row spread"><strong>${label}</strong><span>近30分钟首发 · ${account.attempted ?? account.sent+account.failed} 次发送</span></div><div class="account-error-track ${rate==null ? 'no-sample' : ''}" role="img" aria-label="${e(account.account_name)} 首发错误率 ${label}"><i style="width:${rate || 0}%"></i></div></div><div class="account-error-counts"><span>确认 <b>${account.sent}</b></span><span>失败 <b>${account.failed}</b></span></div>` : ''}${cumulative(historyRow)}${trend(historyRow,account.account_id)}</article>`;
  }
  return `<div class="account-summary"><div class="account-recent-summary"><span>近30分钟首发 · 运行账号</span><div class="account-error-counts account-error-summary"><span>整体确认 <b>${totals.sent}</b></span><span>整体失败 <b>${totals.failed}</b></span></div></div>${cumulative(available ? history.summary : null,true)}${available ? trend(history.summary,'all') : ''}</div><div class="account-error-list">${active.length ? active.map(row=>accountRow(row,true)).join('') : '<div class="account-error-empty">当前没有运行中的账号</div>'}${state.showOther ? others.map(row=>accountRow(row,false)).join('') : ''}</div>${others.length ? `<button class="account-other-toggle" data-other-accounts aria-expanded="${Boolean(state.showOther)}">${state.showOther ? '收起其他账号' : '展开其他账号'} · ${others.length} <span aria-hidden="true">${state.showOther ? '⌃' : '⌄'}</span></button>` : ''}`;
}

export function createAccountOverview(root, dispatchStatus) {
  let recent = null, history = null;
  const state = {openTrend:null,showOther:false,days:7};
  function render() {
    const chart = root.querySelector('.interactive-chart');
    const previous = chart ? {label:chart.dataset.activeLabel,focused:chart.contains(document.activeElement)} : null;
    const focused = root.contains(document.activeElement) ? document.activeElement : null;
    const focusTrend = focused?.dataset.accountTrend;
    const focusOther = focused?.hasAttribute('data-other-accounts');
    const focusLink = focused?.getAttribute('href');
    root.innerHTML = renderAccountOverview(recent,history,state,dispatchStatus);
    const nextChart = root.querySelector('.interactive-chart');
    if (nextChart) bindLineChart(nextChart,previous);
    if (focusTrend !== undefined) [...root.querySelectorAll('[data-account-trend]')].find(node=>node.dataset.accountTrend===focusTrend)?.focus({preventScroll:true});
    else if (focusOther) root.querySelector('[data-other-accounts]')?.focus({preventScroll:true});
    else if (focusLink) [...root.querySelectorAll('a[href]')].find(node=>node.getAttribute('href')===focusLink)?.focus({preventScroll:true});
  }
  root.addEventListener('click',event=>{
    const toggle = event.target.closest('[data-account-trend]');
    if (toggle) {state.openTrend=state.openTrend===toggle.dataset.accountTrend ? null : toggle.dataset.accountTrend;render();}
    if (event.target.closest('[data-other-accounts]')) {
      state.showOther=!state.showOther;
      if (!state.showOther && state.openTrend!=='all' && !recent?.items.some(row=>row.account_id===state.openTrend)) state.openTrend=null;
      render();
    }
  });
  return {update(nextRecent,nextHistory,days){
    recent=nextRecent;history=nextHistory;state.days=days;
    if (history?.items.some(row=>row.account_id===state.openTrend) &&
        !recent?.items.some(row=>row.account_id===state.openTrend)) state.showOther=true;
    render();
  }};
}
