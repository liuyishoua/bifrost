import { escapeHTML as e } from './utils.mjs';
import { localImageURL, uploadImage } from './message-editor.mjs';

// This session exists only while its dialog is open. No account-wide polling.
export function conversationSession({uid,api,onUpdate=()=>{},onError=()=>{}}) {
  const base=`/users/${encodeURIComponent(uid)}`;
  let version=0,closed=false,timer,abort=new AbortController(),active=Promise.resolve();
  let accountId,accounts=[],conversation=null,before=null,after=null,more=false,busy=false,sending=false,permission=null;
  const pendingSends=new Map();
  const messages=new Map();
  const live=v=>!closed&&v===version;
  const canSend=()=>Boolean(accounts.find(a=>a.id===accountId)?.can_send)&&(permission?.can_send??true);
  const emit=()=>onUpdate({account_id:accountId,accounts,conversation,busy,sending,send_permission:permission,
    has_more:more||Boolean(conversation&&!conversation.history_complete),
    items:[...messages.values()].sort((a,b)=>(a.attempted_at||0)-(b.attempted_at||0)||(a.record_id<b.record_id?-1:1))});
  function merge(rows) {
    for(const row of rows) {
      let skip=false;
      for(const [id,old] of messages) {
        if(row.message_id&&old.message_id===row.message_id&&old.account_id===row.account_id&&id!==row.record_id) {
          if(row.message_source==='conversation'&&old.message_source!=='conversation')skip=true;
          else messages.delete(id);
        }
      }
      if(!skip)messages.set(row.record_id,row);
    }
  }
  async function read(v,kind='new') {
    const query=new URLSearchParams();
    if(accountId)query.set('account_id',accountId);
    if(kind==='older'&&before)query.set('before',before);
    if(kind==='new'&&after)query.set('after',after);
    const data=await api(`${base}/conversation?${query}`,'GET',undefined,{signal:abort.signal});
    if(!live(v))return;
    accountId=data.account_id;accounts=data.accounts;conversation=data.conversation;permission=data.send_permission??null;
    merge(data.items);
    if(kind==='initial') {before=data.older_cursor;after=data.newer_cursor;more=data.has_more;}
    else if(kind==='older') {before=data.older_cursor||before;more=data.has_more;}
    else if(kind==='new') {
      after=data.newer_cursor||after;
      if(!before) {before=data.older_cursor;more=data.has_more;}
    }
    emit();
  }
  async function sync(v,direction) {
    if(!conversation||!accountId||!live(v))return;
    await api(`${base}/messages/sync`,'POST',{account_id:accountId,direction},{signal:abort.signal});
  }
  function schedule(v) {
    clearTimeout(timer);
    if(live(v))timer=setTimeout(()=>{active=refresh(v);},5000);
  }
  async function refresh(v) {
    if(!live(v))return;
    if(busy||sending) {schedule(v);return;}
    busy=true;emit();
    try {await sync(v,'newer');if(live(v))await read(v);}
    catch(err) {if(live(v))onError(err.message);}
    finally {if(live(v)){busy=false;emit();schedule(v);}}
  }
  return {
    async select(aid) {
      if(sending||closed)return;
      const v=++version;clearTimeout(timer);abort.abort();abort=new AbortController();
      accountId=aid;permission={can_send:false,state:"busy",reason:"正在读取发送权限"};before=null;after=null;more=false;conversation=null;messages.clear();busy=true;emit();
      active=(async()=>{
        try {await read(v,'initial');if(live(v)){await sync(v,'newer');if(live(v))await read(v);}}
        catch(err){if(live(v))onError(err.message);}
        finally{if(live(v)){busy=false;emit();schedule(v);}}
      })();
      await active;
    },
    async older() {
      if(busy||sending||closed||(!more&&(!conversation||conversation.history_complete)))return;
      const v=version;busy=true;emit();
      active=(async()=>{
        try {if(!more)await sync(v,'older');if(live(v))await read(v,'older');}
        catch(err){if(live(v))onError(err.message);}
        finally{if(live(v)){busy=false;emit();}}
      })();
      await active;
    },
    async send(message) {
      const content=typeof message==='string'?{message}:message?.kind==='image'?{message_type:'image',image_id:message.image_id}:null;
      if(sending||closed||!accountId||!canSend()||!content||(!content.image_id&&!content.message?.trim()))return;
      const v=version; sending=true;clearTimeout(timer);emit();
      await active;
      if(!live(v))return;
      if(!canSend()){sending=false;emit();schedule(v);return;}
      const prior=pendingSends.get(accountId);
      const request=prior&&prior.message===content.message&&prior.image_id===content.image_id?prior:
        {account_id:accountId,...content,request_id:crypto.randomUUID()};
      pendingSends.set(accountId,request);
      const local={record_id:'manual:'+request.request_id,account_id:accountId,attempted_at:Date.now()/1000,
        message:content.message||(content.image_id?'[图片]':''),message_kind:content.image_id?'image':'text',
        image_id:content.image_id,image_url:localImageURL(message?.image_url),direction:'outgoing',status:'sending',message_source:'manual'};
      merge([local]);emit();
      try {
        // Closing the window stops reading; an explicitly submitted send still completes on the backend.
        const result=await api(`${base}/conversation/send`,'POST',request);
        if(!live(v))return;
        merge([{...local,...result,record_id:local.record_id}]);emit();
        if(result.status!=='sending')pendingSends.delete(request.account_id);
        if(result.status==='failed')onError(result.error||'发送失败');
        try {await read(v);}
        catch(err){if(live(v))onError('发送结果已保存，对话刷新失败，请稍后查看');}
        return result;
      } catch(err) {if(live(v)) {
        if(err.status>=400&&err.status<500) {messages.delete(local.record_id);pendingSends.delete(request.account_id);}
        else merge([{...local,error:'结果待确认，请稍后查看；未自动重发'}]);
        emit();onError(err.message);
      }}
      finally {if(live(v)){sending=false;emit();schedule(v);}}
    },
    close() {closed=true;version++;clearTimeout(timer);abort.abort();},
  };
}

