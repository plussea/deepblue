import {$,state,api,node,banner,safely} from './api.js';
import {showLog,markdown} from './render.js';
let folder='',previewPath='',fileText='',fileVersion=0,listVersion=0;
const openFiles=new Map(), expanded=new Set(), children=new Map();
let markdownMode=true;
function drawPreview(line=1){const md=/\.md$/i.test(previewPath);$('markdown-toggle').hidden=!md;$('markdown-toggle').textContent=markdownMode?'源码':'预览';$('preview-markdown').hidden=!md||!markdownMode;$('preview-code').hidden=md&&markdownMode;if(md&&markdownMode)markdown($('preview-markdown'),fileText,previewPath);else drawLines(line);}

function fileTabs(){const tabs=$('file-tabs');tabs.replaceChildren();const browse=node('button',!previewPath?'active':'','目录');browse.setAttribute('role','tab');browse.setAttribute('aria-selected',String(!previewPath));browse.onclick=()=>{fileVersion++;previewPath='';$('file-preview').hidden=true;$('file-browser').hidden=false;fileTabs();};tabs.append(browse);for(const path of openFiles.keys()){const tab=node('button',previewPath===path?'active':'',path.split('/').at(-1));tab.title=path;tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(previewPath===path));tab.onclick=safely(()=>preview(/\.md$/i.test(path)?path:path+'#L'+openFiles.get(path)));tabs.append(tab);}}
export async function preview(target){const match=target.match(/^(.*?)(?:#L?(\d+)|:(\d+))?$/);const path=decodeURIComponent(match[1]),line=Number(match[2]||match[3]||1);const version=++fileVersion;const data=await api('file?path='+encodeURIComponent(path));if(version!==fileVersion)return;previewPath=path;fileText=data.content;openFiles.set(path,line);if(openFiles.size>12)openFiles.delete(openFiles.keys().next().value);$('preview-name').textContent=path+(data.truncated?'（截断）':'');$('preview-name').title=path;$('file-preview').hidden=false;$('file-browser').hidden=true;$('file-line').value=line;$('file-find').value='';fileTabs();const workspace=document.querySelector('.workspace');workspace.classList.remove('no-inspector');workspace.classList.add('show-inspector');selectPanel('files-panel');markdownMode=!(match[2]||match[3]);drawPreview(line);}
function drawLines(line=1){if(previewPath)openFiles.set(previewPath,line);const pre=$('preview-code');pre.replaceChildren();fileText.split('\n').forEach((text,i)=>{const row=node('span','source-line'+(i+1===line?' selected-line':''));row.id='source-line-'+(i+1);row.append(node('span','line-number',String(i+1)),document.createTextNode(text||' '));pre.append(row);});$('source-line-'+line)?.scrollIntoView({block:'nearest'});}
function display(items,tree=false){const list=$('files');list.replaceChildren();
  function append(entries,depth){for(const item of entries){const b=node('button');b.title=item.path;b.style.paddingLeft=(8+depth*18)+'px';b.dataset.path=item.path;b.classList.toggle('selected',item.path===previewPath);
    b.append(node('span','file-icon',item.directory?(expanded.has(item.path)?'⌄':'›'):'·'),node('span','file-name',tree?item.name:item.path));
    if(item.directory)b.setAttribute('aria-expanded',String(expanded.has(item.path)));
    if(item.restricted){b.disabled=true;b.title+='（Git 元数据不可预览）';}
    b.onclick=safely(async()=>{if(!item.directory)return preview(item.path);if(expanded.has(item.path))expanded.delete(item.path);else{children.set(item.path,await api('files?path='+encodeURIComponent(item.path)));expanded.add(item.path);}display(children.get('')||[],true);});list.append(b);
    if(tree&&item.directory&&expanded.has(item.path))append(children.get(item.path)||[],depth+1);
  }}append(items,0);
}
export async function listFiles(){const version=++listVersion;const items=await api('files?path=');if(version!==listVersion)return;children.set('',items);for(const path of [...expanded]){try{children.set(path,await api('files?path='+encodeURIComponent(path)));}catch{expanded.delete(path);}}if(version!==listVersion)return;folder='';$('folder-path').textContent='项目文件';$('parent-folder').hidden=true;display(items,true);}
export function selectPanel(id){for(const b of document.querySelectorAll('[data-panel]'))b.classList.toggle('active',b.dataset.panel===id);for(const p of ['files-panel','details-panel','review-panel'])$(p).hidden=p!==id;}
export async function review(){const selected=state.selected;const data=await api('review'+(selected?'?id='+selected:''));if(selected!==state.selected)return;const list=$('review-content');list.replaceChildren();list.append(node('p','muted','工作区差异可能包含你已有的修改，不能全部归因于深蓝。'));
  if(!data.available)list.append(node('p','',data.reason||'Git 不可用；显示会话已记录的修改。'));
  const baseline=data.baseline?.files||[];const preexisting=new Set(baseline.map(x=>x.path));
  if(data.baseline){const details=node('details');details.append(node('summary','',`任务开始前已有 ${baseline.length} 个修改文件`),node('pre','',baseline.length?baseline.map(x=>x.path).join('\n'):'无已有修改'));list.append(details);}
  for(const file of data.files){const row=node('div','review-file');row.append(node('strong','',file.path),node('small','',preexisting.has(file.path)?'任务前已有修改':'当前修改（不代表独占归因）'));for(const [text,staged] of file.untracked?[['未跟踪内容',false]]:[['未暂存',false],['已暂存',true]]){const b=node('button','quiet-button',text);b.onclick=safely(async()=>{const result=await api(`diff?path=${encodeURIComponent(file.path)}&staged=${staged?1:0}`);await showLog(file.path,async()=>({content:result.content+(result.truncated?'\n[截断]':''),next:null}));});row.append(b);}list.append(row);}
  const details=node('details');details.append(node('summary','',`会话记录的文件操作 (${data.operations.length})`));for(const operation of data.operations)details.append(node('pre','',JSON.stringify(operation,null,2)));list.append(details);
}
export function initFiles(){
  fileTabs();
  $('markdown-toggle').onclick=()=>{markdownMode=!markdownMode;drawPreview(Number($('file-line').value)||1);};
  const workspace=document.querySelector('.workspace'),handle=$('inspector-resize');
  const width=value=>{const size=Math.max(300,Math.min(value,innerWidth-560));workspace.style.setProperty('--inspector-width',size+'px');handle.setAttribute('aria-valuenow',String(Math.round(size)));};
  handle.onpointerdown=e=>{if(innerWidth<=1180||workspace.classList.contains('expanded-inspector'))return;e.preventDefault();handle.setPointerCapture(e.pointerId);handle.onpointermove=event=>width(innerWidth-event.clientX);};
  handle.onpointerup=()=>{handle.onpointermove=null;};handle.onlostpointercapture=()=>{handle.onpointermove=null;};
  handle.onkeydown=e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();width($('inspector').getBoundingClientRect().width+(e.key==='ArrowLeft'?32:-32));}};
  const expand=()=>{const expanded=workspace.classList.toggle('expanded-inspector');$('expand-inspector').textContent=expanded?'⤡':'⤢';$('expand-inspector').title=expanded?'还原面板':'放大面板';$('expand-inspector').setAttribute('aria-label',$('expand-inspector').title);};
  $('expand-inspector').onclick=expand;
  $('hide-inspector').onclick=()=>{if(workspace.classList.contains('expanded-inspector'))expand();workspace.classList.remove('show-inspector');workspace.classList.add('no-inspector');};
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&workspace.classList.contains('expanded-inspector'))expand();});

  $('refresh-files').onclick=safely(async()=>{await listFiles();if(previewPath)await preview(previewPath);});$('parent-folder').onclick=safely(()=>listFiles(folder.split('/').slice(0,-1).join('/')));$('close-preview').onclick=safely(async()=>{fileVersion++;openFiles.delete(previewPath);previewPath='';$('file-preview').hidden=true;$('file-browser').hidden=false;fileTabs();const next=[...openFiles.keys()].at(-1);if(next)await preview(next+'#L'+openFiles.get(next));});
  $('file-line').onchange=()=>{markdownMode=false;drawPreview(Math.max(1,Number($('file-line').value)));};$('file-find').oninput=()=>{const at=fileText.toLowerCase().indexOf($('file-find').value.toLowerCase());if(at>=0){markdownMode=false;drawPreview(fileText.slice(0,at).split('\n').length);}};
  let timer;$('file-search').oninput=()=>{clearTimeout(timer);const version=++listVersion;timer=setTimeout(safely(async()=>{const query=$('file-search').value;if(!query)return listFiles();const data=await api('file-search?q='+encodeURIComponent(query));if(version===listVersion){display(data.items);$('folder-path').textContent=data.truncated?'搜索结果（有上限）':'搜索结果';}}),200);};
  $('insert-reference').onclick=()=>{if(!previewPath)return;const ref='@'+JSON.stringify(previewPath);$('prompt').setRangeText(ref,$('prompt').selectionStart,$('prompt').selectionEnd,'end');$('prompt').dispatchEvent(new Event('input'));$('prompt').focus();};
  let mentionVersion=0;$('prompt').addEventListener('input',safely(async()=>{const version=++mentionVersion,text=$('prompt').value.slice(0,$('prompt').selectionStart),match=text.match(/@([^\s@"]*)$/),menu=$('mentions');menu.replaceChildren();menu.hidden=!match;if(!match)return;const data=await api('file-search?q='+encodeURIComponent(match[1]));if(version!==mentionVersion)return;for(const item of data.items.slice(0,8)){const b=node('button','',item.path);b.type='button';b.onclick=()=>{const end=$('prompt').selectionStart;$('prompt').setRangeText('@'+JSON.stringify(item.path)+' ',end-match[0].length,end,'end');menu.hidden=true;$('prompt').dispatchEvent(new Event('input'));$('prompt').focus();};menu.append(b);}}));
  document.addEventListener('open-file',safely(e=>preview(e.detail)));
  for(const b of document.querySelectorAll('[data-panel]'))b.onclick=safely(async()=>{selectPanel(b.dataset.panel);if(b.dataset.panel==='review-panel')await review();});$('refresh-review').onclick=safely(review);
}
