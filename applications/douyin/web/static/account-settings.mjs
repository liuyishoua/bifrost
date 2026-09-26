import {escapeHTML as e} from './utils.mjs';

const weekdays=['周一','周二','周三','周四','周五','周六','周日'];
const policyFields=[
  ['interval_seconds','发送间隔',0,86400,'秒'],['random_extra_seconds','随机等待上限',0,86400,'秒'],
  ['max_batch_size','每批消息上限',1,10000,'条'],['min_attempts','最少首发次数',1,10000,'次'],
  ['error_rate_percent','错误率大于',0,100,'%'],['rest_minutes','触发后休息',1,1440,'分钟'],
];

export function workSchedule({limited,weekdays:days,start,end,endOfDay=false}) {
  if(!limited)return null;
  if(!Array.isArray(days)||!days.length||days.some(d=>!Number.isInteger(d)||d<1||d>7))throw Error('请至少选择一个工作日');
  const valid=s=>typeof s==='string'&&/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(s);
  end=endOfDay?'24:00':end;
  if(!valid(start)||(!valid(end)&&end!=='24:00'))throw Error('请选择有效的开始和结束时间');
  if(start>=end)throw Error('结束时间必须晚于开始时间，暂不支持跨午夜时段');
  return {weekdays:[...new Set(days)].sort((a,b)=>a-b),start,end};
}
export function workScheduleSummary(schedule) {
  if(!schedule)return '每天全天';
  const days=schedule.weekdays.length===7?'每天':schedule.weekdays.map(d=>weekdays[d-1]).join('、');
  return `${days} · ${schedule.start==='00:00'&&schedule.end==='24:00'?'全天':schedule.start+'～'+schedule.end}`;
}
function workFields(schedule) {
  const days=schedule?.weekdays||[1,2,3,4,5,6,7],endOfDay=schedule?.end==='24:00';
  return `<div class="work-settings">
    <label class="work-mode"><span><strong>限定工作时段</strong><small>关闭时，每天全天可参与调度</small></span><input class="settings-switch" type="checkbox" role="switch" name="work_limited" ${schedule?'checked':''}></label>
    <fieldset data-work-controls ${schedule?'':'disabled'}><legend class="sr-only">每周工作时段（北京时间）</legend>
      <div class="settings-field-heading"><strong>工作日</strong><span>可多选</span></div>
      <div class="weekdays">${weekdays.map((label,i)=>`<label><input type="checkbox" name="work_weekday" value="${i+1}" ${days.includes(i+1)?'checked':''}><span>${label}</span></label>`).join('')}</div>
      <div class="settings-field-heading"><strong>每日时段</strong><span>北京时间</span></div>
      <div class="work-time-range"><label>开始时间<input type="time" name="work_start" value="${e(schedule?.start||'09:00')}" required></label><span aria-hidden="true">—</span><label>结束时间<input type="time" name="work_end" value="${e(endOfDay?'18:00':schedule?.end||'18:00')}" required ${endOfDay?'disabled':''}></label></div>
      <label class="check-label work-end-day"><input type="checkbox" name="work_end_of_day" ${endOfDay?'checked':''}>结束于当天 24:00</label>
    </fieldset><p class="work-preview" data-work-preview aria-live="polite"></p></div>`;
}
function bindWork(root) {
  const form=root.querySelector('.work-settings');
  const update=()=>{
    form.querySelector('[data-work-controls]').disabled=!form.querySelector('[name="work_limited"]').checked;
    form.querySelector('[name="work_end"]').disabled=form.querySelector('[name="work_end_of_day"]').checked;
    try {form.querySelector('[data-work-preview]').textContent='工作安排：'+workScheduleSummary(readWork(form));}
    catch(err){form.querySelector('[data-work-preview]').textContent=err.message;}
  };
  form.addEventListener('change',update);update();
}
function readWork(form) {
  return workSchedule({limited:form.querySelector('[name="work_limited"]').checked,
    weekdays:[...form.querySelectorAll('[name="work_weekday"]:checked')].map(n=>Number(n.value)),
    start:form.querySelector('[name="work_start"]').value,end:form.querySelector('[name="work_end"]').value,
    endOfDay:form.querySelector('[name="work_end_of_day"]').checked});
}

