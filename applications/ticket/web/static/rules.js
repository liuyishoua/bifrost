const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const beijingTime = value => value ? new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}) : '尚未取得';
export function saleLabel(task) {
  if (!task.config.reservation) return task.config.start_at ? '手动开始时间' : '立即执行';
  const kind=task.sale_evidence?.kind;
  if(kind==='manual_start')return '手动抢票开始时间';
  return kind==='train_announcement'?'车次公告起售时间':kind==='on_sale'?'已开售':'起售时间未确认';
}
export function saleDisplay(task) {
  if (task.config.reservation && !['manual_start','on_sale','train_announcement'].includes(task.sale_evidence?.kind)) return '待核验';
  return task.sale_evidence?.kind==='on_sale'?'按任务状态执行':beijingTime(task.sale_at);
}
export function choicesText(config) {
  return config.train_options?.length ? config.train_options.map(o=>`${o.train}${o.from_station?`（${o.from_station} → ${o.to_station}）`:''}：${o.seat_types.join(' → ')}`).join('；') : `${config.trains.join(' → ')} · ${config.seat_types.join(' → ')}（各车次共用偏好）`;
}
export function nextAction(task, account) {
  if(account&&!account.logged_in&&['reserved','waiting','starting','querying'].includes(task.status))return '账号未登录，任务配置已保留。请到账号管理恢复登录后继续。';
  const messages={draft:'还没有开始抢票，请点击“启动任务”。',reserved:'已安排自动抢票。保持电脑和服务运行，等待结果即可。',starting:'正在准备抢票，请保持账号登录。',waiting:'正在等待开始时间或账号下单机会，无需重复启动。',querying:'正在找符合条件的票，有票后会自动尝试下单。',submitting:'正在提交订单，请等待结果。',queueing:'正在等待12306出票结果，请勿重复下单。',success:'已取得订单，请立即到12306核对并按官方期限支付。',review:'订单结果尚不明确。先到12306核对订单，处理后在账号管理中解除限制。',unconfirmed:'暂未取得起售时间，本任务已停止。请核对12306后重新配置。',failed:'本次抢票已停止。查看下方失败原因，处理后重新配置。',stopped:task.order_attempted||task.order_id?'任务已停止，请先核对官方订单。':'任务已暂停，点击“继续抢票”即可按原配置恢复。',exhausted:'本次未抢到票，已结束。可以调整车次后重新配置。',interrupted:'服务中断，请先核对12306订单，再决定是否重新配置。'};
  return messages[task.status]||'请查看当前任务状态。';
}
export function startText(task) {
  const sales=Object.entries(task.confirmed_sales||{});
  if(sales.length)return sales.map(([train,s])=>`${train}：${s.kind==='on_sale'?'已开售':beijingTime(s.sale_at)+' 开抢'}`).join('；');
  if(task.sale_evidence?.kind==='manual_start'&&task.sale_at)return task.sale_at+' 开抢';
  if(task.sale_evidence?.kind==='on_sale')return '已开售';
  if(task.sale_evidence?.kind==='train_announcement'&&task.sale_at)return beijingTime(task.sale_at)+' 开抢';
  if(task.config.start_at)return task.config.start_at+' 开抢';
  return task.config.reservation?'起售时间确认后自动开抢':'启动后立即抢票';
}
export function rulesHtml(task) {
  const c=task.config;
  return `<section class="rule-section"><h3>怎么替你抢</h3><ul>
  <li>只抢你选的车次和席别，一次满足全部乘车人数。多个备选只需买成其中一趟。</li>
  <li>各车次开售后集中抢一分钟，有票立即尝试下单。明确余票不足就换其他候选；暂时没抢到，之后每 ${esc(c.interval??60)} 秒继续找余票。</li>
  <li>多个候选同时有票时，${c.priority_strategy==='seat_first'?'按席别优先，再按车次顺序':'按车次优先，再按该车次的席别顺序'}。同账号一次只下一单。</li>
  <li>座位、铺位按偏好尝试；不能满足时由系统分配。</li></ul></section>
  <section class="rule-section"><h3>什么时候停止</h3><p>任一备选出票成功，本任务就停止。订单结果不明时暂停提交，等你核对。达到 ${esc(c.max_retries??1000)} 次查询${c.stop_at?`或 ${esc(c.stop_at)}`:''} 仍未成功也会结束；你可以随时手动停止。</p></section>
  <section class="rule-section"><h3>你需要做什么</h3><p>保存后还要点击“启动任务”。抢票期间保持账号登录、电脑不休眠、服务运行，网页可以关闭。</p><p>显示“待支付”后，到12306核对并支付。这里不会自动付款；停止或删除任务也不会取消官方订单。</p><p>如果任务停止并提示异常，按任务详情中的提示处理；已预约不代表已经买到票。</p></section>`;
}

