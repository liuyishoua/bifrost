import {saleLabel, nextAction, startText, rulesHtml, choicesText, beijingTime, saleDisplay, journeyHtml} from './rules.js';
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const seats = ['商务座','特等座','一等座','二等座','高级软卧','一等卧','二等卧','软卧','硬卧','软座','硬座','无座'];
const active = ['reserved','starting','waiting','querying','submitting','queueing','stopping'];
const statuses = {reserved:'已预约 · 等待开售',draft:'待启动',starting:'准备中',waiting:'等待开始',querying:'查询中',submitting:'提交中',queueing:'排队中',stopping:'正在停止',stopped:'已暂停',success:'待支付',found:'已结束（旧任务）',unconfirmed:'起售时间未确认',exhausted:'抢票结束',failed:'执行失败',review:'需核对订单',interrupted:'服务中断'};
const state = {accounts:[], tasks:[], page:'query', trains:[], route:null, type:'all', taskFilter:'all', dialog:null, loginTimer:null, selected:[]};
let polling = false;
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').classList.add('show'); clearTimeout(toastTimer); toastTimer = setTimeout(()=>$('#toast').classList.remove('show'),5000); }
async function api(path, data, signal) {
  const response = await fetch(`api/${path}`, data === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Workbench-Token':$('meta[name=workbench-token]').content},body:JSON.stringify(data),...(signal?{signal}:{})});
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '操作失败');
  return result;
}
function options(items, selected='', placeholder='') { return (placeholder ? `<option value="">${esc(placeholder)}</option>`:'') + items.map(([value,label])=>`<option value="${esc(value)}" ${value===selected?'selected':''}>${esc(label)}</option>`).join(''); }
function accountName(id) { return state.accounts.find(a=>a.id===id)?.name || '历史账号'; }
function stock(value) { return value === '有' || /^\d+$/.test(String(value)) && Number(value)>0; }
function hasStock(train) { const seat=$('#seat-filter').value; return train.can_buy && (seat ? stock(train.seats[seat]) : Object.values(train.seats).some(stock)); }
function badge(status) { const color = active.includes(status)?'blue': ['success','found'].includes(status)?'green':['review','interrupted','failed','unconfirmed'].includes(status)?'orange':''; return `<span class="badge ${color}">${esc(statuses[status]||status)}</span>`; }
function showPage(page) {
  state.page = ['query','tasks','accounts'].includes(page)?page:'query';
  const copy = {query:['车票查询','下一程，从这里出发。','查找合适的车次，让每个账号的抢票安排井然有序。'],tasks:['抢票任务','每一次出发，都有安排。','集中查看每个账号的任务进度、接口状态与订单结果。'],accounts:['账号管理','你的账号，各自就位。','独立登录、独立会话，为每个出行安排选择正确的账号。']}[state.page];
  $('#breadcrumb').textContent=copy[0]; $('#page-title').textContent=copy[1]; $('#page-description').textContent=copy[2];
  for (const p of ['query','tasks','accounts']) $(`#page-${p}`).hidden=p!==state.page;
  $$('.nav').forEach(b=>b.classList.toggle('active',b.dataset.page===state.page));
  $('#heading-action').textContent=state.page==='tasks'?'＋ 查询并创建任务':'＋ 添加账号';
  if (location.hash !== `#${state.page}`) history.replaceState(null,'',`#${state.page}`);
}
function renderState() {
  const running=state.tasks.filter(t=>active.includes(t.status)).length;
  $('#nav-count').textContent=running;
  const blocked=state.tasks.filter(t=>active.includes(t.status)&&!state.accounts.find(a=>a.id===t.account_id)?.logged_in);
  $('#session-alert').hidden=!blocked.length;
  $('#session-alert').innerHTML=blocked.length?`${blocked.length} 个抢票任务的账号未登录，任务配置已保留。请先恢复登录，才能继续执行。 <button class="small" data-page="accounts">去登录</button>`:'';
  $('#stats').innerHTML=[['在线账号',state.accounts.filter(a=>a.logged_in).length,'♙'],['进行中任务',running,'↗'],['已生成订单',state.tasks.filter(t=>t.status==='success').length,'✓'],['待核对账号',state.accounts.filter(a=>a.review_required).length,'◷']].map(([label,value,icon])=>`<div class="stat"><div><small>${label}</small><strong>${value}</strong></div><div class="stat-icon">${icon}</div></div>`).join('');
  const selected=$('#query-account').value;
  $('#query-account').innerHTML=options(state.accounts.filter(a=>a.logged_in&&!a.busy).map(a=>[a.id,a.name]),selected,'选择在线空闲账号');
  if (!$('#query-account').value) $('#query-account').value=state.accounts.find(a=>a.logged_in&&!a.busy)?.id||'';
  renderAccounts(); renderTasks();
  if (state.dialog?.kind==='detail') renderDetail(state.dialog.id);
}
async function refresh() {
  if (polling) return;
  polling=true;
  try { const data=await api('state'); Object.assign(state,{accounts:data.accounts,tasks:data.tasks}); $('#date').min=data.today; $('#date').removeAttribute('max'); if (!$('#date').value) $('#date').value=data.today; $('#date-range').textContent=`实时余票至 ${data.max_date}；更远日期可参考近期车次预约`; $('#connection').textContent='已连接'; renderState(); }
  catch (error) { $('#connection').textContent='连接中断'; }
  finally { polling=false; }
}
function renderAccounts() {
  $('#account-list').innerHTML=state.accounts.length?state.accounts.map(a=>`<article class="card account-card"><div class="account-top"><div class="avatar">${esc(a.name[0])}</div><div><h3>${esc(a.name)}</h3><p>${esc(a.identity||'尚未绑定12306身份')}</p></div></div><div class="account-info"><span>${a.busy?'正在执行操作':'独立会话'}</span><span class="badge ${a.logged_in?'green':''}">${a.logged_in?(['unconfirmed','login_failed','cookie_save_failed','cookie_delete_failed'].includes(a.session_events?.at(-1)?.kind)?'会话待重试':'已登录'):'未登录'}</span></div>${a.logged_in?`<p class="help">自动检查：随机20分钟内一次${a.session_check_next?` · 下次 ${esc(beijingTime(a.session_check_next*1000))}`:''}</p>`:''}${a.diagnostic_error?`<p class="review-note">${esc(a.diagnostic_error)}</p>`:''}${a.session_events?.length?`<p class="help">${esc(new Date(a.session_events.at(-1).at*1000).toLocaleString('zh-CN'))} · ${esc(a.session_events.at(-1).message)}</p>`:''}${a.review_required?'<div class="review-note">这个账号有已生成或结果待确认的订单。请先<a href="https://kyfw.12306.cn/otn/view/train_order.html" target="_blank" rel="noopener">到12306核对订单 ↗</a>，处理后再解除限制。</div>':''}<div class="account-actions"><button class="small" data-session-history="${a.id}">会话记录</button><button class="${a.logged_in?'':'primary'} small" data-login="${a.id}" ${a.busy?'disabled':''}>${a.logged_in?'检查登录':'扫码登录'}</button><button class="small" data-passengers="${a.id}" ${!a.logged_in||a.busy?'disabled':''}>乘车人</button><button class="subtle small" data-rename="${a.id}" ${a.busy?'disabled':''}>重命名</button>${a.logged_in?`<button class="subtle small" data-logout="${a.id}" ${a.busy?'disabled':''}>退出</button>`:`<button class="subtle small danger" data-delete="${a.id}" ${a.busy?'disabled':''}>删除</button>`}${a.review_required?`<button class="small" data-review="${a.id}" ${a.busy?'disabled':''}>已处理官方订单</button>`:''}</div></article>`).join(''):'<div class="card empty span2"><div class="empty-icon">♙</div><h3>添加第一个出行账号</h3><p>扫码使用12306账号，无需在这里输入密码。</p><button class="primary" data-add-account>＋ 添加账号</button></div>';
}
function renderTasks() {
  const account=$('#task-account').value;
  const accountOptions=options(state.accounts.map(a=>[a.id,a.name]),account,'全部账号');
  if($('#task-account').innerHTML!==accountOptions)$('#task-account').innerHTML=accountOptions;
  const search=$('#task-search').value.trim().toUpperCase(), date=$('#task-date').value;
  const list=state.tasks.filter(t=>(state.taskFilter==='all'||state.taskFilter==='active'&&active.includes(t.status)||state.taskFilter==='attention'&&['failed','review','interrupted','unconfirmed'].includes(t.status)||t.status===state.taskFilter)
    &&(!account||t.account_id===account)&&(!date||t.config.train_date===date)
    &&(!search||[t.config.from_station,t.config.to_station,...t.config.trains].join(' ').toUpperCase().includes(search)));
  $('#task-count').textContent=`显示 ${list.length} / ${state.tasks.length} 个任务`;
  $('#task-list').innerHTML=list.length?list.map(t=>`<article class="card task-card"><div class="task-top"><div class="task-heading"><div class="task-title-row"><div class="task-route">${esc(t.config.from_station)} <span>→</span> ${esc(t.config.to_station)} <span>·</span> ${esc(t.config.trains.join(' / '))}</div><div class="task-account-label"><span>账号</span><strong>${esc(accountName(t.account_id))}</strong></div></div><div class="task-meta"><span>${esc(t.config.train_date)}</span><span>${t.config.auto_submit?'自动提交':'历史任务'}</span><span>${t.config.priority_strategy==='seat_first'?'席别优先':'车次优先'}</span></div></div>${badge(t.status)}</div>${journeyHtml(t)}<div class="task-bottom"><div class="task-message">${esc(t.message)}<div class="help">已查询 ${t.attempt} 次 / 上限 ${t.config.max_retries||1000} 次 · 常规 ${t.config.interval||3} 秒 / 开售 ${t.config.hot_interval||0.5} 秒${t.sale_at?` · ${saleLabel(t)} ${saleDisplay(t)}`:''}</div></div><div class="task-actions"><button class="small" data-detail="${t.id}">任务详情</button><button class="small" data-rules="${t.id}">抢票说明</button>${['draft','stopped'].includes(t.status)&&!t.order_attempted&&!t.order_id?`<button class="primary small" data-start="${t.id}">${t.status==='stopped'?'继续抢票':'启动任务'}</button>`:''}${active.includes(t.status)||t.status==='draft'?`<button class="small danger" data-stop="${t.id}" ${t.status==='stopping'?'disabled':''}>${t.status==='draft'?'取消':'暂停'}</button>`:''}<button class="subtle small danger" data-delete-task="${t.id}" ${active.includes(t.status)&&t.status!=='reserved'?'disabled title="请先停止任务，等待结束后删除"':''}>删除</button></div></div>${t.order_id||t.status==='review'?`<div class="order-line">${t.order_id?`订单号 ${esc(t.order_id)}`:'订单结果待确认'} · <a href="https://kyfw.12306.cn/otn/view/train_order.html" target="_blank" rel="noopener">前往12306核对并支付 ↗</a></div>`:''}</article>`).join(''):`<div class="card empty"><div class="empty-icon">▤</div><h3>${state.tasks.length?'当前筛选下没有任务':'这里还没有任务'}</h3><p>${state.tasks.length?'点击“清空筛选”查看其他任务。':'在车次列表点击“抢票”，为指定账号配置任务。'}</p><button class="secondary" data-goto-query>查询车票 →</button></div>`;
  list.filter(t=>['draft','reserved','stopped'].includes(t.status)&&!t.order_attempted&&!t.order_id).forEach(t=>{
    $(`[data-detail="${t.id}"]`)?.parentElement.insertAdjacentHTML('beforeend', `<button class="small" data-edit-start="${t.id}">编辑开始时间</button>`);
  });
}
function priceBounds() {
  const min=$('#price-min').value, max=$('#price-max').value;
  return {min:min===''?0:Number(min),max:max===''?Infinity:Number(max),enabled:min!==''||max!=='',invalid:$('#price-min').validity.badInput||$('#price-max').validity.badInput||Number(min)<0||Number(max)<0||(min!==''&&max!==''&&Number(min)>Number(max))};
}
function matchesPrice(t) {
  const b=priceBounds(), seat=$('#seat-filter').value;
  return !b.invalid&&(!b.enabled||(seat?[seat]:seats).some(s=>Number.isFinite(t.prices?.[s])&&t.prices[s]>=b.min&&t.prices[s]<=b.max&&!['--','',null,undefined].includes(t.seats[s])&&(!$('#only-stock').checked||(t.can_buy&&stock(t.seats[s])))));
}
function priceCell(t,s) {
  if(['--','',null,undefined].includes(t.seats[s]))return '';
  const value=t.prices?.[s];
  return `<small class="ticket-price" data-price-seat="${esc(s)}">${Number.isFinite(value)?`${state.reference?'参考 ':''}¥${esc(value)}${s.includes('卧')?' 起':''}`:t.priceLoading?'票价加载中…':t.price_query&&!t.priceDone?'等待加载…':t.priceError?'获取失败':'未返回票价'}</small>`;
}
let priceGeneration=0, priceController=null, priceRenderTimer=null;
function updatePriceRow(t) {
  renderPriceStatus();
  if(priceBounds().enabled) {
    if(!priceRenderTimer)priceRenderTimer=setTimeout(()=>{priceRenderTimer=null;renderTrains();},100);
  } else {
    const row=$(`#results tr[data-train-index="${state.trains.indexOf(t)}"]`);
    if(row)$$('[data-price-seat]',row).forEach(node=>{node.outerHTML=priceCell(t,node.dataset.priceSeat);});
  }
  if(state.dialog?.kind==='book'&&state.dialog.route.from_station===t.from_station&&state.dialog.route.to_station===t.to_station){
    const saved=state.dialog.catalog.find(r=>r.train===t.train);if(saved)saved.prices=t.prices;
    state.dialog.updateSummary?.();
  }
}
async function loadPrices(rows,generation) {
  priceController?.abort();const controller=new AbortController();priceController=controller;
  const pending=rows.filter(t=>t.price_query&&!t.priceDone);let index=0;
  async function worker(){
    while(index<pending.length&&generation===priceGeneration){
      const t=pending[index++];t.priceLoading=true;t.priceError=false;updatePriceRow(t);
      try {const result=await api('prices',{...t.price_query,refresh:t.forcePrice===true},controller.signal);if(generation!==priceGeneration)return;t.prices=result.prices;}
      catch(error){if(generation!==priceGeneration||controller.signal.aborted)return;t.priceError=true;}
      finally{t.priceLoading=false;}
      if(generation!==priceGeneration)return;t.priceDone=true;updatePriceRow(t);
    }
  }
  await Promise.all(Array.from({length:Math.min(3,pending.length)},worker));
}
function filteredTrains() {
  const texts=$('#train-filter').value.trim().toUpperCase().split(/[,，、;；\s]+/).filter(Boolean); const from=$('#from-filter').value; const to=$('#to-filter').value; const range=$('#time-filter').value.split('-').map(Number); const seat=$('#seat-filter').value;
  const result=state.trains.filter(t=> {
    const prefix=t.train[0];
    const typeMatches=state.type==='all'||(state.type==='other'?!'GDCZTK'.includes(prefix):state.type.includes(prefix));
    return typeMatches && matchesPrice(t) &&
      (!texts.length||texts.some(text=>t.train.includes(text))) && (!from||t.from_station===from) && (!to||t.to_station===to) &&
      (!seat||!['--','',undefined,null].includes(t.seats[seat])) &&
      (!$('#time-filter').value||Number(t.departure.split(':')[0])>=range[0]&&Number(t.departure.split(':')[0])<range[1]) &&
      (!$('#only-stock').checked||hasStock(t));
  });
  const duration=t=>t.duration.split(':').reduce((a,x)=>a*60+Number(x),0);
  result.sort((a,b)=>$('#sort-filter').value==='duration'?duration(a)-duration(b):$('#sort-filter').value==='stock'?Number(hasStock(b))-Number(hasStock(a))||a.departure.localeCompare(b.departure):a.departure.localeCompare(b.departure));
  return result;
}
function renderPriceStatus() {
  const b=priceBounds(), pending=state.trains.filter(t=>t.price_query&&!t.priceDone).length;
  $('#price-note').textContent=b.invalid?'最低票价不能高于最高票价，且不能为负数。':`${pending?`票价加载中，还剩 ${pending} 趟。 `:''}${state.reference?'参考日期成人票价，目标日期以开售后为准。':'成人单人票价，卧铺为起价，实际铺位以订单为准。'}${b.enabled?' 仅显示已获取票价且符合区间的席别；同时满足所选席别和余票条件。':''}`;
  $('#retry-prices').hidden=Boolean(pending)||!state.trains.some(t=>t.price_query&&seats.some(s=>!['--','',null,undefined].includes(t.seats[s])&&!Number.isFinite(t.prices?.[s])));
}
function renderTrains() {
  if (!state.route) return;
  renderPriceStatus();
  const list=filteredTrains(); $('#result-count').textContent=`${list.length} / ${state.trains.length}`;
  if (!list.length) { $('#results').innerHTML='<div class="empty"><div class="empty-icon">⌕</div><h3>没有符合条件的车次</h3><p>试试调整日期、路线，或重置筛选条件。</p></div>'; return; }
  const cols=seats.filter(s=>state.trains.some(t=>!['--','',null,undefined].includes(t.seats[s])));
  $('#results').innerHTML=`<div class="table-scroll"><table><thead><tr><th>车次</th><th>出发</th><th>历时</th><th>到达</th>${cols.map(s=>`<th>${s}</th>`).join('')}<th>操作</th></tr></thead><tbody>${list.map(t=>`<tr data-train-index="${state.trains.indexOf(t)}"><td><div class="train-code">${esc(t.train)}</div><span class="train-kind">${esc(t.status||(['G','D','C'].includes(t.train[0])?'高铁 / 动车':'普速列车'))}</span></td><td><div class="time">${esc(t.departure)}</div><div class="station">${esc(t.from_station)}</div></td><td><span class="duration">${esc(t.duration)}<i class="route-line"></i></span></td><td><div class="time">${esc(t.arrival)}</div><div class="station">${esc(t.to_station)}</div></td>${cols.map(s=>`<td class="${stock(t.seats[s])?'stock':'no-stock'}">${stock(t.seats[s])?t.seats[s]==='有'?'有票':`${esc(t.seats[s])} 张`:t.seats[s]==='参考'?'参考':['无','0'].includes(t.seats[s])?'无票':['--','',undefined].includes(t.seats[s])?'—':'未知'}${priceCell(t,s)}</td>`).join('')}<td><button class="${hasStock(t)?'primary':'secondary'} small" data-book="${esc(t.train)}" data-origin="${esc(t.from_station)}" data-destination="${esc(t.to_station)}">${state.reference?'预约':'抢票'}</button></td></tr>`).join('')}</tbody></table></div>`;
}
function modal(title, subtitle, body, footer='') {
  clearTimeout(state.loginTimer);
  $('#dialog').innerHTML=`<div class="dialog-header"><div><h2 id="dialog-title">${title}</h2><p>${subtitle}</p></div><button class="close" data-close aria-label="关闭">×</button></div><div class="dialog-body">${body}</div>${footer?`<div class="dialog-footer">${footer}</div>`:''}`;
  if (!$('#dialog').open) $('#dialog').showModal();
}
function closeModal() { clearTimeout(state.loginTimer); state.dialog=null; $('#dialog').close(); }
let stationNames = null;
async function selectStation(field) {
  state.dialog={kind:'station',field};
  modal(field==='from'?'选择出发站':'选择到达站','输入站名后按回车确认，也可以点击选择。', '<p class="muted">正在加载车站列表…</p>');
  try {
    if(!stationNames)stationNames=(await api('stations')).sort((a,b)=>a.localeCompare(b,'zh-CN'));
    if(state.dialog?.kind!=='station'||state.dialog.field!==field||!$('#dialog').open)return;
    const cities=['北京','上海','广州','深圳','南昌','杭州','南京','武汉','长沙','成都','重庆','西安','郑州','天津','济南','合肥','福州','厦门','昆明','贵阳','南宁','海口','沈阳','长春','哈尔滨','太原','石家庄','兰州','呼和浩特','乌鲁木齐','西宁','银川','拉萨'];
    $('.dialog-body').innerHTML=`<label>搜索城市 / 车站<input id="station-search" placeholder="例如：南昌、南昌西" autocomplete="off"></label><div class="station-city-label">常用城市快捷筛选</div><div class="station-cities"><button type="button" class="chip active" data-station-keyword="">全部车站</button>${cities.filter(c=>stationNames.some(s=>s.includes(c))).map(c=>`<button type="button" class="chip" data-station-keyword="${esc(c)}">${esc(c)}</button>`).join('')}</div><p class="help">城市按钮按站名筛选；选择的是具体车站。</p><div id="station-count" class="muted" role="status"></div><div id="station-list" class="station-options"></div><div class="station-pages"><button type="button" id="station-prev">上一页</button><span id="station-page"></span><button type="button" id="station-next">下一页</button></div>`;
    let page=0;
    function render() {
      const keyword=$('#station-search').value.trim();
      const matches=stationNames.filter(name=>name.includes(keyword));
      const pages=Math.max(1,Math.ceil(matches.length/60));
      page=Math.min(page,pages-1);
      $('#station-count').textContent=`${keyword?`“${keyword}”匹配`:'全部'} ${matches.length} 个车站 · 点击选择`;
      $('#station-list').innerHTML=matches.length?matches.slice(page*60,(page+1)*60).map(name=>`<button type="button" data-station-name="${esc(name)}" class="${name===$('#'+field).value?'selected':''}">${esc(name)}</button>`).join(''):'<p class="muted">没有找到车站，请更换关键词或查看全部车站。</p>';
      $('#station-page').textContent=`${page+1} / ${pages}`;
      $('#station-prev').disabled=page===0;
      $('#station-next').disabled=page===pages-1;
      $$('[data-station-keyword]').forEach(b=>b.classList.toggle('active',b.dataset.stationKeyword===keyword));
    }
    $('#station-search').oninput=()=>{page=0;render();};
    $('#station-search').onkeydown=e=>{
      if(e.key!=='Enter'||e.isComposing)return;
      e.preventDefault();
      const keyword=e.currentTarget.value.trim();
      const matches=keyword?stationNames.filter(name=>name.includes(keyword)):[];
      const selected=matches.find(name=>name===keyword)||(matches.length===1?matches[0]:null);
      if(selected){$('#'+field).value=selected;closeModal();$('#'+field).focus();}
      else $('#station-count').textContent=matches.length?'匹配到多个车站，请补全站名或点击选择。':'请输入可匹配的车站名称。';
    };
    $('.station-cities').onclick=e=>{
      const b=e.target.closest('[data-station-keyword]');if(!b)return;
      $('#station-search').value=b.dataset.stationKeyword;page=0;render();
    };
    $('#station-list').onclick=e=>{
      const b=e.target.closest('[data-station-name]');if(!b)return;
      $('#'+field).value=b.dataset.stationName;closeModal();$('#'+field).focus();
    };
    $('#station-prev').onclick=()=>{page--;render();};
    $('#station-next').onclick=()=>{page++;render();};
    render();$('#station-search').focus();
  } catch(error) {
    if(state.dialog?.kind==='station'&&state.dialog.field===field){
      $('.dialog-body').innerHTML='<p class="muted">车站列表加载失败，请稍后重试。</p><button type="button" id="retry-stations">重新加载</button>';
      $('#retry-stations').onclick=()=>selectStation(field);
    }
  }
}
function errorInDialog(error) { const node=$('#dialog-error'); if(node) node.textContent=error.message; else toast(error.message); }
function addAccount(id=null) {
  const a=state.accounts.find(a=>a.id===id); state.dialog={kind:'account'};
  modal(id?'重命名账号':'添加12306账号','使用备注区分家人或不同用途的账号。',`<form id="account-form"><label>账号备注<input id="account-name" maxlength="40" required placeholder="如：我的账号、家人账号" value="${esc(a?.name||'')}"></label><div id="dialog-error" class="form-error"></div></form>`,`<button data-close>取消</button><button class="primary" form="account-form">${id?'保存':'添加账号'}</button>`);
  $('#account-form').onsubmit=async e=>{ e.preventDefault(); const b=$('[form=account-form]'); b.disabled=true; try { await api(id?`accounts/${id}/rename`:'accounts',{name:$('#account-name').value}); closeModal(); await refresh(); toast(id?'备注已保存':'账号已添加，点击扫码登录'); } catch(error) {errorInDialog(error);} finally{b.disabled=false;} };
}
async function showSessionHistory(id) {
  const dialog={kind:'session-history',id,before:null};state.dialog=dialog;
  modal('会话记录',esc(accountName(id)), '<p class="help">每次操作都有追踪编号，可查看检查、恢复、扫码及凭证保存过程。旧版未记录的信息无法补回；平台未说明的失效原因保持未知。</p><div id="session-history-list" class="timeline"></div><div id="history-error" class="form-error"></div>', '<button id="history-more">加载更早记录</button><button data-close>关闭</button>');
  const sources={sale_verification:'核验车次开售状态',check_login:'检查 / 登录',query:'查票',passengers:'查看乘车人',booking_task:'抢票任务',service_restore:'服务启动',account_create:'添加账号',logout:'主动退出',delete_account:'删除账号',idle:'后台记录'};
  async function load() {
    const button=$('#history-more');button.disabled=true;button.textContent='正在加载…';$('#history-error').textContent='';
    try {
      const result=await api(`accounts/${id}/session-history${dialog.before?`?before=${dialog.before}`:''}`);
      if(state.dialog!==dialog)return;
      const rows=result.events.map(e=>`<div class="event"><time>${esc(new Date(e.at*1000).toLocaleString('zh-CN'))} · ${esc(sources[e.operation]||'旧版记录')}</time><strong>${esc(e.message)}</strong><details><summary>查看诊断详情${e.trace_id?` · ${esc(e.trace_id.slice(0,10))}`:''}</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify(e,null,2))}</pre></details></div>`).join('');
      $('#session-history-list').insertAdjacentHTML('beforeend',rows||(!dialog.before?'<p class="muted">暂无会话记录。</p>':''));
      dialog.before=result.next_before;button.disabled=!result.next_before;button.textContent=result.next_before?'加载更早记录':'已显示全部记录';
    } catch(error) {
      if(state.dialog!==dialog)return;
      $('#history-error').textContent=error.message;button.disabled=false;button.textContent='重新加载';
    }
  }
  $('#history-more').onclick=load;await load();
}
async function login(id) {
  state.dialog={kind:'login',id}; modal('检查登录',esc(accountName(id)), '<div class="qr"><div id="login-content" class="loading">正在检查会话，必要时自动恢复…</div></div><div id="dialog-error" class="form-error"></div>',`<button data-cancel-login="${id}">停止登录</button><button data-close>关闭</button>`);
  try {await api(`accounts/${id}/login`,{});} catch(error){errorInDialog(error);return;}
  async function tick() {
    if(state.dialog?.kind!=='login'||state.dialog.id!==id) return;
    try {
      const d=await api(`accounts/${id}/login`);
      if(state.dialog?.kind!=='login'||state.dialog.id!==id)return;
      $('#dialog-title').textContent=d.qr?'扫码登录':d.status==='confirmed'?'登录有效':'检查登录';
      $('#login-content').classList.remove('loading');
      $('#login-content').innerHTML=`${d.qr?`<img alt="12306登录二维码" src="data:image/png;base64,${d.qr}">`:`<div class="empty-icon">${d.status==='confirmed'?'✓':'♙'}</div>`}<p class="identity">${esc(d.message)}</p>${d.qr?`<p>请用12306 App扫码，并在手机端确认<br>二维码剩余 ${Math.max(0,Math.ceil(d.expires_at-Date.now()/1000))} 秒</p>`:''}`;
      if(['confirmed','failed','expired'].includes(d.status)){await refresh();return;}
      state.loginTimer=setTimeout(tick,1000);
    }catch(error){errorInDialog(error);}
  } tick();
}
async function showPassengers(id) {
  state.dialog={kind:'passengers'}; modal('常用乘车人',esc(accountName(id)),'<div id="passenger-list">正在从12306加载…</div><div id="dialog-error" class="form-error"></div>','<button data-close>完成</button>');
  try {const rows=await api(`accounts/${id}/passengers`,{}); if(state.dialog?.kind!=='passengers')return; $('#passenger-list').innerHTML=rows.map(p=>`<div class="account-info"><strong>${esc(p.name)}</strong><span>${p.type==='1'?'成人':'非成人（暂不支持）'}</span></div>`).join('')||'暂无常用乘车人，请先在12306添加';}catch(error){errorInDialog(error);}
}
async function book(train, origin, destination) {
  const row=state.trains.find(t=>t.train===train&&t.from_station===origin&&t.to_station===destination);
  const route=row?{...state.route,from_station:row.from_station,to_station:row.to_station}:{from_station:origin,to_station:destination,train_date:$('#date').value};
  const future=!row||Boolean(state.reference);
  state.dialog={kind:'book',route,requestId:crypto.randomUUID(),choices:[],catalog:[],accountId:''};
  modal('配置抢票任务',`${esc(origin)} → ${esc(destination)} · ${esc(route.train_date)}`,`<form id="booking-form"><div class="summary-box" id="booking-summary"></div><div class="form-section">01 · 账号与乘车人</div><label>执行账号<select id="booking-account" required>${options(state.accounts.filter(a=>a.logged_in&&!a.busy&&!a.review_required).map(a=>[a.id,a.name]),$('#query-account').value,'选择账号')}</select></label><div id="booking-passengers" class="option-grid"></div><div class="help">乘车人来自所选账号。当前支持成人票，每次最多5人。</div><div class="form-section">02 · 车次与席别优先级</div><label class="check"><input id="same-stations-only" type="checkbox">仅看相同出发站、到达站</label><div class="train-picker"><div class="train-combobox"><label for="train-input">选择或搜索车次</label><div class="train-search-field"><input id="train-input" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="train-catalog" placeholder="点击选择，或输入车次搜索" autocomplete="off"><span aria-hidden="true">⌄</span></div><div id="train-catalog" class="train-dropdown" role="listbox" aria-label="可选车次" hidden></div></div><button type="button" id="add-train" class="primary">＋ 添加车次</button><button type="button" id="refresh-trains">刷新车次</button></div><p class="help" id="catalog-note"></p><div id="booking-seats"></div><div class="form-section">03 · 系统如何替你抢票</div><div id="booking-plan" class="summary-box">正在生成凌晨核验安排；保存配置不会立即下单。</div><p class="help">${future?'按近期车次与席别资料选择，实际是否开行以目标日期核验为准。':'默认按起售时间自动安排；已开售的车票，启动后立即检查并抢票。'} 预约需要本机服务持续运行；重启保留预约和账号会话；会话明确失效时才需重新扫码。</p><div class="form-grid"><label>尝试顺序<select id="priority"><option value="train_first">车次优先</option><option value="seat_first">席别优先</option></select></label></div><p class="help">系统自动安排开始时间。平时默认每60秒查一次余票，开售窗口加快查询；买到后停止，也可在任务列表随时停止。</p><details id="advanced-timing" style="margin-top:18px"><summary class="muted">查询设置（通常无需调整）</summary><div class="form-grid" style="margin-top:15px"><label>平时查余票间隔（秒）<input id="interval" type="number" min="1" max="300" step="1" value="60" required></label><label>开售窗口查询间隔（秒）<input id="hot-interval" type="number" min="0.2" max="300" step="0.1" value="0.5" required></label><label>最多查询次数<input id="max-retries" type="number" min="1" max="100000" value="1000" required></label></div></details><details id="preference-settings" open style="margin-top:18px"><summary>选座 / 选铺 · 默认系统分配</summary><div id="preference-note" class="help" role="status"></div><div id="seat-preferences"></div><div id="berth-preferences"></div><p class="help">这里选择的是位置偏好，不是具体车厢或座位号。实际能否选座/选铺以下单时12306开放的能力为准；无法满足时由系统分配。</p></details><div id="dialog-error" class="form-error"></div></form>`,`<button data-close>取消</button><button class="primary" form="booking-form">保存任务配置 →</button>`);
  const dialog=state.dialog;
  $('#priority').closest('.form-grid').insertAdjacentHTML('afterbegin', '<label>抢票开始时间（可选）<input id="start-at" type="datetime-local" step="60"><span class="help">填写后按这个时间开抢，不再等待车次公告；不填则自动核验。</span></label>');
  dialog.preference={positions:[],berths:[],count:0,notice:''};
  function summary(){
    if(state.dialog!==dialog)return;
    $('#booking-summary').textContent=`${accountName($('#booking-account').value)} · 已选 ${dialog.choices.length} 个备选车次 · 只需买成一趟`;
    const origins=[...new Set(dialog.choices.map(o=>o.from_station||route.from_station))],destinations=[...new Set(dialog.choices.map(o=>o.to_station||route.to_station))];
    if(origins.length>1||destinations.length>1)$('#booking-summary').insertAdjacentHTML('beforeend',`<p><strong>含不同车站：${esc(origins.join(' / '))} → ${esc(destinations.join(' / '))}，请确认这些车站均可接受。</strong></p>`);
    $('#booking-summary').insertAdjacentHTML('beforeend',journeyHtml({config:{train_date:route.train_date,from_station:route.from_station,to_station:route.to_station,trains:dialog.choices.map(o=>o.train),train_options:dialog.choices,passenger_ids:$$('#booking-passengers input:checked').map(p=>p.value)},timetable:dialog.choices.map(o=>{const saved=dialog.catalog.find(t=>t.train===o.train)||{train:o.train};const live=state.trains.find(t=>t.train===o.train&&t.from_station===route.from_station&&t.to_station===route.to_station);return {...saved,prices:live?.prices&&Object.keys(live.prices).length?live.prices:saved.prices};})}));
  }
  dialog.updateSummary=summary;
  function renderPlan(){
    $('#booking-plan').innerHTML=`<strong>保存后点击“启动任务”，系统才会开始抢票。</strong><p>${$('#start-at').value?'将按你填写的抢票开始时间开抢，不再等待车次公告。':'未填写时，系统会核验目标车次起售时间后开抢。'}多个备选有票就尝试，买成一趟即停止。开售一分钟内集中尝试，之后每 ${esc($('#interval').value)} 秒继续查余票。</p><p>拿到订单后请到12306支付。保持电脑和服务运行，网页可以关闭。</p>`;
  }

  function renderChoices(){
    $('#booking-seats').innerHTML=dialog.choices.map((choice,index)=>{
      const row=dialog.catalog.find(t=>t.train===choice.train);
      return `<article class="train-choice" data-choice="${index}"><div class="train-choice-header"><strong>${index+1}. ${esc(choice.train)}</strong><div class="train-choice-actions"><button type="button" class="small" data-move="-1" ${index===0?'disabled':''} aria-label="上移 ${esc(choice.train)}">↑</button><button type="button" class="small" data-move="1" ${index===dialog.choices.length-1?'disabled':''} aria-label="下移 ${esc(choice.train)}">↓</button><button type="button" class="small danger" data-remove aria-label="移除 ${esc(choice.train)}">移除</button></div></div><div class="help">${row?`${esc(choice.from_station||route.from_station)} ${esc(row.departure||'—')} → ${esc(choice.to_station||route.to_station)} ${esc(row.arrival||'—')}`:'暂无车次时刻'}</div><div class="option-grid">${(row?.seat_types||[]).map(s=>`<label><input type="checkbox" value="${esc(s)}" ${choice.seat_types.includes(s)?'checked':''}>${esc(s)}</label>`).join('')}</div><div class="help">已选顺序：${esc(choice.seat_types.join(' → ')||'请至少选择一个席别')}。仅显示参考资料中存在的席别，目标日期可能调整。</div><div class="seat-order-buttons">${choice.seat_types.map((s,i)=>i?`<button type="button" data-seat-up="${i}" title="提高${esc(s)}优先级">↑ ${esc(s)}</button>`:'').join('')}</div></article>`;
    }).join('')||'<p class="help">点击上方列表中的车次即可添加，每趟车分别选择席别。</p>';
    summary();renderPreferences();
  }
  function renderPreferences(){
    summary();
    const pref=dialog.preference;
    const people=$$('#booking-passengers input:checked');
    const n=people.length;
    const types=[...new Set(dialog.choices.flatMap(c=>c.seat_types))];
    const layouts={'二等座':'ABCDF','一等座':'ACDF','特等座':'ACF','商务座':'AF'};
    const seated=types.filter(t=>layouts[t]);
    const letters=seated.length?[...layouts[seated[0]]].filter(l=>seated.every(t=>layouts[t].includes(l))):[];
    const sleepers=types.filter(t=>['硬卧','软卧','高级软卧','一等卧','二等卧'].includes(t));
    const middle=sleepers.length>0&&sleepers.every(t=>['硬卧','二等卧'].includes(t));
    const allowed=(n>1?[1,2]:[1]).flatMap(row=>letters.map(l=>`${row}${l}`));
    const resetSeats=pref.count!==n||pref.positions.some(p=>!allowed.includes(p));
    const resetBeds=pref.count!==n||!sleepers.length||(!middle&&pref.berths.includes('middle'));
    if((resetSeats&&pref.positions.length)||(resetBeds&&pref.berths.length))pref.notice='乘车人或席别已变化，不再适用的偏好已重置为系统分配，请重新选择。';
    if(resetSeats)pref.positions=[];if(resetBeds)pref.berths=[];pref.count=n;
    $('#preference-note').textContent=pref.notice;
    $('#seat-preferences').innerHTML='';$('#berth-preferences').innerHTML='';
    if(!n){$('#seat-preferences').innerHTML='<p class="help">先选择乘车人，再选择座位或铺位偏好。</p>';return;}
    if(letters.length&&allowed.length>=n){
      const label={A:'靠窗',B:'中间',C:'过道',D:'过道',F:'靠窗'};
      const rows=(n>1?[1,2]:[1]).map(row=>`<div class="seat-map-row"><span class="seat-row-label">${row===1?'同一排':'相邻排'}</span>${letters.map(l=>`${((l==='D')||(l==='F'&&!letters.includes('D')))?'<span class="seat-aisle">过道</span>':''}<button type="button" data-seat-position="${row}${l}" aria-label="${row===1?'同一排':'相邻排'} ${l} ${label[l]}" aria-pressed="${pref.positions.includes(`${row}${l}`)}" ${n>1&&pref.positions.length>=n&&!pref.positions.includes(`${row}${l}`)?'disabled':''}>${l}<small>${label[l]}</small></button>`).join('')}</div>`).join('');
      $('#seat-preferences').innerHTML=`<div class="preference-heading"><strong>座位位置</strong><button type="button" class="subtle" id="reset-seat-preference" aria-pressed="${!pref.positions.length}">恢复系统分配</button></div><p class="help">${esc(seated.join(' / '))} · ${n} 位乘车人。${seated.length>1?'只显示所选席别共有的位置。':''}${seated.includes('商务座')?'商务座车型未确定，暂提供 A / F 位置偏好。':''}</p><p class="preference-status" role="status">${pref.positions.length?`已选 ${pref.positions.length} / ${n} 个位置${pref.positions.length<n?`，还需选择 ${n-pref.positions.length} 个`:' · 已选齐'}。点击已选座位可取消。`:'当前由系统分配。想靠窗或靠过道？点击下方座位即可选择。'}</p><div class="seat-map">${rows}</div>`;
      $('#reset-seat-preference').onclick=()=>{pref.positions=[];pref.notice='';renderPreferences();};
      $('#seat-preferences').onclick=e=>{const b=e.target.closest('[data-seat-position]');if(!b)return;const p=b.dataset.seatPosition;pref.positions=pref.positions.includes(p)?pref.positions.filter(x=>x!==p):n===1?[p]:[...pref.positions,p];pref.notice='';renderPreferences();};
    }
    if(sleepers.length){
      $('#berth-preferences').innerHTML=`<div class="preference-heading"><strong>铺位位置</strong><button type="button" class="subtle" id="reset-berth-preference" aria-pressed="${!pref.berths.length}">恢复系统分配</button></div><p class="help">${esc(sleepers.join(' / '))} · ${n} 位乘车人。选择想要的铺位数量，具体分配给谁以出票结果为准。</p><div class="berth-cards">${[['lower','下铺','上下方便'],['middle','中铺','硬卧 / 二等卧支持'],['upper','上铺','位置较高']].map(([value,label,hint])=>{
        const count=pref.berths.filter(b=>b===value).length, unavailable=value==='middle'&&!middle;
        return `<div class="berth-card ${count?'selected':''} ${unavailable?'unavailable':''}"><strong>${label}</strong><small>${unavailable?'所选席别不全支持中铺':hint}</small><div class="berth-counter"><button type="button" data-berth-remove="${value}" aria-label="减少${label}" ${!count?'disabled':''}>−</button><span aria-label="${label}人数">${count}</span><button type="button" data-berth-add="${value}" aria-label="增加${label}" ${unavailable||pref.berths.length>=n?'disabled':''}>＋</button></div></div>`;
      }).join('')}</div><p id="berth-selection-status" class="preference-status" role="status">${pref.berths.length?`已选 ${pref.berths.length} / ${n} 人${pref.berths.length<n?`，还需选择 ${n-pref.berths.length} 人的铺位。`:' · 已选齐。'}`:'当前由系统分配。点击 ＋ 选择偏好；不选也可以直接保存任务。'}</p>`;
      $('#reset-berth-preference').onclick=()=>{pref.berths=[];pref.notice='';renderPreferences();};
      $('#berth-preferences').onclick=e=>{
        const b=e.target.closest('[data-berth-add],[data-berth-remove]');if(!b||b.disabled)return;
        if(b.dataset.berthAdd)pref.berths.push(b.dataset.berthAdd);
        else {const index=pref.berths.indexOf(b.dataset.berthRemove);if(index>=0)pref.berths.splice(index,1);}
        pref.notice='';renderPreferences();
      };
    }
    if(letters.length&&allowed.length<n)$('#seat-preferences').innerHTML='<p class="help">当前示意位置少于乘车人数，请使用系统分配。</p>';
    if(!letters.length&&!sleepers.length)$('#seat-preferences').innerHTML='<p class="help">当前所选席别由系统分配座位，不提供位置选择。</p>';
    if(types.some(t=>['硬座','软座'].includes(t)))$('#seat-preferences').insertAdjacentHTML('beforeend','<p class="help">硬座、软座当前由系统分配座位，不提供靠窗或过道位置选择；铺位偏好仅用于卧铺。</p>');
  }
  function addTrain(code){
    code=code.trim().toUpperCase();
    const row=dialog.catalog.find(t=>t.train===code);
    if(!row?.seat_types.length)throw new Error(`${code||'该车次'} 暂无当前区间的席别参考资料，请先刷新车次；不会按车次名称猜席别。`);
    if(dialog.choices.some(o=>o.train===code))throw new Error(`${code} 已添加，请在该行选择席别。`);
    const live=state.trains.find(t=>t.train===code&&t.from_station===route.from_station&&t.to_station===route.to_station);
    const preferred=row.seat_types.find(s=>stock(live?.seats[s]))||row.seat_types[0];
    dialog.choices.push({train:code,from_station:row.from_station||route.from_station,to_station:row.to_station||route.to_station,seat_types:[preferred]});
    $('#train-input').value='';$('#dialog-error').textContent='';renderChoices();closeTrainPicker();
  }
  let catalogGeneration=0;
  async function loadCatalog(force=false){
    const generation=++catalogGeneration;
    const button=$('#refresh-trains');button.disabled=true;
    $('#catalog-note').textContent='正在加载车次…';
    try{
      let rows=await api('train-options',{...route,same_city:true});
      if(force||!rows.length||(train&&!rows.some(t=>t.train===train))){
        const accountId=$('#booking-account').value;
        if(!accountId)throw new Error('选择在线账号后可刷新车次资料。');
        await api('query',{...route,account_id:accountId});
        rows=await api('train-options',{...route,same_city:true});
      }
      if(state.dialog!==dialog||generation!==catalogGeneration)return;
      dialog.catalog=rows;
      if(!$('#train-catalog').hidden)renderTrainPicker();
      let changed=false;
      for(const c of dialog.choices){const allowed=rows.find(r=>r.train===c.train)?.seat_types||[];const next=c.seat_types.filter(s=>allowed.includes(s));changed ||= next.length!==c.seat_types.length;c.seat_types=next;}
      $('#catalog-note').textContent=`同城已收录 ${rows.length} 趟参考车次，实际车站见各行；点击添加，已选顺序就是抢票优先级。${changed?'资料已变化，请重新确认席别。':''}`;
      if(train&&!dialog.choices.length)addTrain(train);
      renderChoices();
    }catch(error){if(state.dialog===dialog){$('#catalog-note').textContent='车次加载未完成，可点击刷新重试。';errorInDialog(error);}}
    finally{if(state.dialog===dialog&&generation===catalogGeneration)button.disabled=false;}
  }
  $('#add-train').onclick=()=>{try{addTrain($('#train-input').value);}catch(error){errorInDialog(error);}};
  let trainCursor=-1;
  function closeTrainPicker(){
    $('#train-catalog').hidden=true;$('#train-input').setAttribute('aria-expanded','false');
    $('#train-input').removeAttribute('aria-activedescendant');trainCursor=-1;
  }
  function renderTrainPicker(){
    const query=$('#train-input').value.trim().toUpperCase();
    const rows=dialog.catalog.filter(r=>r.train.toUpperCase().includes(query)&&(!$('#same-stations-only').checked||((r.from_station||route.from_station)===route.from_station&&(r.to_station||route.to_station)===route.to_station)));
    const list=$('#train-catalog');list.hidden=false;trainCursor=-1;
    $('#train-input').setAttribute('aria-expanded','true');$('#train-input').removeAttribute('aria-activedescendant');
    list.innerHTML=rows.map((r,i)=>{
      const selected=dialog.choices.some(c=>c.train===r.train), unavailable=!r.seat_types.length;
      return `<button type="button" role="option" id="train-option-${i}" class="train-option" data-pick-train="${esc(r.train)}" aria-selected="${selected}" ${selected||unavailable?'disabled':''}><span class="train-option-main"><strong>${esc(r.train)}</strong><span class="train-option-time">${esc(r.departure||'—')} <span>→</span> ${esc(r.arrival||'—')}</span><span class="train-option-action">${selected?'已添加':unavailable?'暂无席别':'＋ 添加'}</span></span><span class="train-option-seats">${esc(r.from_station||route.from_station)} → ${esc(r.to_station||route.to_station)} · ${esc(r.seat_types.join(' · '))}</span></button>`;
    }).join('')||`<p class="train-picker-empty">${query?'没有匹配的车次，请换个关键词。':$('#refresh-trains').disabled?'正在加载车次…':'暂无车次，请点击“刷新车次”。'}</p>`;
  }
  $('#same-stations-only').onchange=async()=>{
    renderTrainPicker();
    if(!$('#same-stations-only').checked)await loadCatalog();
  };
  $('#train-input').onfocus=renderTrainPicker;
  $('#train-input').onclick=renderTrainPicker;
  $('#train-input').oninput=renderTrainPicker;
  $('#train-input').onkeydown=e=>{
    if(e.key==='Escape'){if(!$('#train-catalog').hidden){e.preventDefault();e.stopPropagation();closeTrainPicker();}return;}
    if(['ArrowDown','ArrowUp'].includes(e.key)){
      e.preventDefault();if($('#train-catalog').hidden)renderTrainPicker();
      const rows=$$('#train-catalog [data-pick-train]:not(:disabled)');if(!rows.length)return;
      trainCursor=(trainCursor+(e.key==='ArrowDown'?1:trainCursor<0?0:-1)+rows.length)%rows.length;
      rows.forEach((r,i)=>r.classList.toggle('highlighted',i===trainCursor));
      $('#train-input').setAttribute('aria-activedescendant',rows[trainCursor].id);
      rows[trainCursor].scrollIntoView?.({block:'nearest'});return;
    }
    if(e.key==='Enter'){
      e.preventDefault();const row=$$('#train-catalog [data-pick-train]:not(:disabled)')[trainCursor];
      if(!$('#train-catalog').hidden&&row)row.click();else $('#add-train').click();
    }
  };
  $('#train-catalog').onclick=e=>{const b=e.target.closest('[data-pick-train]');if(!b||b.disabled)return;try{addTrain(b.dataset.pickTrain);}catch(error){errorInDialog(error);}};
  $('#booking-form').addEventListener('click',e=>{if(!e.target.closest('.train-combobox'))closeTrainPicker();});
  $('.train-combobox').addEventListener('focusout',e=>{if(!e.currentTarget.contains(e.relatedTarget))closeTrainPicker();});
  $('#refresh-trains').onclick=()=>loadCatalog(true);
  $('#booking-seats').onchange=e=>{
    const index=Number(e.target.closest('[data-choice]')?.dataset.choice);
    const choice=dialog.choices[index];if(!choice)return;
    if(e.target.checked)choice.seat_types.push(e.target.value);else choice.seat_types=choice.seat_types.filter(s=>s!==e.target.value);
    renderChoices();
  };
  $('#booking-seats').onclick=e=>{
    const b=e.target.closest('button');if(!b)return;
    const index=Number(b.closest('[data-choice]').dataset.choice);
    if(b.hasAttribute('data-remove'))dialog.choices.splice(index,1);
    if(b.dataset.move){const next=index+Number(b.dataset.move);if(next>=0&&next<dialog.choices.length)[dialog.choices[index],dialog.choices[next]]=[dialog.choices[next],dialog.choices[index]];}
    if(b.dataset.seatUp){const i=Number(b.dataset.seatUp),list=dialog.choices[index].seat_types;[list[i-1],list[i]]=[list[i],list[i-1]];}
    renderChoices();
  };
  let passengerGeneration=0;
  async function load(){
    const generation=++passengerGeneration,id=$('#booking-account').value;dialog.accountId=id;
    $('#booking-passengers').textContent=id?'正在加载乘车人…':'请先选择在线空闲账号';summary();renderPreferences();if(!id)return;
    try{const rows=await api(`accounts/${id}/passengers`,{});if(state.dialog!==dialog||generation!==passengerGeneration)return;$('#booking-passengers').innerHTML=rows.map(p=>`<label><input type="checkbox" value="${esc(p.id)}" ${p.type!=='1'?'disabled':''}>${esc(p.name)}${p.type!=='1'?'（非成人）':''}</label>`).join('')||'暂无乘车人，请先在12306添加';renderPreferences();}
    catch(error){if(state.dialog===dialog&&generation===passengerGeneration){$('#booking-passengers').textContent='乘车人加载失败';errorInDialog(error);}}
  }
  $('#booking-passengers').onchange=renderPreferences;
  $('#booking-account').onchange=async()=>{await load();if(state.dialog===dialog&&!dialog.catalog.length)await loadCatalog();};
  $('#booking-form').oninput=()=>{summary();renderPlan();};
  $('#booking-form').onsubmit=async e=>{
    e.preventDefault();const b=$('[form=booking-form]');b.disabled=true;$('#dialog-error').textContent='';
    try{
      if(!dialog.choices.length)throw new Error('请添加至少一个车次。');
      if(dialog.choices.some(o=>!o.seat_types.length))throw new Error('请为每个车次至少选择一个席别。');
      const n=$$('#booking-passengers input:checked').length;
      if(n<1||n>5)throw new Error('请选择1至5位乘车人。');
      if(dialog.preference.positions.length&&dialog.preference.positions.length!==n)throw new Error(`请选满 ${n} 个座位位置，或清空后使用系统分配。`);
      if(dialog.preference.berths.length&&dialog.preference.berths.length!==n)throw new Error(`还需选择 ${n-dialog.preference.berths.length} 人的铺位，或点击“恢复系统分配”。`);
      const berth={lower:0,middle:0,upper:0};for(const value of dialog.preference.berths)berth[value]++;
      const startAt=$('#start-at').value;
      const data={...route,request_id:dialog.requestId,account_id:$('#booking-account').value,passenger_ids:$$('#booking-passengers input:checked').map(x=>x.value),train_options:dialog.choices,priority_strategy:$('#priority').value,reservation:true,hot_interval:Number($('#hot-interval').value),interval:Number($('#interval').value),max_retries:Number($('#max-retries').value),positions:[...dialog.preference.positions].sort(),berth,...(startAt?{start_at:`${startAt.replace('T',' ')}:00`}:{})};
      await api('tasks',data);closeModal();showPage('tasks');await refresh();toast('配置已保存，点击“启动任务”执行');
    }catch(error){errorInDialog(error);}finally{b.disabled=false;}
  };
  renderChoices();renderPlan();await load();if(state.dialog===dialog)await loadCatalog();
}
function renderDetail(id) {
  const t=state.tasks.find(t=>t.id===id);if(!t)return;
  const node=$('#task-detail-body');if(!node)return;
  const logsOpen=$('#technical-log',node)?.open;
  node.innerHTML=`<div class="summary-box"><strong>${esc(t.config.from_station)} → ${esc(t.config.to_station)}</strong><p>乘车人：${esc(t.config.passenger_names.join('、'))} · 账号：${esc(accountName(t.account_id))}</p></div>${journeyHtml(t)}<p><strong>抢票安排：</strong>${esc(startText(t))}</p><div class="summary-box">${badge(t.status)}<p>${esc(nextAction(t,state.accounts.find(a=>a.id===t.account_id)))}</p>${['failed','unconfirmed','interrupted','review'].includes(t.status)?`<p>${esc(t.message)}</p>`:''}</div>${t.order_id||t.status==='review'||t.status==='interrupted'?`<p class="order-line">${t.order_id?`订单号：${esc(t.order_id)}<br>`:''}<a href="https://kyfw.12306.cn/otn/view/train_order.html" target="_blank" rel="noopener">前往12306核对与支付 ↗</a></p>`:''}<details id="technical-log" ${logsOpen?'open':''}><summary>查看执行日志（排查问题时使用）</summary><div class="timeline">${[...t.events].reverse().map(e=>`<div class="event"><time>${new Date(e.at*1000).toLocaleString('zh-CN')}</time>${esc(e.message)}${e.endpoint||e.query_rtt_ms!==undefined||e.dispatch_offset_ms!==undefined?`<code>${esc(JSON.stringify(Object.fromEntries(Object.entries(e).filter(([key])=>!['at','message'].includes(key))),null,2))}</code>`:''}</div>`).join('')||'<p class="muted">启动后会在这里记录执行情况。</p>'}</div></details>`;
}
function showRules(id){const t=state.tasks.find(t=>t.id===id);if(!t)return;state.dialog={kind:'rules',id};modal('抢票说明','怎么抢、什么时候停，以及你需要做什么。',`<div id="rules-body">${rulesHtml(t)}</div>`,'<button data-close>我明白了</button>');}
function detail(id){state.dialog={kind:'detail',id};modal('任务详情','查看行程、抢票状态和下一步操作。','<div id="task-detail-body"></div>','<button data-close>关闭</button>');renderDetail(id);}
function editStart(id){
  const task=state.tasks.find(t=>t.id===id);if(!task)return;
  const value=task.config.start_at?task.config.start_at.replace(' ','T').slice(0,16):'';
  state.dialog={kind:'edit-start',id};
  modal('编辑抢票开始时间',`${esc(task.config.trains.join(' / '))} · 未开始抢票前可修改`, `<form id="edit-start-form"><label>抢票开始时间（可选）<input id="edit-start-at" type="datetime-local" step="60" value="${esc(value)}"><span class="help">填写后按这个时间开抢，不等待车次公告；清空则恢复自动核验。</span></label><div id="dialog-error" class="form-error"></div></form>`, '<button data-close>取消</button><button class="primary" form="edit-start-form">保存</button>');
  $('#edit-start-form').onsubmit=async event=>{event.preventDefault();const button=$('[form="edit-start-form"]');button.disabled=true;try{const selected=$('#edit-start-at').value;await api(`tasks/${id}/edit`,{start_at:selected?`${selected.replace('T',' ')}:00`:''});closeModal();await refresh();toast('抢票开始时间已更新');}catch(error){errorInDialog(error);button.disabled=false;}};
}
function confirmAction(title, message, action, label='确认') {state.dialog={kind:'confirm'};modal(title,'',`<p style="font-size:13px;color:#68768a">${esc(message)}</p><div id="dialog-error" class="form-error"></div>`,`<button data-close>取消</button><button id="confirm-action" class="primary">${label}</button>`);$('#confirm-action').onclick=async e=>{e.target.disabled=true;try{await action();closeModal();await refresh();}catch(error){errorInDialog(error);e.target.disabled=false;}};}
document.addEventListener('click',async e=>{
  const b=e.target.closest('button');if(!b)return;
  try{
    if(b.hasAttribute('data-close'))closeModal();
    if(b.dataset.page)showPage(b.dataset.page);
    if(b.hasAttribute('data-add-account'))addAccount();
    if(b.hasAttribute('data-goto-query'))showPage('query');
    if(b.dataset.sessionHistory)await showSessionHistory(b.dataset.sessionHistory);
    if(b.dataset.login)await login(b.dataset.login);
    if(b.dataset.cancelLogin){await api(`accounts/${b.dataset.cancelLogin}/cancel-login`,{});closeModal();toast('已请求停止登录');}
    if(b.dataset.passengers)await showPassengers(b.dataset.passengers);
    if(b.dataset.rename)addAccount(b.dataset.rename);
    if(b.dataset.logout)confirmAction('退出账号','将清除本次登录会话，下次使用需要重新扫码。',()=>api(`accounts/${b.dataset.logout}/logout`,{}),'退出登录');
    if(b.dataset.delete)confirmAction('删除账号','只删除未关联任务的本地账号记录。',()=>api(`accounts/${b.dataset.delete}/delete`,{}),'删除');
    if(b.dataset.review)confirmAction('确认已处理官方订单','请确认已经在12306核对并处理未完成订单。此处仅解除本地任务限制，不会取消或查询官方订单。',()=>api(`accounts/${b.dataset.review}/review`,{confirmed:true}),'已核对，解除限制');
    if(b.dataset.book)await book(b.dataset.book,b.dataset.origin,b.dataset.destination);
    if(b.dataset.deleteTask){const t=state.tasks.find(t=>t.id===b.dataset.deleteTask);if(t)confirmAction('删除任务',`${t.config.train_date} · ${t.config.from_station} → ${t.config.to_station} · ${t.config.trains.join(' / ')}。任务将从列表移除，未开始的预约会取消，保留必要操作记录。${t.order_attempted||t.order_id?'此操作不会取消12306订单或解除账号核对限制，请到官方订单页处理。':''}`,async()=>{await api(`tasks/${t.id}/delete`,{});toast('任务已删除');},'删除任务');}
    if(b.dataset.editStart)editStart(b.dataset.editStart);
    if(b.dataset.detail)detail(b.dataset.detail);
    if(b.dataset.rules)showRules(b.dataset.rules);
    if(b.dataset.start){const t=state.tasks.find(t=>t.id===b.dataset.start);confirmAction(t.status==='stopped'?'继续抢票':'启动抢票任务',`${accountName(t.account_id)} · ${t.config.passenger_names.join('、')} · ${t.config.train_date} · ${choicesText(t.config)}。${t.config.reservation?'预约启动后，开售前自动准备；请保持服务运行和账号登录。':''}发现有票后将自动提交真实订单，生成订单号后请到12306支付。`,()=>api(`tasks/${b.dataset.start}/start`,{}),t.status==='stopped'?'确认继续':'确认启动');}
    if(b.dataset.stop){b.disabled=true;await api(`tasks/${b.dataset.stop}/stop`,{});await refresh();toast('已请求停止');}
  }catch(error){toast(error.message);b.disabled=false;}
});
$('#dialog').addEventListener('close',()=>{clearTimeout(state.loginTimer);state.dialog=null;});
$('#heading-action').onclick=()=>state.page==='tasks'?showPage('query'):addAccount();
$('#empty-accounts').onclick=()=>showPage('accounts');
for(const field of ['from','to']) {
  $('#'+field).onclick=()=>selectStation(field);
  $('#'+field).onkeydown=e=>{if(['Enter',' ','ArrowDown'].includes(e.key)){e.preventDefault();selectStation(field);}};
}
$('#swap').onclick=()=>{const from=$('#from').value;$('#from').value=$('#to').value;$('#to').value=from;};
$('#type-filters').onclick=e=>{const b=e.target.closest('[data-type]');if(!b)return;state.type=b.dataset.type;$$('#type-filters button').forEach(x=>x.classList.toggle('active',x===b));renderTrains();};
$('#task-search').oninput=renderTasks;
$('#task-date').onchange=renderTasks;
$('#task-account').onchange=renderTasks;
$('#task-reset').onclick=()=>{state.taskFilter='all';$('#task-search').value='';$('#task-date').value='';$('#task-account').value='';$$('#task-filters button').forEach(b=>b.classList.toggle('active',b.dataset.status==='all'));renderTasks();};
$('#task-filters').onclick=e=>{const b=e.target.closest('[data-status]');if(!b)return;state.taskFilter=b.dataset.status;$$('#task-filters button').forEach(x=>x.classList.toggle('active',x===b));renderTasks();};
$('#seat-filter').innerHTML=options(seats.map(s=>[s,s]),'','全部席别');
for(const id of ['train-filter','from-filter','to-filter','time-filter','seat-filter','sort-filter','only-stock','price-min','price-max'])$(`#${id}`).addEventListener('input',renderTrains);
$('#reset-filters').onclick=()=>{state.type='all';$$('#type-filters button').forEach(b=>b.classList.toggle('active',b.dataset.type==='all'));for(const id of ['train-filter','from-filter','to-filter','time-filter','seat-filter','price-min','price-max'])$(`#${id}`).value='';$('#sort-filter').value='departure';$('#only-stock').checked=false;renderTrains();};
$('#retry-prices').onclick=()=>{const rows=state.trains.filter(t=>t.price_query&&seats.some(s=>!['--','',null,undefined].includes(t.seats[s])&&!Number.isFinite(t.prices?.[s])));for(const t of rows){t.priceDone=false;t.forcePrice=true;}renderTrains();void loadPrices(rows,++priceGeneration);};
$('#search-form').onsubmit=async e=>{
  e.preventDefault();priceController?.abort();const generation=++priceGeneration;$('#retry-prices').hidden=true;const button=$('#search-button');button.disabled=true;button.textContent='正在查询…';$('#query-note').textContent='正在获取12306最新车次信息…';
  const data=Object.fromEntries(new FormData(e.target));
  try{const result=await api('query',data);state.trains=result.trains;state.route=result.route;state.reference=Boolean(result.reference);$('#only-stock').disabled=state.reference;if(state.reference)$('#only-stock').checked=false;for(const [id,key] of [['from-filter','from_station'],['to-filter','to_station']])$(`#${id}`).innerHTML=options([...new Set(state.trains.map(t=>t[key]))].sort().map(v=>[v,v]),'','全部车站');$('#query-note').textContent=`${result.route.from_station} → ${result.route.to_station} · ${result.route.train_date} · ${result.reference?`参考 ${result.reference_date} 的车次，非目标日期余票；可预约，开售前核验`:`${new Date(result.queried_at*1000).toLocaleTimeString('zh-CN')} 更新`}`;renderTrains();void loadPrices(state.trains,generation);}
  catch(error){state.route=null;state.trains=[];$('#result-count').textContent='0';$('#query-note').textContent='查询失败，未获得有效余票结果';$('#results').innerHTML=`<div class="empty"><div class="empty-icon">!</div><h3>这次查询未成功</h3><p>${esc(error.message)}</p></div>`;toast(error.message);}
  finally{button.disabled=false;button.innerHTML='查询车票 <span>→</span>';}
};
window.addEventListener('hashchange',()=>showPage(location.hash.slice(1)));
showPage(location.hash.slice(1));
await refresh();
setInterval(refresh,2500);