export function editWorkTime({account,dialog,showDialog,api,reload,toast}) {
  showDialog('工作时间 · '+e(account.name),
    `<form id="account-work-form" class="account-settings-form">${workFields(account.work_schedule)}<p class="settings-note">仅影响自动调度。时段结束后，已发起的消息继续处理；手动聊天不受限制。</p><div data-settings-error class="errorbox" hidden></div></form>`,
    '<button data-close>取消</button><button class="primary" type="submit" form="account-work-form">保存工作时间</button>');
  const form=dialog.querySelector('#account-work-form');bindWork(form);
  form.addEventListener('submit',async ev=>{
    ev.preventDefault();const button=ev.submitter;if(button)button.disabled=true;
    const error=form.querySelector('[data-settings-error]');error.hidden=true;
    try {
      await api(`/accounts/${account.id}/sending`,'PATCH',{work_schedule:readWork(form)});
      if(!dialog.open||!dialog.contains(form))return;
      dialog.close();toast('工作时间已保存');await reload();
    } catch(err){error.textContent=err.message;error.hidden=false;}
    finally {if(button)button.disabled=false;}
  });
}

export function editAccountsBatch({selected,dialog,showDialog,api,reload,toast}) {
  const ids=selected.map(a=>a.id);
  const common=key=>selected.every(a=>a.send_policy[key]===selected[0].send_policy[key])?selected[0].send_policy[key]:'';
  const sameWork=selected.every(a=>JSON.stringify(a.work_schedule)===JSON.stringify(selected[0].work_schedule));
  const properties=fields=>fields.map(([key,label,min,max,unit])=>`<div class="batch-property"><label class="check-label"><input type="checkbox" data-policy-field="${key}">${label}</label><div class="settings-number"><input type="number" name="${key}" aria-label="${label}（${unit}）" min="${min}" max="${max}" value="${common(key)}" placeholder="数值不同" disabled required><span>${unit}</span></div></div>`).join('');
  showDialog(`批量编辑 · ${ids.length} 个账号`,
    `<form id="account-batch-form" class="account-settings-form">
      <div class="batch-intro"><p>勾选要修改的属性，统一应用到所选账号。</p><div class="account-batch-targets" aria-label="所选账号">${selected.map(a=>`<span>${e(a.name)}</span>`).join('')}</div></div>
      <section class="settings-section"><h3>发送节奏</h3><div class="batch-property-grid">${properties(policyFields.slice(0,3))}</div></section>
      <section class="settings-section"><h3>首发错误率保护 <small>近 30 分钟 · 不含回复后跟进</small></h3><div class="batch-property-grid">${properties(policyFields.slice(3))}</div></section>
      <section class="settings-section batch-work"><label class="check-label batch-work-heading"><input type="checkbox" id="batch-change-work"><strong>工作时间</strong><span>勾选后统一设置</span></label><fieldset id="batch-work-fields" disabled>${workFields(sameWork?selected[0].work_schedule:null)}</fieldset><p class="batch-work-unchanged">${sameWork?e(workScheduleSummary(selected[0].work_schedule)):'所选账号的工作时间不同'} · 保持原设置</p></section>
      <label class="batch-dispatch"><span>调度状态<small>应用到所有所选账号</small></span><select name="send_enabled"><option value="">保持当前状态</option><option value="false">暂停调度</option><option value="true">恢复调度</option></select></label>
      <p class="settings-note">未勾选的属性保持原值，已有冷却和休息继续生效。</p><div data-settings-error class="errorbox" hidden></div></form>`,
    '<button data-close>取消</button><button class="primary" type="submit" form="account-batch-form">保存所选属性</button>');
  const form=dialog.querySelector('#account-batch-form');bindWork(form);
  form.querySelectorAll('[data-policy-field]').forEach(box=>box.addEventListener('change',()=>{form.elements.namedItem(box.dataset.policyField).disabled=!box.checked;}));
  form.querySelector('#batch-change-work').addEventListener('change',ev=>{form.querySelector('#batch-work-fields').disabled=!ev.target.checked;});
  form.addEventListener('submit',async ev=>{
    ev.preventDefault();const button=ev.submitter;if(button)button.disabled=true;
    const error=form.querySelector('[data-settings-error]');error.hidden=true;
    try {
      const changes={};
      for(const box of form.querySelectorAll('[data-policy-field]:checked')) {
        const key=box.dataset.policyField,value=form.elements.namedItem(key).value;
        if(value==='')throw Error('请填写已勾选属性的数值');
        changes[key]=Number(value);
      }
      if(form.elements.send_enabled.value!=='')changes.send_enabled=form.elements.send_enabled.value==='true';
      if(form.querySelector('#batch-change-work').checked)changes.work_schedule=readWork(form);
      if(!Object.keys(changes).length)throw Error('请至少选择一项需要修改的属性');
      const result=await api('/accounts/batch/sending','PATCH',{account_ids:ids,changes});
      if(!dialog.open||!dialog.contains(form))return;
      dialog.close();toast(`已更新 ${result.updated} 个账号`);await reload();
    } catch(err){error.textContent=err.message;error.hidden=false;}
    finally{if(button)button.disabled=false;}
  });
}
