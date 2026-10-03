import {initCommands} from './commands.js';
import {$,state,api,node,labels,banner,safely,storage} from './api.js';
import {clearChat,message,notice,renderEvent,metrics,follow} from './render.js';
import {connect,disconnect} from './connection.js';
import {initSessions,refreshSessions,openSession,loadDraft,saveDraft} from './sessions.js';
import {initFiles,listFiles} from './files.js';
import {initWorkflows,enqueue} from './workflows.js';

function setBusy(value){state.busy=value;$('send').disabled=!state.configured||(value&&!state.job);$('send').title=value?'加入后续任务队列（Enter）':'发送任务（Enter）';$('steer').hidden=!value||!state.job;$('stop').hidden=!value||!state.job;$('compact').disabled=value||!state.selected||!state.configured;$('retry').disabled=value||!state.selected||!state.configured;$('active-job').hidden=!value;$('active-job').textContent=value?'查看正在运行的任务':'';}
let lastMetrics=0;
async function packet(result){
  if(state.job!==result.job_id)return;
  const version=state.generation;
  state.jobSession=result.session_id;setBusy(!result.finished);$('run-status').textContent=labels[result.status]||'就绪';
  if(state.selected===result.session_id){
    if(result.snapshot){clearChat();notice('当前任务快照；完整历史可在任务结束后或“加载历史”中查看。');for(const e of result.snapshot)renderEvent(e);}
    else for(const e of result.events)renderEvent(e);
    if(result.snapshot||result.events.length)follow();
    if(!result.finished&&Date.now()-lastMetrics>2500){lastMetrics=Date.now();const selected=state.selected;api('session?id='+selected+'&limit=1').then(data=>{if(selected===state.selected)metrics(data);}).catch(()=>{});}
  }
  if(result.finished){$('reconnect').hidden=true;banner(result.status==='interrupted'?'上次服务中断；未确认操作不会重放，请检查恢复详情。':'');await refreshSessions();if(version!==state.generation||state.job!==result.job_id)return;if(state.selected===result.session_id)await openSession(result.session_id,{save:false});await listFiles();}
}
async function attach(jobId){state.job=jobId;storage('job',jobId);$('reconnect').hidden=true;await connect(jobId,packet,()=>{$('reconnect').hidden=false;});}
async function start(action='run'){
  if(state.busy){if(action==='run')return enqueue('follow-up');return;}const prompt=$('prompt').value.trim();if(action==='run'&&!prompt)return;
  const body={action,session_id:state.selected,prompt,verify:$('verify-command').value.trim()};
  let pending=storage('pending');
  if(pending){const lookup=await api('request?id='+pending.request_id);if(lookup.job_id){state.selected=lookup.session_id;storage('pending',null);await attach(lookup.job_id);return;}if(JSON.stringify({...pending,request_id:undefined})!==JSON.stringify(body))throw Error('上次提交结果待确认。请恢复原草稿后重新发送；内容不会自动重复执行。');}
  else pending={...body,request_id:crypto.randomUUID().replaceAll('-','')};
  storage('pending',pending);saveDraft();state.job=null;setBusy(true);state.generation++;disconnect();banner();
  try {const result=await api('run',pending, {timeout:30000});storage('pending',null);const previous=state.selected;state.selected=result.session_id;state.jobSession=result.session_id;storage('selected',result.session_id);
    if(action==='run'){storage('draft:'+(previous||'new'),null);$('prompt').value='';$('prompt').dispatchEvent(new Event('input'));saveDraft();}
    clearChat();state.following=true;await refreshSessions();await attach(result.job_id);
  }catch(error){setBusy(false);if(error.status&&error.status<500)storage('pending',null);$('reconnect').hidden=false;throw error;}
}
async function reconnect(){const pending=storage('pending');if(pending){const lookup=await api('request?id='+pending.request_id);if(!lookup.job_id){banner('服务器没有该请求记录，原草稿已保留。点击发送可用相同请求 ID 重试。');return;}storage('pending',null);state.selected=lookup.session_id;state.job=lookup.job_id;}if(state.job)await attach(state.job);}
let profileId='default',profileConfigured=false,settingsRevision=0;
function fillSettings(data){settingsRevision++;profileConfigured=data.configured;profileId=data.profile_id;const select=$('setting-profile');select.replaceChildren();for(const p of data.profiles){const o=document.createElement('option');o.value=p.id;o.textContent=p.name;select.append(o);}select.value=profileId;$('setting-name').value=data.profiles.find(p=>p.id===profileId)?.name||profileId;
for(const field of ['model','base_url','timeout','api_key']){const input=$('setting-'+field);input.value=field==='api_key'?'':data[field];input.disabled=!['saved','startup','memory'].includes(data.sources[field]);$('source-'+field).textContent=input.disabled?data.sources[field]:(field==='api_key'?(data.configured?'已保存（默认隐藏）；留空保持不变':'未配置'):'已保存');}
$('setting-provider').value=data.provider;$('setting-token_parameter').value=data.token_parameter;$('setting-include_usage').checked=data.include_usage;$('setting-api_key').type='password';$('key-toggle').textContent='显示 Key';}
async function showSettings(){fillSettings(await api('config'));$('settings-result').textContent='切换地址会清除未重新填写的 Key；运行中任务不变。';$('settings-dialog').showModal();}
async function saveSettings(clearKey=false){const data={profile_id:profileId,name:$('setting-name').value,provider:$('setting-provider').value,token_parameter:$('setting-token_parameter').value,include_usage:$('setting-include_usage').checked};for(const field of ['model','base_url','timeout','api_key']){const input=$('setting-'+field);if(!input.disabled&&(field!=='api_key'||input.value||clearKey))data[field]=field==='timeout'?Number(input.value):input.value;}if(clearKey)data.api_key='';const result=await api('config',data);fillSettings(result);state.configured=result.configured;setBusy(state.busy);$('settings-result').textContent='已保存，下次启动自动加载。';if(state.configured)banner();}
$('setting-profile').onchange=safely(async()=>{settingsRevision++;const result=await api('config',{profile_id:$('setting-profile').value});fillSettings(result);state.configured=result.configured;setBusy(state.busy);});
$('profile-new').onclick=()=>{profileId='api-'+crypto.randomUUID().slice(0,8);fillSettings({profile_id:profileId,profiles:[{id:profileId,name:'新的 API'}],model:'',base_url:'https://api.example.com/v1',timeout:30,provider:'openai-compatible',token_parameter:'max_tokens',include_usage:true,configured:false,sources:{model:'saved',base_url:'saved',timeout:'saved',api_key:'saved'}});};
$('key-toggle').onclick=safely(async()=>{const input=$('setting-api_key');if(input.type==='text'){input.type='password';$('key-toggle').textContent='显示 Key';return;}if(!input.value&&profileConfigured){const revision=settingsRevision;const result=await api('config/key',{});if(revision!==settingsRevision||!$('settings-dialog').open)return;input.value=result.api_key;}input.type='text';$('key-toggle').textContent='隐藏 Key';});
$('settings-dialog').addEventListener('close',()=>{settingsRevision++;$('setting-api_key').value='';$('setting-api_key').type='password';});
initSessions();initFiles();initWorkflows();initCommands();
$('composer').onsubmit=safely(e=>{e.preventDefault();return start();});$('prompt').onkeydown=safely(e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();return start();}});
$('compact').onclick=safely(()=>start('compact'));$('retry').onclick=safely(()=>start('retry'));$('reconnect').onclick=safely(reconnect);
$('stop').onclick=safely(async()=>{await api('cancel',{job_id:state.job});banner('已请求停止指定任务；停止不回滚已经发生的修改。');});
$('active-job').onclick=safely(async()=>{if(state.jobSession)await openSession(state.jobSession);});
document.addEventListener('session-opened',()=>{setBusy(state.busy);if(state.busy&&state.selected===state.jobSession)attach(state.job).catch(e=>banner(e.message));});
$('conversation').onscroll=()=>{const e=$('conversation');state.following=e.scrollHeight-e.scrollTop-e.clientHeight<90;if(state.following)$('new-messages').hidden=true;};$('new-messages').onclick=()=>{state.following=true;follow();};
$('toggle-files').onclick=()=>{const w=document.querySelector('.workspace');if(innerWidth<=1180)w.classList.toggle('show-inspector');else w.classList.toggle('no-inspector');};$('toggle-sessions').onclick=()=>document.querySelector('.workspace').classList.toggle('show-sessions');
for(const b of document.querySelectorAll('[data-prompt]'))b.onclick=()=>{$('prompt').value=b.dataset.prompt;saveDraft();$('prompt').focus();};
$('settings-open').onclick=safely(showSettings);$('settings-save').onclick=safely(()=>saveSettings());$('settings-clear-key').onclick=safely(()=>saveSettings(true));$('settings-test').onclick=safely(async()=>{$('settings-test').disabled=true;$('settings-result').textContent='正在测试（会发起一次模型请求）…';try{await saveSettings();const result=await api('config/test',{}, {timeout:310000});$('settings-result').textContent='连接成功：'+result.model;}catch(e){$('settings-result').textContent=e.message;}finally{$('settings-test').disabled=false;}});
for(const b of document.querySelectorAll('[data-close]'))b.onclick=()=>$(b.dataset.close).close();
const sessionMenu=document.querySelector('.session-menu');
document.addEventListener('click',e=>{if(!sessionMenu.contains(e.target)||e.target.closest('.session-menu-items button'))sessionMenu.open=false;});
document.addEventListener('keydown',e=>{if(e.key==='Escape')sessionMenu.open=false;});

