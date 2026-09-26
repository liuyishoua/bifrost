import {escapeHTML as e} from './utils.mjs';
import {localImageURL} from './message-editor.mjs';

export function recordMessageContent(row, large=false) {
  if (row.message_kind !== 'image') return `<div class="message record-text">${e(row.message ?? '未记录正文')}</div>`;
  const url=localImageURL(row.image_url);
  if (!url) return '<span class="hint">[图片] 图片未保存，暂无法预览</span>';
  return `<a class="record-image" href="${e(url)}" target="_blank" rel="noopener noreferrer" aria-label="查看消息图片原图（新窗口）"><img class="message-image-preview${large?' record-image-large':''}" src="${e(url)}" alt="消息图片" loading="lazy"><span class="hint">点击查看原图</span></a>`;
}

export function messageNumberLabel(row) {
  const incoming=row.direction==='incoming';
  const number=row.message_number ?? (incoming?row.reply_number:row.send_number);
  return Number.isInteger(number) && number>0 ? `第 ${number} 次${incoming?'对方回复':'发送'}` : `${incoming?'回复':'发送'}次序未确认`;
}

export function messageStageOptions(stages=[]) {
  const options=[];
  for (const direction of ['outgoing','incoming']) {
    const action=direction==='incoming'?'对方回复':'发送';
    options.push({id:`${direction}:1`,name:`第 1 次${action}`},{id:`${direction}:later`,name:`后续${action}（第 2 次及以后）`});
    const numbers=stages.filter(s=>s.startsWith(direction+':')).map(s=>Number(s.split(':')[1])).filter(n=>Number.isInteger(n)&&n>1);
    for (const n of [...new Set(numbers)].sort((a,b)=>a-b)) options.push({id:`${direction}:${n}`,name:`第 ${n} 次${action}`});
    if (stages.includes(`${direction}:unknown`)) options.push({id:`${direction}:unknown`,name:`${action}次序未确认`});
  }
  return options;
}
