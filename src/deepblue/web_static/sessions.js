import {$,state,api,node,banner,safely,storage} from './api.js';
import {clearChat,transcript,metrics,follow,notice} from './render.js';
let items=[],next=null,before=null,listRequest=null,sessionRequest=null,listGeneration=0,archived=false;
export function saveDraft(){storage('draft:'+(state.selected||'new'),{prompt:$('prompt').value,verify:$('verify-command').value});}
export function loadDraft(){const data=storage('draft:'+(state.selected||'new'))||{};$('prompt').value=data.prompt||'';$('verify-command').value=data.verify||'';document.dispatchEvent(new Event('draft-loaded'));}
function show(){const list=$('sessions');list.replaceChildren();for(const item of items){const b=node('button','session-item'+(item.id===state.selected?' active':''),item.title);b.title=item.title;b.onclick=safely(()=>openSession(item.id));list.append(b);}if(!items.length)list.append(node('p','empty-sessions','没有匹配的会话'));$('more-sessions').hidden=next===null;}
export async function refreshSessions(more=false){listRequest?.abort();listRequest=new AbortController();const version=++listGeneration;const data=await api(`sessions?cursor=${more?(next||0):0}&q=${encodeURIComponent($('session-search').value)}&archived=${archived?1:0}`,undefined,{signal:listRequest.signal});if(version!==listGeneration)return;items=more?[...items,...data.items]:data.items;next=data.next;$('session-count').textContent=data.total;show();}
export async function openSession(id,{more=false,save=true}={}){
  sessionRequest?.abort();sessionRequest=new AbortController();const version=++state.generation;
  if(!more){if(save)saveDraft();state.selected=id;storage('selected',id);loadDraft();show();}
  const data=await api('session?id='+encodeURIComponent(id)+(more&&before!==null?'&before='+before:''),undefined,{signal:sessionRequest.signal});if(version!==state.generation||state.selected!==id)return;
  const container=$('conversation'),height=container.scrollHeight,top=container.scrollTop;
  if(more){const old=[...container.childNodes];clearChat();transcript(data.messages,id);container.append(...old);container.scrollTop=top+container.scrollHeight-height;}
  else {clearChat();transcript(data.messages,id);for(const text of data.job_notices||[])notice(text);state.following=true;follow();}
  before=data.before;$('older-messages').hidden=before===null;$('session-title').textContent=data.metadata.title||items.find(s=>s.id===id)?.title||'当前会话';$('archive-session').textContent=data.metadata.archived?'恢复会话':'归档会话';$('model-name').textContent=data.model;metrics(data);
  for(const control of ['rename-session','archive-session','export-md','export-json'])$(control).disabled=false;
  if(!more)document.dispatchEvent(new CustomEvent('session-opened',{detail:id}));
}
export function newSession(){sessionRequest?.abort();state.generation++;saveDraft();state.selected=null;storage('selected',null);loadDraft();clearChat();$('session-title').textContent='新会话';$('older-messages').hidden=true;for(const c of ['rename-session','archive-session','export-md','export-json'])$(c).disabled=true;show();metrics({});}
export function initSessions(){
  $('prompt').addEventListener('input',saveDraft);$('verify-command').addEventListener('input',saveDraft);
  $('new-session').onclick=newSession;$('more-sessions').onclick=safely(()=>refreshSessions(true));$('older-messages').onclick=safely(()=>openSession(state.selected,{more:true}));
  let timer;$('session-search').oninput=()=>{clearTimeout(timer);listRequest?.abort();listGeneration++;timer=setTimeout(safely(()=>refreshSessions()),250);};
  $('show-archived').onchange=safely(async()=>{archived=$('show-archived').checked;await refreshSessions();});
  $('rename-session').onclick=()=>{$('rename-value').value=$('session-title').textContent;$('rename-dialog').showModal();};
  $('rename-form').onsubmit=safely(async e=>{e.preventDefault();await api('session/edit',{id:state.selected,title:$('rename-value').value});$('rename-dialog').close();await refreshSessions();await openSession(state.selected);});
  $('archive-session').onclick=safely(async()=>{const target=state.selected;await api('session/edit',{id:target,archived:$('archive-session').textContent!=='恢复会话'});newSession();await refreshSessions();});
  for(const format of ['md','json'])$('export-'+format).onclick=safely(async()=>{const data=await api(`export?id=${state.selected}&format=${format}`);const url=URL.createObjectURL(new Blob([data.content],{type:'text/plain;charset=utf-8'})),a=node('a');a.href=url;a.download=data.filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  $('clear-draft').onclick=()=>{storage('draft:'+(state.selected||'new'),null);loadDraft();};
  window.addEventListener('beforeunload',saveDraft);
}