(async()=>{try{const info=await api('bootstrap');state.token=info.token;state.projectId=info.project_id;state.configured=info.configured;state.project=info.cwd;const project=info.cwd.split(/[\\/]/).filter(Boolean).at(-1);for(const id of ['project-name','breadcrumb-project'])$(id).textContent=project;$('project-name').title=info.cwd;$('model-name').textContent=info.model;$('version').textContent='v'+info.version;if(!info.configured)banner('尚未配置 DeepSeek Key。点击“设置”后可执行任务。');await Promise.all([refreshSessions(),listFiles()]);loadDraft();
  const selected=storage('selected');if(selected){try{await openSession(selected,{save:false});}catch{state.selected=null;loadDraft();}}
  const running=await api('events');if(running.job_id&&!running.finished){state.job=running.job_id;state.jobSession=running.session_id;setBusy(true);if(!selected)state.selected=running.session_id;await attach(running.job_id);}else{setBusy(false);if(running.status==='interrupted')banner('上次服务中断。选择会话查看恢复核对；不会自动重跑。');}
  if(storage('pending')){$('reconnect').hidden=false;banner('存在待确认的提交，请点击重新连接核对。');}
}catch(e){banner(e.message);}})();

function resizePrompt(){const input=$('prompt');input.style.height='auto';input.style.height=Math.min(160,Math.max(48,input.scrollHeight))+'px';}
$('prompt').addEventListener('input',resizePrompt);
document.addEventListener('draft-loaded',resizePrompt);
new ResizeObserver(resizePrompt).observe($('composer'));
const options=document.querySelector('.verification-config');
document.addEventListener('click',e=>{if(!options.contains(e.target))options.open=false;});
document.addEventListener('keydown',e=>{if(e.key==='Escape')options.open=false;});

// Discover a follow-up dispatched after the previous SSE stream ended.
setInterval(async()=>{if(!state.token||state.busy)return;try{const job=await api('events');if(state.job&&job.job_id&&job.job_id!==state.job){state.jobSession=job.session_id;await attach(job.job_id);}}catch{}},2000);
