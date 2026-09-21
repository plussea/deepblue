export const $ = id => document.getElementById(id);
export const state = {token:'', project:'', selected:null, configured:false, busy:false, job:null, jobSession:null, stream:null, generation:0, following:true};
export const labels = {unverified:'未验证',not_applicable:'不适用',passed:'指定检查通过',failed:'失败',error:'执行异常',stale:'证据已过期',cancelled:'已取消',finished:'已结束',running:'执行中',completed:'任务结束',interrupted:'服务中断，需核对',unknown:'结果未知',cancelling:'正在停止',step_limit:'达到调用上限',context_limit:'上下文超限',incomplete:'回复不完整'};
export function banner(text='') {$('banner').textContent=text; $('banner').hidden=!text;}
export function node(tag, cls='', text) {const e=document.createElement(tag); e.className=cls; if(text!==undefined)e.textContent=text; return e;}
export async function api(path, body, options={}) {
  const controller=new AbortController();
  const relay=()=>controller.abort(); options.signal?.addEventListener('abort',relay,{once:true});
  if(options.signal?.aborted)controller.abort();
  const timer=setTimeout(()=>controller.abort(),options.timeout||15000);
  try {
    const response=await fetch('/api/'+path,{method:body===undefined?'GET':'POST',headers:{'X-Deepblue-Token':state.token,...(body===undefined?{}:{'Content-Type':'application/json'})},body:body===undefined?undefined:JSON.stringify(body),signal:controller.signal});
    const result=await response.json();
    if(!response.ok){const error=Error(result.error||'请求失败');error.status=response.status;throw error;}
    return result;
  } finally {clearTimeout(timer);options.signal?.removeEventListener('abort',relay);}
}
export function safely(action) {return (...args)=>{try{return Promise.resolve(action(...args)).catch(e=>{if(e.name!=='AbortError')banner(e.message);});}catch(e){banner(e.message);return Promise.resolve();}};}
export function storage(key, value) {try {const name='deepblue:'+state.project+':'+key;if(value===undefined)return JSON.parse(localStorage.getItem(name)||'null');if(value===null)localStorage.removeItem(name);else localStorage.setItem(name,JSON.stringify(value));}catch{}return null;}
