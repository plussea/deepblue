import {api,state,banner} from './api.js';
let active=null,generation=0;
export function disconnect(){generation++;active?.abort();active=null;}
export async function connect(jobId,onPacket,onStopped,{snapshot=true}={}){
  disconnect();const version=generation;let cursor=0,failures=0,fallback=false;
  while(version===generation){active=new AbortController();let timer;
    try {
      if(fallback){const packet=await api(`events?job_id=${jobId}&after=${cursor}&snapshot=${snapshot?1:0}`,undefined,{signal:active.signal});if(failures)banner();failures=0;await onPacket(packet);cursor=packet.sequence;snapshot=false;if(packet.finished)return;await new Promise(r=>setTimeout(r,650));continue;}
      timer=setTimeout(()=>active?.abort(),30000);
      const response=await fetch(`/api/stream?job_id=${jobId}&after=${cursor}&snapshot=${snapshot?1:0}`,{headers:{'X-Deepblue-Token':state.token},signal:active.signal});
      if(!response.ok){const error=Error((await response.json()).error||'连接失败');error.status=response.status;throw error;}
      if(!response.body?.getReader){fallback=true;continue;}
      const reader=response.body.getReader(),decoder=new TextDecoder();let buffer='';
      try {while(version===generation){const {value,done}=await reader.read();if(done)break;buffer+=decoder.decode(value,{stream:true});let split;while((split=buffer.indexOf('\n\n'))>=0){const frame=buffer.slice(0,split);buffer=buffer.slice(split+2);if(!frame.startsWith('data: '))continue;const packet=JSON.parse(frame.slice(6));if(failures)banner();await onPacket(packet);cursor=packet.sequence;snapshot=false;failures=0;if(packet.finished)return;}}}
      finally {await reader.cancel().catch(()=>{});}
    }catch(error){if(version!==generation)return;failures++;if((error.status&&error.status<500)||failures>=5){banner('连接已停止：'+error.message+'。可点击重新连接；任务不会自动重新提交。');onStopped();return;}banner(`连接中断，正在重新连接（${failures}/5）…`);if(failures>=2)fallback=true;await new Promise(r=>setTimeout(r,Math.min(4000,500*2**failures)));}
    finally{clearTimeout(timer);}
  }
}
