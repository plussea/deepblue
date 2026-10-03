import {$,state,api,node,safely,banner,storage} from './api.js';
import {saveDraft,refreshSessions,openSession} from './sessions.js';

let submitting=false,pending=null;
export async function enqueue(kind){
  if(submitting)return;pending ||= storage('pending-queue');const prompt=$('prompt').value.trim();if(!prompt||!state.job)return;
  const body={kind,prompt,job_id:state.job,verify:$('verify-command').value.trim()};
  if(pending&&JSON.stringify(pending.body)!==JSON.stringify(body))throw Error('上次入队结果待确认，请恢复原文重试。');
  pending ||= {body,request_id:crypto.randomUUID().replaceAll('-','')};submitting=true;storage('pending-queue',pending);
  try{await api('queue',{...body,request_id:pending.request_id});pending=null;storage('pending-queue',null);if($('prompt').value.trim()===prompt){$('prompt').value='';$('prompt').dispatchEvent(new Event('input'));saveDraft();}banner(kind==='steering'?'补充指令已入队，将在下一次模型请求前接收。':'后续任务已入队，可在“会话操作 → 消息队列”修改或取消。');}
  catch(error){if(error.status&&error.status<500){pending=null;storage('pending-queue',null);}throw error;}
  finally{submitting=false;}
}

async function queue(){const items=await api('queue'),list=$('queue-list');list.replaceChildren();if(!items.length)list.append(node('p','','暂无队列消息'));for(const item of items){const row=node('div','queue-item'),text=node('textarea');text.value=item.prompt;text.setAttribute('aria-label','队列消息');text.disabled=!['pending','held'].includes(item.status);row.append(node('div','',`${item.kind==='steering'?'补充指令':'后续任务'} · ${item.status}`),text);if(!text.disabled){const save=node('button','','保存'),cancel=node('button','','取消');save.onclick=safely(async()=>{await api('queue/edit',{id:item.id,prompt:text.value});await queue();});cancel.onclick=safely(async()=>{await api('queue/edit',{id:item.id,cancel:true});await queue();});row.append(save,cancel);if(item.kind==='follow-up'){const run=node('button','','现在执行');run.disabled=state.busy;run.onclick=safely(async()=>{await api('queue/run',{id:item.id});await queue();});row.append(run);}}list.append(row);}}

export function initWorkflows(){
  $('steer').onclick=safely(()=>enqueue('steering'));
  $('queue-open').onclick=safely(async()=>{await queue();$('queue-dialog').showModal();});$('queue-refresh').onclick=safely(queue);
  $('projects-open').onclick=safely(async()=>{const projects=await api('projects'),list=$('project-list');list.replaceChildren();for(const project of projects){const button=node('button','project-choice',project.name);button.title=project.path;button.disabled=project.id===state.projectId;button.onclick=()=>{saveDraft();location.assign('/?project='+encodeURIComponent(project.id));};list.append(button);}$('projects-dialog').showModal();});
  $('project-add').onsubmit=safely(async e=>{e.preventDefault();const project=await api('projects',{path:$('project-path').value});saveDraft();location.assign('/?project='+encodeURIComponent(project.id));});
  $('fork-session').onclick=()=>{if(!state.selected){banner('请先选择会话。');return;}$('fork-count').value='';$('fork-dialog').showModal();};
  $('fork-form').onsubmit=safely(async e=>{e.preventDefault();const count=$('fork-count').value;const result=await api('session/fork',{session_id:state.selected,...(count?{message_count:Number(count)}:{})});$('fork-dialog').close();await refreshSessions();await openSession(result.session_id);});
}