export function openConversation({dialog,showDialog,api,user,accountId,onClose=()=>{}}) {
  showDialog(`与 ${e(user.nickname||user.uid)} 对话`,
    `<div class="chat-toolbar"><label>执行账号<select id="chat-account" aria-label="当前对话账号"></select></label><span id="chat-sync" class="hint" role="status">正在加载对话…</span></div><div id="chat-error" class="chat-error" role="status" hidden></div><div id="chat-scroll" class="chat-scroll" tabindex="0" aria-label="聊天记录"><button id="chat-older" class="chat-older">加载更早消息</button><div id="chat-messages"></div></div>`,
    '<form id="chat-compose" class="chat-compose"><textarea id="chat-input" aria-label="消息内容" placeholder="输入消息…" maxlength="2000" rows="3" required></textarea><div id="chat-image-draft" hidden></div><div id="chat-permission" class="hint" role="status"></div><div class="row spread"><div class="row"><button type="button" id="chat-image-pick">选择图片</button><input type="file" id="chat-image-file" accept="image/png,image/jpeg,image/gif,image/webp" hidden><span class="hint">文字或图片每次选一种</span></div><button id="chat-send" type="submit" class="primary">发送</button></div></form>');
  dialog.classList.add('conversation-dialog');
  const find=s=>dialog.querySelector(s),scroll=find('#chat-scroll'),list=find('#chat-messages'),select=find('#chat-account');
  const input=find('#chat-input'),sendButton=find('#chat-send'),older=find('#chat-older'),error=find('#chat-error');
  const imagePick=find('#chat-image-pick'),imageFile=find('#chat-image-file'),imageDraft=find('#chat-image-draft');
  let previousAccount,previousRows='',first=true,closed=false,lastState=null,imagePickerAccount;
  const drafts=new Map();
  const draftFor=aid=>{if(!drafts.has(aid))drafts.set(aid,{text:'',image:null,uploading:false,version:0});return drafts.get(aid);};
  const date=value=>value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false}):'时间未记录';
  function update(state) {
    if(closed)return;
    lastState=state;
    const switched=previousAccount!==state.account_id;
    const bottom=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<60;
    const oldHeight=scroll.scrollHeight,oldTop=scroll.scrollTop,oldFirst=list.firstElementChild?.dataset.chatRecord;
    const options=state.accounts.map(a=>`<option value="${e(a.id)}">${e(a.name)}${a.can_send?'':'（暂不可发送）'}</option>`).join('');
    if(select.innerHTML!==options)select.innerHTML=options;
    select.value=state.account_id||'';select.disabled=state.sending;
    if(switched){if(previousAccount)draftFor(previousAccount).text=input.value;previousAccount=state.account_id;first=true;input.value=draftFor(previousAccount).text;error.hidden=true;}
    const rows=state.items.map(row=>`<div class="chat-line ${row.direction==='outgoing'?'outgoing':'incoming'}" data-chat-record="${e(row.record_id)}"><div class="chat-meta">${row.direction==='outgoing'?'我':e(user.nickname||user.uid)} · ${date(row.attempted_at)}</div><div class="chat-bubble">${(row.message_kind==='image'||row.message_type==='image')&&localImageURL(row.image_url)?`<img class="chat-image" src="${e(localImageURL(row.image_url))}" alt="对话图片" loading="lazy">`:e(row.message??'未记录正文')}</div>${row.direction==='outgoing'?`<div class="chat-result ${row.status==='failed'?'danger':''}">${e(({sending:'发送中…',sent:'已发送',failed:'发送失败'})[row.status]||row.status)}${row.error?' · '+e(row.error):''}</div>`:''}</div>`).join('')||'<div class="chat-empty">暂无聊天记录</div>';
    if(rows!==previousRows){list.innerHTML=rows;previousRows=rows;
      if(first||bottom)scroll.scrollTop=scroll.scrollHeight;
      else if(oldFirst&&list.firstElementChild?.dataset.chatRecord!==oldFirst)scroll.scrollTop=oldTop+Math.max(0,scroll.scrollHeight-oldHeight);
      else scroll.scrollTop=oldTop;
      if(state.items.length)first=false;
    }
    older.hidden=!state.has_more;older.disabled=state.busy||state.sending;
    const account=state.accounts.find(a=>a.id===state.account_id);
    const draft=draftFor(state.account_id),allowed=Boolean(account?.can_send)&&(state.send_permission?.can_send??true);
    const disabled=state.sending||!allowed;
    sendButton.disabled=disabled||draft.uploading;
    sendButton.textContent=state.sending?'发送中…':'发送';input.disabled=disabled||Boolean(draft.image)||draft.uploading;input.required=!draft.image;
    input.hidden=Boolean(draft.image);imagePick.disabled=disabled||draft.uploading;
    imageDraft.hidden=!draft.image&&!draft.uploading;
    const preview=draft.uploading?'<span class="hint">正在上传图片…</span>':draft.image?`<img class="message-image-preview" src="${e(localImageURL(draft.image.url))}" alt="待发送图片预览"><button type="button" id="chat-image-remove" ${state.sending?'disabled':''}>移除图片，改发文字</button>`:'';
    if(imageDraft.innerHTML!==preview)imageDraft.innerHTML=preview;
    find('#chat-permission').textContent=!account?.can_send?'当前账号暂不可发送':state.send_permission?.state==='unreplied'?'已发送，等待对方回复':state.send_permission?.state==='replied'?'对方已回复，可连续发送文字或图片，不受首发冷却限制':state.send_permission?.reason||(draft.image?'图片已上传，点击发送后才提交':'Enter 换行 · Ctrl / ⌘ + Enter 发送');
    find('#chat-sync').textContent=state.busy?'正在同步当前会话…':state.conversation?'每 5 秒同步当前会话':'暂无可同步的会话；发送后可继续同步';
  }
  const session=conversationSession({uid:user.uid,api,onUpdate:update,onError:message=>{
    if(!closed){error.textContent=message;error.hidden=false;}
  }});
  select.addEventListener('change',()=>session.select(select.value));
  input.addEventListener('input',()=>{if(previousAccount)draftFor(previousAccount).text=input.value;});
  imagePick.addEventListener('click',()=>{imagePickerAccount=previousAccount;imageFile.click();});
  imageDraft.addEventListener('click',ev=>{
    if(!ev.target.closest('#chat-image-remove')||lastState.sending)return;
    const draft=draftFor(previousAccount);draft.image=null;draft.version++;update(lastState);
  });
  imageFile.addEventListener('change',async()=>{
    const file=imageFile.files[0];imageFile.value='';
    if(!file||!previousAccount||imagePickerAccount!==previousAccount||imagePick.disabled)return;
    const aid=previousAccount,draft=draftFor(aid),version=++draft.version;draft.uploading=true;error.hidden=true;update(lastState);
    try {const image=await uploadImage(file,api);if(!closed&&version===draft.version)draft.image=image;}
    catch(err){if(!closed&&aid===previousAccount){error.textContent=err.message;error.hidden=false;}}
    finally{if(version===draft.version)draft.uploading=false;if(!closed)update(lastState);}
  });
  older.addEventListener('click',()=>session.older());
  scroll.addEventListener('scroll',()=>{if(scroll.scrollTop<25)session.older();});
  find('#chat-compose').addEventListener('submit',async ev=>{
    ev.preventDefault();if(sendButton.disabled)return;
    const aid=previousAccount,draft=draftFor(aid),photo=draft.image;
    const message=photo?{kind:'image',image_id:photo.id,image_url:photo.url}:input.value;error.hidden=true;
    const result=await session.send(message);
    if(!closed&&result?.status==='sent') {
      if(photo&&draft.image===photo)draft.image=null;
      else if(!photo&&draft.text===message)draft.text='';
      if(aid===previousAccount){input.value=draft.text;update(lastState);}
    }
  });
  input.addEventListener('keydown',ev=>{if(ev.key==='Enter'&&(ev.ctrlKey||ev.metaKey)&&!ev.isComposing){ev.preventDefault();find('#chat-compose').requestSubmit();}});
  function close() {
    if(closed)return;closed=true;session.close();dialog.classList.remove('conversation-dialog');
    dialog.removeEventListener('close',close);onClose();
  }
  dialog.addEventListener('close',close);
  session.select(accountId);
  return close;
}
