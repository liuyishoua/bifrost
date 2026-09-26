import { escapeHTML as e } from './utils.mjs';
import { appURL } from './mount.mjs';

export function localImageURL(url) {
  return typeof url==='string'&&/^\/api\/media\/[A-Za-z0-9_-]+$/.test(url)?appURL(url):'';
}
export async function uploadImage(file,api) {
  if(file.size>5*1024*1024)throw Error('图片不能超过 5 MB');
  if(!['image/png','image/jpeg','image/gif','image/webp'].includes(file.type))throw Error('请选择 PNG、JPEG、GIF 或 WebP 图片');
  const data=await new Promise((resolve,reject)=>{
    const reader=new FileReader();
    reader.onload=()=>resolve(String(reader.result).split(',')[1]);
    reader.onerror=()=>reject(Error('无法读取图片，请重新选择'));
    reader.readAsDataURL(file);
  });
  const image=await api('/media','POST',{data,name:file.name});
  if(!image.id||!localImageURL(image.url))throw Error('图片上传返回无效，请重新选择');
  return image;
}
export function taskMessageValues({first,firsts,replies}) {
  function value(item) {
    if(item.uploading)throw Error('图片正在上传，请稍后保存');
    if(item.kind==='image') {
      if(!item.image_id)throw Error('请先选择并上传图片');
      return {kind:'image',image_id:item.image_id};
    }
    if(!item.text?.trim())throw Error('请填写未成功发送用户的文字内容');
    return {kind:'text',text:item.text};
  }
  if(firsts && (!firsts.length || firsts.length>20))throw Error('首次消息请选择 1～20 条候选文案');
  const candidates=firsts?.map(value);
  return {...(candidates?{first_messages:candidates}:{}),first_message:candidates?.[0]||value(first),reply_messages:replies.filter(item=>item.kind!=='text'||item.text?.trim()).map(value)};
}
export function bindTaskMessageEditor(root,{api,task=null,onError=()=>{}}) {
  const copy=item=>({...item});
  const state={firsts:(task?.first_messages||[task?.first_message||{kind:'text',text:task?.message||''}]).map(copy),
    replies:(task?.reply_messages??[{kind:'text',text:task?.message||''}]).map(copy)};
  function itemMarkup(item,first,index) {
    const imageURL=localImageURL(item.image_url||item.url|| (item.image_id?`/api/media/${item.image_id}`:''));
    return `<div class="message-editor-item" data-message-index="${first?`first-${index}`:index}"><div class="row spread"><strong>${first?`候选 ${index+1}`:`${index+1}. ${item.kind==='image'?'图片':'文字'}`}</strong>${first?`${index===0?`<label>消息类型<select data-message-kind aria-label="首条消息类型"><option value="text" ${item.kind==='text'?'selected':''}>文字</option><option value="image" ${item.kind==='image'?'selected':''}>图片</option></select></label>${state.firsts.length>1?'<button type="button" class="small danger" data-remove-first>删除文案</button>':''}`:'<button type="button" class="small danger" data-remove-first>删除文案</button>'}`:`<div class="row"><button type="button" class="small" data-move="-1" aria-label="上移第 ${index+1} 条" ${index===0?'disabled':''}>上移</button><button type="button" class="small" data-move="1" aria-label="下移第 ${index+1} 条" ${index===state.replies.length-1?'disabled':''}>下移</button><button type="button" class="small danger" data-remove>删除</button></div>`}</div>${item.kind==='text'?`<textarea ${first?'name="message" required':''} data-message-text maxlength="2000" rows="3" aria-label="${first?'未成功发送消息正文':`回复后第 ${index+1} 条文字`}" placeholder="${first?'写下想对用户说的话…':'填写回复后的消息；留空则不发送此条'}">${e(item.text||'')}</textarea>`:`${imageURL?`<img class="message-image-preview" src="${e(imageURL)}" alt="待发送图片预览">`:''}<label class="hint">${item.uploading?'正在上传…':'选择图片（PNG / JPEG / GIF / WebP，最大 5 MB）'}<input type="file" data-message-file accept="image/png,image/jpeg,image/gif,image/webp" ${item.uploading?'disabled':''}></label>`}</div>`;
  }
  function render() {
    if(!root.isConnected)return;
    root.innerHTML=`<fieldset class="message-group"><legend>未成功发送</legend><p class="hint">多条文案随机选一条发送给每个联系人；图片仅支持单张。</p>${state.firsts.map((item,i)=>itemMarkup(item,true,i)).join('')}${state.firsts[0].kind==='text'?'<button type="button" data-add-first>添加候选文案</button>':''}</fieldset><p class="hint">已发送未回复：自动跳过，等待对方回复。</p><fieldset class="message-group"><legend>已发送且已回复</legend><p class="hint">确认回复后自动跟进，同一组图文连续发送，不等待首发间隔或休息；手动暂停仍生效。没有后续文字或图片时，不跟进，也不回查回复（包括最终补查）。</p>${state.replies.map((item,i)=>itemMarkup(item,false,i)).join('')}<div class="row"><button type="button" data-add="text">添加文字</button><button type="button" data-add="image">添加图片</button></div></fieldset>`;
    root.querySelector('[data-message-kind]').addEventListener('change',ev=>{state.firsts=[{kind:ev.target.value,text:state.firsts[0].text||''}];render();});
    root.querySelector('[data-add-first]')?.addEventListener('click',()=>{if(state.firsts.length>=20){onError('最多选择 20 条候选文案');return;}state.firsts.push({kind:'text',text:''});render();});
    root.querySelectorAll('[data-add]').forEach(button=>button.addEventListener('click',()=>{state.replies.push({kind:button.dataset.add,text:''});render();}));
    root.querySelectorAll('[data-message-index]').forEach(node=>{
      const first=node.dataset.messageIndex.startsWith('first-'),index=Number(first?node.dataset.messageIndex.slice(6):node.dataset.messageIndex),item=first?state.firsts[index]:state.replies[index];
      node.querySelector('[data-remove-first]')?.addEventListener('click',()=>{state.firsts.splice(index,1);render();});
      node.querySelector('[data-message-text]')?.addEventListener('input',ev=>{item.text=ev.target.value;});
      node.querySelector('[data-remove]')?.addEventListener('click',()=>{state.replies.splice(index,1);render();});
      node.querySelectorAll('[data-move]').forEach(button=>button.addEventListener('click',()=>{const to=index+Number(button.dataset.move);[state.replies[index],state.replies[to]]=[state.replies[to],state.replies[index]];render();}));
      node.querySelector('[data-message-file]')?.addEventListener('change',async ev=>{
        const file=ev.target.files[0];if(!file)return;
        item.uploading=true;render();
        try {const image=await uploadImage(file,api);item.image_id=image.id;item.image_url=image.url;}
        catch(err){if(root.isConnected)onError(err.message);}
        finally{item.uploading=false;render();}
      });
    });
  }
  render();
  return {values:()=>taskMessageValues(state),setFirstTexts(texts){if(!texts.length||texts.length>20)throw Error('请选择 1～20 条首次话术');state.firsts=texts.map(text=>({kind:'text',text}));render();}};
}
