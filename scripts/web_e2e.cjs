/* Run: DEEPBLUE_PYTHON=<python> PLAYWRIGHT_MODULE=<module path> node scripts/web_e2e.cjs */
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const root=path.resolve(__dirname,'..');
const server=spawn(process.env.DEEPBLUE_PYTHON||'python',['scripts/web_e2e_server.py'],{cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'src'),PYTHONIOENCODING:'utf-8'},windowsHide:true,stdio:['ignore','pipe','pipe']});
let errors='';server.stderr.on('data',chunk=>errors+=chunk);
let browser, debugPage;
const checks=[];
async function check(name,fn){await fn();checks.push(name);console.log('PASS '+name);}
(async()=>{
  const info=await new Promise((resolve,reject)=>{let output='';const timer=setTimeout(()=>reject(Error('server startup timeout '+errors)),20000);server.stdout.on('data',chunk=>{output+=chunk;if(output.includes('\n')){clearTimeout(timer);try{resolve(JSON.parse(output.split('\n')[0]));}catch(e){reject(e);}}});server.once('exit',code=>reject(Error('server exited '+code+' '+errors)));});
  browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1000},permissions:['clipboard-read','clipboard-write']});
  const page=await context.newPage();debugPage=page;const pageErrors=[];page.on('pageerror',e=>pageErrors.push(e.message));
  await page.goto(info.url);await page.waitForFunction(()=>document.querySelector('#version').textContent.startsWith('v'));
  await check('DeepSeek 内存设置和连接测试',async()=>{await page.click('#settings-open');await page.fill('#setting-api_key','browser-fixture-secret');await page.click('#settings-save');await page.waitForFunction(()=>document.querySelector('#settings-result').textContent.includes('已保存'));assert.equal(await page.inputValue('#setting-api_key'),'');await page.click('#settings-test');await page.waitForFunction(()=>document.querySelector('#settings-result').textContent.includes('连接成功'));await page.click('[data-close="settings-dialog"]');assert.equal(await page.isDisabled('#send'),false);});
  await check('草稿刷新恢复、历史分页、重命名',async()=>{await page.fill('#prompt','draft preserved');await page.reload();await page.waitForFunction(()=>document.querySelector('#prompt').value==='draft preserved');await page.getByRole('button',{name:'历史会话',exact:true}).click();await page.waitForSelector('#older-messages:not([hidden])');const count=await page.locator('.message').count();await page.click('#older-messages');await page.waitForFunction(n=>document.querySelectorAll('.message').length>n,count);await page.locator('.session-menu summary').click();await page.click('#rename-session');await page.fill('#rename-value','重命名历史');await page.click('#rename-form button[type=submit]');await page.waitForFunction(()=>document.querySelector('#session-title').textContent==='重命名历史');await page.click('#new-session');assert.equal(await page.inputValue('#prompt'),'draft preserved');});
  await check('双击发送幂等与安全 Markdown',async()=>{await page.fill('#prompt','normal render');await page.locator('#send').evaluate(e=>{e.click();e.click();});await page.waitForFunction(()=>document.querySelector('#run-status').textContent.includes('已结束'),{},{timeout:20000});assert.equal(await page.locator('.message.user').count(),1);assert.equal(await page.locator('.message-body table').count(),1);assert.equal(await page.evaluate(()=>window.injected),undefined);assert.equal(await page.locator('a[href^="javascript:"]').count(),0);await page.getByRole('button',{name:'复制',exact:true}).click();assert.equal(await page.evaluate(()=>navigator.clipboard.readText()),'print("深蓝")');});
  await check('任务状态展示与刷新恢复',async()=>{assert.ok((await page.textContent('#task-state-detail')).includes('normal render'));await page.reload();await page.waitForFunction(()=>document.querySelector('#task-state-detail').textContent.includes('normal render'));assert.ok((await page.textContent('#task-state-detail')).includes('finished'));});
  await check('文件链接、行号、引用与内容搜索',async()=>{await page.getByText('打开文件',{exact:true}).click();await page.waitForSelector('#source-line-2.selected-line');await page.click('#insert-reference');assert.match(await page.inputValue('#prompt'),/@"hello.py"/);await page.getByRole('tab',{name:'目录',exact:true}).click();await page.fill('#file-search','hello');await page.waitForFunction(()=>document.querySelector('#folder-path').textContent==='搜索结果');await page.getByRole('tab',{name:'hello.py',exact:true}).click();await page.fill('#file-find','print');assert.equal(await page.locator('#source-line-2.selected-line').count(),1);});
  await check('文件标签切换、整高预览、拖动与放大还原',async()=>{
    assert.ok((await page.locator('#preview-code').boundingBox()).height>600);
    await page.getByRole('tab',{name:'目录',exact:true}).click();await page.fill('#file-search','');
    await page.locator('#files button').filter({hasText:'notes.py'}).click();await page.waitForFunction(()=>document.querySelector('#preview-name').textContent==='notes.py');
    await page.getByRole('tab',{name:'hello.py',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#preview-name').textContent==='hello.py');
    const before=await page.locator('#inspector').boundingBox();const handle=await page.locator('#inspector-resize').boundingBox();
    await page.mouse.move(handle.x+4,handle.y+200);await page.mouse.down();await page.mouse.move(handle.x-140,handle.y+200);await page.mouse.up();
    assert.ok((await page.locator('#inspector').boundingBox()).width>before.width+100);
    await page.click('#expand-inspector');assert.ok((await page.locator('#preview-code').boundingBox()).width>1300);
    await page.screenshot({path:path.join(info.output,'file-expanded.png')});await page.keyboard.press('Escape');
    await page.click('#close-preview');await page.waitForFunction(()=>document.querySelector('#preview-name').textContent==='notes.py');
    assert.equal(await page.getByRole('tab',{name:'hello.py',exact:true}).count(),0);
    await page.click('#close-preview');assert.equal(await page.isVisible('#file-browser'),true);
  });
  await check('长输出不强制滚动、断线刷新与停止',async()=>{await page.fill('#prompt','slow stream');await page.click('#send');await page.waitForFunction(()=>document.querySelector('#conversation').scrollHeight>document.querySelector('#conversation').clientHeight+300);await page.locator('#conversation').evaluate(e=>{e.scrollTop=0;e.dispatchEvent(new Event('scroll'));});await page.waitForTimeout(700);assert.equal(await page.locator('#conversation').evaluate(e=>e.scrollTop),0);await context.setOffline(true);await page.waitForTimeout(700);await context.setOffline(false);await page.reload();await page.waitForSelector('#stop:not([hidden])');await page.waitForFunction(()=>document.querySelector('#conversation').textContent.includes('第 '));await page.click('#stop');await page.waitForFunction(()=>document.querySelector('#run-status').textContent.includes('已取消'),{},{timeout:20000});});
  await check('归档恢复与导出',async()=>{const title=await page.textContent('#session-title');const download=page.waitForEvent('download');await page.locator('.session-menu summary').click();await page.click('#export-json');const item=await download;await item.saveAs(path.join(info.output,'export.json'));assert.ok(JSON.parse(fs.readFileSync(path.join(info.output,'export.json'),'utf8')).messages.length>1);await page.locator('.session-menu summary').click();await page.click('#archive-session');await page.check('#show-archived');await page.getByRole('button',{name:title,exact:true}).click();await page.waitForFunction(()=>document.querySelector('#archive-session').textContent==='恢复会话');await page.locator('.session-menu summary').click();await page.click('#archive-session');await page.uncheck('#show-archived');});
  await check('双标签页错误任务 ID 不会取消当前任务',async()=>{await page.fill('#prompt','slow two tabs');await page.click('#send');await page.waitForSelector('#stop:not([hidden])');const second=await context.newPage();await second.goto(info.url);await second.waitForSelector('#stop:not([hidden])');const code=await second.evaluate(async()=>{const b=await(await fetch('/api/bootstrap')).json();return(await fetch('/api/cancel',{method:'POST',headers:{'Content-Type':'application/json','X-Deepblue-Token':b.token},body:JSON.stringify({job_id:'f'.repeat(32)})})).status;});assert.equal(code,400);assert.equal(await page.isVisible('#stop'),true);await second.click('#stop');await page.waitForFunction(()=>document.querySelector('#run-status').textContent.includes('已取消'),{},{timeout:20000});await second.close();});
  await check('实时工具日志与可点击验收证据',async()=>{await page.fill('#prompt','tool-fixture');await page.locator('.verification-config summary').click();await page.fill('#verify-command',info.verify);await page.click('#send');await page.click('[data-panel="details-panel"]');await page.getByRole('button',{name:'shell · 实时日志',exact:true}).click({timeout:15000});await page.waitForFunction(()=>document.querySelector('#log-content').textContent.includes('live log'));await page.click('[data-close="log-dialog"]');await page.waitForFunction(()=>document.querySelector('#run-status').textContent.includes('指定检查通过'),{},{timeout:20000});await page.locator('#evidence-list summary').first().click();await page.getByRole('button',{name:'查看日志',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#log-content').textContent.includes('verification evidence'));await page.click('[data-close="log-dialog"]');});
  await check('Git 已有修改、暂存与未暂存差异',async()=>{await page.click('[data-panel="review-panel"]');await page.waitForFunction(()=>document.querySelector('#review-content').textContent.includes('任务开始前已有 1 个修改文件'));await page.getByRole('button',{name:'未暂存',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#log-content').textContent.includes('+print("unstaged")'));await page.click('[data-close="log-dialog"]');await page.getByRole('button',{name:'已暂存',exact:true}).click();await page.waitForFunction(()=>document.querySelector('#log-content').textContent.includes('+print("staged")'));await page.click('[data-close="log-dialog"]');await page.locator('.verification-config summary').click();await page.screenshot({path:path.join(info.output,'review.png')});});
  await check('窄屏可操作并且没有横向溢出',async()=>{await page.setViewportSize({width:390,height:844});await page.click('#toggle-sessions');await page.click('#settings-open');await page.waitForSelector('#settings-dialog[open]');await page.click('[data-close="settings-dialog"]');await page.click('#toggle-sessions');assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));await page.screenshot({path:path.join(info.output,'mobile.png')});await page.setViewportSize({width:1440,height:1000});await page.screenshot({path:path.join(info.output,'desktop.png')});});
  for(const [prompt,expected] of [['fault-401','DeepSeek HTTP 401'],['fault-429','DeepSeek HTTP 429'],['fault-timeout','请求超时'],['fault-disconnect','[DONE] 前结束']]){
    await check('受控故障：'+prompt,async()=>{
      await page.click('#new-session');
      if(prompt==='fault-timeout'){await page.click('#settings-open');await page.fill('#setting-timeout','0.2');await page.click('#settings-save');await page.waitForFunction(()=>document.querySelector('#settings-result').textContent.includes('已保存'));await page.click('[data-close="settings-dialog"]');}
      await page.fill('#prompt',prompt);await page.click('#send');
      await page.waitForFunction(()=>document.querySelector('#run-status').textContent.includes('执行异常'),null,{timeout:20000});
      assert.ok((await page.textContent('#conversation')).includes(expected));assert.ok(!(await page.textContent('body')).includes('browser-fixture-secret'));
      await page.reload();await page.waitForFunction(text=>document.querySelector('#conversation').textContent.includes(text),expected);assert.ok(!(await page.textContent('body')).includes('browser-fixture-secret'));
      if(prompt==='fault-timeout'){await page.click('#settings-open');await page.fill('#setting-timeout','30');await page.click('#settings-save');await page.waitForFunction(()=>document.querySelector('#settings-result').textContent.includes('已保存'));await page.click('[data-close="settings-dialog"]');}
      if(prompt==='fault-disconnect')assert.equal(fs.existsSync(path.join(info.output,'project','must-not-exist.txt')),false);
    });
  }
  await check('工具失败与模型连接错误分别展示',async()=>{await page.click('#new-session');await page.fill('#prompt','fault-tool');await page.click('#send');await page.waitForFunction(()=>document.querySelector('#run-status').textContent.includes('已结束'),null,{timeout:20000});assert.ok((await page.textContent('#conversation')).includes('未完成'));assert.equal(await page.isVisible('#banner'),false);});
  await check('窄屏文件阅读与关闭返回对话',async()=>{
    await page.setViewportSize({width:390,height:844});await page.click('#toggle-files');await page.click('[data-panel="files-panel"]');
    await page.locator('#files button').filter({hasText:'hello.py'}).click();await page.waitForSelector('#preview-code:not([hidden])');
    assert.ok((await page.locator('#preview-code').boundingBox()).height>500);assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
    await page.click('#expand-inspector');await page.screenshot({path:path.join(info.output,'mobile-file.png')});
    await page.click('#hide-inspector');assert.equal(await page.isVisible('#inspector'),false);assert.equal(await page.isVisible('#prompt'),true);
    await page.setViewportSize({width:1440,height:1000});await page.click('#toggle-files');
  });
  await check('紧凑输入、Enter 发送、Shift 换行与输入法保护',async()=>{
    await page.click('#new-session');await page.fill('#prompt','line one');
    assert.ok((await page.locator('#composer').boundingBox()).height<130);
    await page.press('#prompt','Shift+Enter');await page.type('#prompt','line two');assert.equal(await page.inputValue('#prompt'),'line one\nline two');
    await page.locator('#prompt').evaluate(el=>el.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',isComposing:true,bubbles:true,cancelable:true})));
    assert.equal(await page.locator('.message.user').count(),0);
    await page.press('#prompt','Enter');await page.waitForFunction(()=>document.querySelector('#run-status').textContent.includes('已结束'));
    assert.equal(await page.locator('.message.user').count(),1);assert.equal(await page.inputValue('#prompt'),'');
    await page.click('#new-session');await page.screenshot({path:path.join(info.output,'compact-composer.png')});
  });
  await check('会话分叉不继承验收结果',async()=>{
    await page.fill('#prompt','fork source');await page.click('#send');await page.waitForFunction(()=>document.querySelector('#run-status').textContent.includes('已结束'));
    const count=await page.locator('#sessions button').count();await page.locator('.session-menu summary').click();await page.click('#fork-session');await page.click('#fork-form button[type="submit"], #fork-form button:not([type])');
    await page.waitForFunction(n=>document.querySelectorAll('#sessions button').length===n+1,count);
    assert.equal(await page.locator('.message.user').count(),1);assert.ok((await page.textContent('#run-status')).includes('未验证'));
  });
  await check('补充指令、后续队列编辑取消与自动执行',async()=>{
    await page.click('#new-session');await page.fill('#prompt','tool-fixture');await page.click('#send');await page.waitForSelector('#stop:not([hidden])');
    await page.waitForSelector('.tool-card.running-tool');
    await page.fill('#prompt','steering marker');await page.click('#steer');await page.waitForFunction(()=>document.querySelector('#prompt').value==='');
    await page.fill('#prompt','followup marker');await page.press('#prompt','Enter');await page.waitForFunction(()=>document.querySelector('#prompt').value==='');
    await page.fill('#prompt','cancel marker');await page.press('#prompt','Enter');await page.waitForFunction(()=>document.querySelector('#prompt').value==='');
    await page.locator('.session-menu summary').click();await page.click('#queue-open');await page.waitForSelector('#queue-dialog[open]');await page.screenshot({path:path.join(info.output,'message-queue.png')});
    await page.locator('.queue-item').evaluateAll(rows=>{const item=rows.find(r=>r.querySelector('textarea').value==='cancel marker');[...item.querySelectorAll('button')].find(b=>b.textContent==='取消').click();});
    await page.waitForFunction(()=>document.querySelector('#queue-list').textContent.includes('cancelled'));
    await page.locator('.queue-item textarea').evaluateAll(inputs=>{const input=inputs.find(e=>e.value==='followup marker');input.value='edited followup';input.dispatchEvent(new Event('input',{bubbles:true}));[...input.parentElement.querySelectorAll('button')].find(b=>b.textContent==='保存').click();});
    await page.click('[data-close="queue-dialog"]');
    await page.waitForFunction(()=>document.querySelector('#conversation').textContent.includes('edited followup')&&document.querySelector('#conversation').textContent.includes('steering marker')&&document.querySelector('#stop').hidden,null,{timeout:30000});
    assert.ok((await page.textContent('#conversation')).includes('steering marker'));
    assert.ok(!(await page.textContent('#conversation')).includes('cancel marker'));
  });
  await check('多项目路由、独立草稿与会话',async()=>{
    await page.fill('#prompt','primary project draft');await page.click('#projects-open');await page.waitForSelector('#projects-dialog[open]');await page.screenshot({path:path.join(info.output,'projects.png')});await page.fill('#project-path',path.join(info.output,'second-project'));await page.click('#project-add button');
    await page.waitForFunction(()=>document.querySelector('#project-name').textContent==='second-project');
    assert.equal(await page.inputValue('#prompt'),'');assert.equal(await page.locator('#sessions .session-item').count(),0);
    await page.fill('#prompt','other draft');await page.reload();await page.waitForFunction(()=>document.querySelector('#prompt').value==='other draft');
    await page.click('#projects-open');await page.getByRole('button',{name:'project',exact:true}).click();
    await page.waitForFunction(()=>document.querySelector('#prompt').value==='primary project draft');assert.ok(await page.locator('#sessions .session-item').count()>0);
  });
  await check('自定义命令目录、键盘补全与执行',async()=>{
    await page.click('#new-session');await page.fill('#prompt','/');
    await page.waitForSelector('#command-menu [data-kind=skill]');
    assert.ok(await page.locator('#command-menu [data-kind=command]').count()>0);
    await page.locator('#command-menu [data-kind=skill]').filter({hasText:'skill-creator'}).click();
    assert.equal(await page.inputValue('#prompt'),'/skill:skill-creator ');
    await page.fill('#prompt','/rev');
    await page.waitForSelector('#command-menu:not([hidden])');
    assert.ok((await page.textContent('#command-menu')).includes('/review'));
    await page.press('#prompt','Tab');assert.equal(await page.inputValue('#prompt'),'/review ');
    await page.fill('#prompt','/rev');await page.waitForSelector('#command-menu:not([hidden])');
    await page.press('#prompt','Escape');assert.equal(await page.isVisible('#command-menu'),false);
    await page.fill('#prompt','/review sample.py');await page.press('#prompt','Enter');
    await page.waitForFunction(()=>document.querySelector('#run-status').textContent.includes('已结束'),null,{timeout:20000});
    assert.ok((await page.textContent('#conversation')).includes('Review sample.py'));
    assert.ok((await page.textContent('#conversation')).includes('Inspect then verify.'));
  });
  await check('隐藏目录树与 Markdown 预览源码切换',async()=>{
    await page.click('[data-panel="files-panel"]');await page.getByRole('tab',{name:'目录',exact:true}).click();
    await page.fill('#file-search','');await page.click('#refresh-files');
    await page.locator('#files button[data-path=".hidden"]').click();
    await page.locator('#files button[data-path=".hidden/guide.md"]').click();
    await page.waitForSelector('#preview-markdown:not([hidden]) h1');
    assert.equal(await page.locator('#preview-markdown table').count(),1);
    assert.equal(await page.locator('#preview-markdown .code-block').count(),1);
    assert.equal(await page.evaluate(()=>window.injected),undefined);
    await page.click('#markdown-toggle');assert.equal(await page.isVisible('#preview-code'),true);
    await page.click('#markdown-toggle');assert.equal(await page.isVisible('#preview-markdown'),true);
    await page.screenshot({path:path.join(info.output,'markdown-preview.png')});
  });
  assert.deepEqual(pageErrors,[]);fs.writeFileSync(path.join(info.output,'report.json'),JSON.stringify({checks,errors:pageErrors},null,2));console.log(JSON.stringify({passed:checks.length,output:info.output}));
})().catch(async e=>{console.error(e);if(debugPage){console.error(await debugPage.evaluate(()=>({banner:document.querySelector('#banner').textContent,status:document.querySelector('#run-status').textContent,chat:document.querySelector('#conversation').textContent.slice(-600),height:document.querySelector('#conversation').scrollHeight,client:document.querySelector('#conversation').clientHeight})));await debugPage.screenshot({path:path.join(root,'.test-tmp','e2e-failure.png')});}process.exitCode=1;}).finally(async()=>{if(browser)await browser.close();server.kill();});
