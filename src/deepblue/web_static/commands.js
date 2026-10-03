import {$,api,banner} from './api.js';
export function initCommands(){
  const input=$('prompt'), menu=document.createElement('div');
  menu.className='command-menu';menu.hidden=true;menu.setAttribute('role','listbox');
  menu.id='command-menu';input.setAttribute('aria-controls',menu.id);
  input.parentNode.insertBefore(menu,input);
  let commands=[],matches=[],selected=0,request=0,opened=true;
  function close(){opened=false;menu.hidden=true;input.setAttribute('aria-expanded','false');}
  function choose(index){input.value='/'+(matches[index].kind==='skill'?'skill:':'')+matches[index].name+' ';close();input.dispatchEvent(new Event('input'));input.focus();}
  function render(){
    const match=input.value.match(/^\/([a-z0-9_:-]*)$/i);
    matches=match?commands.filter(c=>c.name.startsWith(match[1])||(c.kind==='skill'&&('skill:'+c.name).startsWith(match[1]))):[];
    menu.replaceChildren();selected=Math.min(selected,Math.max(0,matches.length-1));
    matches.forEach((c,i)=>{const b=document.createElement('button');b.type='button';b.setAttribute('role','option');b.setAttribute('aria-selected',String(i===selected));b.dataset.kind=c.kind;const badge=document.createElement('span');badge.className='capability-badge '+c.kind;badge.textContent=c.kind==='skill'?'◇ Skill':'/ Command';const label=document.createElement('span');label.textContent='/'+(c.kind==='skill'?'skill:':'')+c.name+'  '+c.description;b.append(badge,label);b.onclick=()=>choose(i);menu.append(b);});
    menu.hidden=!opened||!matches.length;input.setAttribute('aria-expanded',String(!menu.hidden));
  }
  input.addEventListener('input',async()=>{opened=true;render();if(!/^\/[a-z0-9_:-]*$/i.test(input.value))return;const id=++request;try{const data=await api('capabilities');if(id!==request)return;commands=[...data.commands.map(c=>({...c,kind:'command'})),...data.skills.map(c=>({...c,kind:'skill'}))];render();}catch(e){banner(e.message);}});
  input.addEventListener('keydown',e=>{if(menu.hidden||e.isComposing||e.keyCode===229)return;
    if(['ArrowDown','ArrowUp','Tab','Enter','Escape'].includes(e.key)){e.preventDefault();e.stopImmediatePropagation();}
    if(e.key==='ArrowDown'){selected=(selected+1)%matches.length;render();}
    if(e.key==='ArrowUp'){selected=(selected+matches.length-1)%matches.length;render();}
    if(e.key==='Tab'||e.key==='Enter')choose(selected);
    if(e.key==='Escape')close();
  },true);
  document.addEventListener('click',e=>{if(e.target!==input&&!menu.contains(e.target))close();});
}