export function fareEstimate(config, rows) {
  const values=[];let missing=0, sleeper=false;
  for(const row of rows){
    const selected=config.train_options?.find(o=>o.train===row.train)?.seat_types||config.seat_types||[];
    for(const seat of selected){const value=row.prices?.[seat];if(Number.isFinite(value)&&value>0){values.push(value);sleeper ||= seat.includes('卧');}else missing++;}
  }
  if(!values.length)return {label:'票价暂未获取',missing:true};
  const min=Math.min(...values), max=Math.max(...values), range=(a,b)=>a===b?`¥${a}`:`¥${a}–${b}`;
  const n=config.passenger_ids?.length||config.passenger_names?.length||0;
  return {label:`约 ${range(min,max)} / 人`,total:n>1?`${n} 人约 ${range(Number((min*n).toFixed(2)),Number((max*n).toFixed(2)))}`:'',missing:missing>0,sleeper};
}
export function journeyHtml(task) {
  const c=task.config, rows=task.timetable||c.train_options||c.trains.map(train=>({train}));
  const total=fareEstimate(c,rows);
  return `<div class="task-journeys">${rows.map(row=>{
    const valid=/^\d{2}:\d{2}$/.test(row.departure||'')&&/^\d{2}:\d{2}$/.test(row.arrival||'');
    const minutes=value=>value.split(':').reduce((a,v)=>a*60+Number(v),0);
    const days=valid&&/^\d{2,3}:\d{2}$/.test(row.duration||'')?Math.floor((minutes(row.departure)+minutes(row.duration))/1440):null;
    const dateAt=offset=>{const d=new Date(`${c.train_date}T00:00:00Z`);if(!Number.isFinite(d.getTime()))return '';d.setUTCDate(d.getUTCDate()+offset);return `${d.getUTCFullYear()}年${String(d.getUTCMonth()+1).padStart(2,'0')}月${String(d.getUTCDate()).padStart(2,'0')}日`;};
    const selected=c.train_options?.find(o=>o.train===row.train)?.seat_types||c.seat_types||[];
    const fare=fareEstimate(c,[row]);
    const duration=row.duration?`${Number(row.duration.split(':')[0])}小时${Number(row.duration.split(':')[1])}分`:'';
    return `<article class="task-journey"><div class="journey-heading"><strong>${esc(row.train)}</strong><span class="journey-fare ${fare.missing?'partial':''}">${esc(fare.label)}</span></div>${valid?`<div class="journey-route"><div class="journey-end"><span class="journey-date">${esc(dateAt(0))}</span><b class="journey-clock">${esc(row.departure)}</b><span class="journey-station">${esc(row.from_station||c.from_station||'出发')}</span></div><div class="journey-duration"><span>${esc(duration)}</span><span class="journey-arrow">→</span>${days>0?`<small>${days===1?'次日':`第 ${days+1} 天`}到达</small>`:''}</div><div class="journey-end arrival"><span class="journey-date">${days===null?'到达':esc(dateAt(days))}</span><b class="journey-clock">${esc(row.arrival)}</b><span class="journey-station">${esc(row.to_station||c.to_station||'到达')}</span></div></div>`:'<p class="help">暂无时刻</p>'}<div class="journey-seats">${esc(selected.join(' → ')||'请选择席别')}</div></article>`;
  }).join('')}<div class="journey-budget"><strong>预计票价：${esc(total.label)}</strong>${total.total?`<span>${esc(total.total)}</span>`:''}<small>${total.missing?'部分或全部席别暂未返回票价。':''}${total.sleeper?'卧铺按起价估算。':''}最终金额以12306订单为准。</small></div></div>`;
}
