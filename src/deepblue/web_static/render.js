import {$,state,node,api,labels,banner,safely} from './api.js';

function highlight(code,text) {
  // Small dependency-free lexical highlighter; never generates HTML from input.
  const pattern=/("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:def|class|return|import|from|if|else|for|while|const|let|function|async|await|true|false|null|None|True|False|try|except|raise)\b|\b\d+(?:\.\d+)?\b|#[^\n]*|\/\/[^\n]*)/g;
  let offset=0;
  for(const match of text.matchAll(pattern)){code.append(document.createTextNode(text.slice(offset,match.index)));code.append(node('span',/^['"]/.test(match[0])?'syntax-string':/^#|^\/\//.test(match[0])?'syntax-comment':'syntax-keyword',match[0]));offset=match.index+match[0].length;}
  code.append(document.createTextNode(text.slice(offset)));
}
function inline(parent,text) {
  const pattern=/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^\s)]+\))/g;let offset=0;
  for(const m of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(offset,m.index)));const part=m[0];
    if(part.startsWith('`'))parent.append(node('code','',part.slice(1,-1)));
    else if(part.startsWith('**'))parent.append(node('strong','',part.slice(2,-2)));
    else if(part.startsWith('*'))parent.append(node('em','',part.slice(1,-1)));
    else {const [,label,target]=part.match(/^\[([^\]]+)\]\((.*)\)$/);const a=node('a','',label);
      if(/^https?:\/\//i.test(target)){a.href=target;a.target='_blank';a.rel='noopener noreferrer';}
      else if(!/^[a-z][a-z\d+.-]*:|^\/\//i.test(target)&&!target.startsWith('#')) {a.href='#';a.onclick=e=>{e.preventDefault();document.dispatchEvent(new CustomEvent('open-file',{detail:target}));};}
      parent.append(a);
    }offset=m.index+part.length;
  }parent.append(document.createTextNode(text.slice(offset)));
}
export function markdown(body,text='') {
  body.replaceChildren();const lines=String(text).split('\n');let i=0;
  while(i<lines.length){const line=lines[i];
    if(line.startsWith('```')) {const language=line.slice(3).trim();const values=[];i++;while(i<lines.length&&!lines[i].startsWith('```'))values.push(lines[i++]);if(i<lines.length)i++;const raw=values.join('\n');const box=node('div','code-block'),bar=node('div','code-toolbar'),copy=node('button','','复制');copy.onclick=safely(async()=>{await navigator.clipboard.writeText(raw);copy.textContent='已复制';});bar.append(node('span','',language||'代码'),copy);const pre=node('pre'),code=node('code');highlight(code,raw);pre.append(code);box.append(bar,pre);body.append(box);continue;}
    if(i+1<lines.length&&line.includes('|')&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1])) {const table=node('table'),head=node('thead'),tr=node('tr');const cells=s=>s.replace(/^\s*\||\|\s*$/g,'').split('|');for(const c of cells(line)){const th=node('th');inline(th,c.trim());tr.append(th);}head.append(tr);table.append(head);i+=2;const tbody=node('tbody');while(i<lines.length&&lines[i].includes('|')){const row=node('tr');for(const c of cells(lines[i++])){const td=node('td');inline(td,c.trim());row.append(td);}tbody.append(row);}table.append(tbody);const wrap=node('div','table-wrap');wrap.append(table);body.append(wrap);continue;}
    if(/^\s*([-*+] |\d+\. )/.test(line)){const ordered=/^\s*\d/.test(line),list=node(ordered?'ol':'ul');while(i<lines.length&&/^\s*([-*+] |\d+\. )/.test(lines[i])){const li=node('li');inline(li,lines[i++].replace(/^\s*([-*+] |\d+\. )/,''));list.append(li);}body.append(list);continue;}
    const heading=line.match(/^(#{1,6})\s+(.*)$/);const element=node(heading?'h'+heading[1].length:line.startsWith('> ')?'blockquote':'p');inline(element,heading?heading[2]:line.replace(/^> /,''));if(line.trim())body.append(element);i++;
  }
}
export function clearChat(){ $('conversation').replaceChildren();state.stream=null; }
export function message(role,text){const article=node('article','message '+role),heading=node('div','message-label'),body=node('div','message-body');heading.append(node('strong','',role==='user'?'你':'◈ 深蓝'));markdown(body,text);article.append(heading,body);$('conversation').append(article);return body;}
export function notice(text){$('conversation').append(node('div','notice',text));}
export function tool(name,result,details={}) {
  const card=node('details','tool-card'),heading=node('summary','',`${result?.ok===false?'×':'◇'} ${name} · ${result?.ok===false?'未完成':'工具结果'}${result?.exit_code!==undefined?' · 退出码 '+result.exit_code:''}${details.elapsed_seconds!==undefined?' · '+details.elapsed_seconds+'s':''}`);
  const pre=node('pre','',typeof result==='string'?result:JSON.stringify(result,null,2));card.append(heading,pre);$('conversation').append(card);return card;
}
export function transcript(messages,sessionId) {for(const m of messages){if(m.role==='system')continue;let body;if(m.role==='tool'){let data;try{data=JSON.parse(m.content);}catch{data={output:m.content};}body=tool(m.tool_name||'工具',data,m);}else if(m.content)body=message(m.role,m.content);
  if(m.content_truncated&&body){let offset=0;const more=node('button','','读取完整原文（分段）'),pre=node('pre');more.onclick=safely(async()=>{const r=await api(`message?id=${sessionId}&index=${m.index}&offset=${offset}`);pre.textContent+=r.content;offset=r.next;more.disabled=offset===null;});body.append(more,pre);}
}}
export function follow(){if(state.following){const e=$('conversation');e.scrollTop=e.scrollHeight;$('new-messages').hidden=true;}else $('new-messages').hidden=false;}
export function renderEvent({kind,data}) {
  if(kind==='user')message('user',data.text);
  else if(kind==='text_delta'){if(!state.stream){state.stream=message('assistant','');state.stream.dataset.raw='';}state.stream.dataset.raw+=data.text;state.stream.textContent=state.stream.dataset.raw;}
  else if(kind==='text_end'){if(state.stream)markdown(state.stream,state.stream.dataset.raw);state.stream=null;}
  else if(kind==='text')message('assistant',data.text);
  else if(kind==='tool_start'){const card=tool(data.name,{status:'执行中',arguments:data.arguments});card.classList.add('running-tool');state.stream=null;}
  else if(kind==='tool_end'){document.querySelector('.running-tool')?.remove();tool(data.name,data.result,data);}
  else if(kind==='notice')notice(data.text);
  else if(kind==='done'){notice(`执行：${labels[data.execution_status]||data.execution_status} · 验收：${labels[data.verification_status]||data.verification_status}`);}
  else if(kind==='error'){banner(data.text);notice('错误：'+data.text);}
}
export function metrics(data) {
  const bytes=data.context_bytes||0,limit=data.context_limit||400000;$('context-value').textContent=(bytes/1000).toFixed(1)+' / '+Math.round(limit/1000)+' KB';$('context-progress').value=Math.min(100,100*bytes/limit);$('tokens-value').textContent=(data.usage?.total_tokens||0).toLocaleString();$('compact-value').textContent=data.compactions||0;$('context-footer').textContent=`${data.usage?.total_tokens||0} tokens · ${data.compactions||0} 次压缩`;
  $('verification-state').textContent=labels[data.verification_status]||'未验证';const r=data.recovery||{};$('recovery-summary').textContent=`${r.confirmed_operations||0} 项完成 · ${r.unfinished_operations?.length||0} 项待核对 · ${r.changed_files?.length||0} 个文件变化`;$('recovery-detail').textContent=JSON.stringify(r,null,2);
  const list=$('evidence-list');list.replaceChildren();for(const e of data.evidence||[]){const card=node('details','tool-card');card.append(node('summary','',`${labels[e.status]||e.status} · ${e.command}`),node('pre','',JSON.stringify(e,null,2)));const button=node('button','','查看日志');button.onclick=safely(async()=>{let offset=0;const load=async()=>{const page=await api(`evidence?id=${data.id}&evidence_id=${e.id}&offset=${offset}`);offset=page.next;return page;};await showLog('验收日志',load);});card.append(button);list.append(card);}
  if(!state.busy)$('run-status').textContent=`${data.job_status==='interrupted'?'服务中断，需核对':labels[data.last_run?.execution_status]||'就绪'} · ${labels[data.verification_status]||'未验证'}`;
  const logs=$('tool-logs');logs.replaceChildren();for(const log of data.logs||[]){const button=node('button','quiet-button',`${log.name} · ${log.phase==='finished'?'命令日志':'实时日志'}`);button.onclick=safely(async()=>{let offset=0;await showLog('工具输出',async()=>{const page=await api(`tool-log?id=${data.id}&operation_id=${log.id}&offset=${offset}`);offset=page.next;return page;});});logs.append(button);}
}
let logGeneration=0;
export async function showLog(title,load){const generation=++logGeneration;$('log-title').textContent=title;$('log-content').textContent='';$('log-dialog').showModal();const more=$('log-more');more.hidden=false;more.onclick=safely(async()=>{more.disabled=true;try{const page=await load();if(generation!==logGeneration||!$('log-dialog').open)return;$('log-content').textContent+=page.content;more.hidden=page.next===null||page.next===undefined;if(page.live)setTimeout(()=>{if(generation===logGeneration&&$('log-dialog').open)more.onclick();},1000);}finally{more.disabled=false;}});await more.onclick();}
