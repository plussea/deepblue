/* Explicit, paid browser acceptance against an already configured local Web server.
 * No credential is read, copied, or persisted. Run with --run; see README.
 */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs');
const path=require('node:path');
const assert=require('node:assert/strict');
const {randomUUID,createHash}=require('node:crypto');
const {spawnSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const url=process.env.DEEPBLUE_WEB_URL||'http://127.0.0.1:30142';
const python=process.env.DEEPBLUE_PYTHON||'python';
const shellQuote=s=>"'"+s.replaceAll("'",process.platform==='win32'?"''":"'\"'\"'")+"'";
const pythonShell=(process.platform==='win32'?'& ':'')+shellQuote(python.replaceAll('\\','/'));
if(!process.argv.includes('--run')){console.log('显式收费验收：先在 Web 配置 Key，再传入 --run。');process.exit(2);}
assert.equal(new URL(url).hostname,'127.0.0.1','只测试本机服务');
const resume=process.argv.includes('--resume')?process.argv[process.argv.indexOf('--resume')+1]:null;
if(resume)assert.match(resume,/^\.test-tmp\/web-live-[a-f0-9]{8}$/);
const rel=resume||'.test-tmp/web-live-'+randomUUID().slice(0,8);
const output=path.join(root,rel);fs.mkdirSync(output,{recursive:true});
const report=resume?JSON.parse(fs.readFileSync(path.join(output,'report.json'),'utf8')):{started:new Date().toISOString(),kind:'real-deepseek-web',checks:[],output:rel};
const save=()=>fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
const digest=p=>createHash('sha256').update(fs.readFileSync(p)).digest('hex');
let browser,page,ownJob;
async function api(route,body){return page.evaluate(async({route,body})=>{const b=await(await fetch('/api/bootstrap')).json();const r=await fetch('/api/'+route,{method:body?'POST':'GET',headers:{'X-Deepblue-Token':b.token,...(body?{'Content-Type':'application/json'}:{})},body:body?JSON.stringify(body):undefined});const result=await r.json();if(!r.ok)throw Error(result.error);return result;},{route,body});}
async function check(name,fn){if(report.checks.some(c=>c.name===name)){console.log('SKIP 已通过 '+name);return;}const start=Date.now();console.log('START '+name);await fn();report.checks.push({name,seconds:Math.round((Date.now()-start)/100)/10});save();console.log('PASS '+name);}
async function submit(prompt){await page.fill('#prompt',prompt);const response=page.waitForResponse(r=>r.url().endsWith('/api/run')&&r.request().method()==='POST');await page.click('#send');const r=await response;const result=await r.json();assert.ok(r.ok(),'Web 提交失败');ownJob=result.job_id;return result;}
async function finish(){await page.waitForFunction(()=>document.querySelector('#stop').hidden,null,{timeout:180000});const job=await api('events?job_id='+ownJob);assert.equal(job.finished,true);assert.equal(job.status,'completed',JSON.stringify(job.outcome));const session=await api('session?id='+job.session_id);return {job,session};}
(async()=>{
 browser=await chromium.launch({headless:true,...(process.env.CHROME_PATH?{executablePath:process.env.CHROME_PATH}:{})});
 const context=await browser.newContext({viewport:{width:1440,height:1000}});page=await context.newPage();
 const errors=[];page.on('pageerror',()=>errors.push('browser script error'));
 await page.goto(url);await page.waitForFunction(()=>document.querySelector('#version').textContent.startsWith('v'));
 const info=await api('bootstrap');assert.equal(path.resolve(info.cwd),root,'请使用当前仓库启动服务');assert.ok(info.configured,'请先在 Web 设置配置 Key');
 const config=await api('config');assert.equal(new URL(config.base_url).hostname,'api.deepseek.com','此脚本只验收真实 DeepSeek');
 const running=await api('events');assert.ok(!running.job_id||running.finished,'已有任务执行中，请稍后运行');
 report.version=info.version;report.model=info.model;let marker=report.marker||'BLUE-'+randomUUID().slice(0,8);
 await page.click('#new-session');
 let selected=report.session_id;
 if(resume){assert.ok(selected);const existing=await api('session?id='+selected);marker=existing.messages.find(m=>m.role==='user'&&/BLUE-[a-f0-9]{8}/.test(m.content||''))?.content.match(/BLUE-[a-f0-9]{8}/)[0]||marker;await page.evaluate(({cwd,selected})=>localStorage.setItem('deepblue:'+cwd+':selected',JSON.stringify(selected)),{cwd:info.cwd,selected});await page.reload();await page.waitForFunction(()=>document.querySelector('#compact').disabled===false);}
 report.marker=marker;
 await check('输入 hi，真实流式回复与 Token 统计',async()=>{
   const r=await submit('hi。不要调用工具，只回复 hi。记住验收标记 '+marker+'，后面会问。');selected=r.session_id;report.session_id=selected;
   const {session}=await finish();assert.ok(session.messages.some(m=>m.role==='assistant'&&/hi/i.test(m.content||'')));assert.ok(session.usage.total_tokens>0);report.hi_tokens=session.usage.total_tokens;
 });
 if(!resume)fs.writeFileSync(path.join(output,'calc.py'),'def add(a, b):\n    return a - b\n');
 const test=path.join(output,'test_calc.py');
 if(!resume)fs.writeFileSync(test,'import unittest\nfrom calc import add\nclass TestAdd(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n        self.assertEqual(add(-2, -3), -5)\n        self.assertEqual(add(0, 8), 8)\n');
 const hash=digest(test);const args=['-B','-m','unittest','discover','-s',output,'-v'];
 if(!resume)assert.notEqual(spawnSync(python,args,{cwd:root,encoding:'utf8',windowsHide:true}).status,0);
 const verify=pythonShell+' -B -m unittest discover -s '+shellQuote(rel)+' -v';
 await check('真实工具修复、刷新恢复、独立测试与 Web 验收证据',async()=>{
   await page.locator('.verification-config summary').click();await page.fill('#verify-command',verify);
   const r=await submit(`仅允许修改目录 ${rel} 内的 calc.py。先 read 阅读 ${rel}/calc.py 和 ${rel}/test_calc.py，然后 edit 修复 add 的错误，不得修改测试。不用扫描项目，不要调用其他工具。完成后简短回复，验收由系统执行。`);assert.equal(r.session_id,selected);
   await page.reload();await page.waitForFunction(()=>document.querySelector('#version').textContent.startsWith('v'));
   // After reload wait for the exact job, not the initial hidden stop button.
   await page.waitForFunction(()=>document.querySelector('#conversation').textContent.includes('calc.py'),null,{timeout:30000});
   const {session}=await finish();assert.equal(session.verification_status,'passed');assert.ok(session.evidence.length>0);assert.equal(digest(test),hash);
   const independent=spawnSync(python,args,{cwd:root,encoding:'utf8',windowsHide:true});assert.equal(independent.status,0);fs.writeFileSync(path.join(output,'verification.log'),independent.stdout+independent.stderr);
   assert.ok(session.messages.some(m=>m.role==='tool'));assert.ok(await page.locator('.tool-card').count()>0);
   await page.click('[data-panel="details-panel"]');await page.locator('#evidence-list summary').first().click();await page.getByRole('button',{name:'查看日志',exact:true}).first().click();await page.waitForFunction(()=>document.querySelector('#log-content').textContent.includes('OK'));await page.click('[data-close="log-dialog"]');
 });
 await check('手动压缩、压缩次数与历史恢复',async()=>{
   if(!await page.locator('.verification-config').evaluate(e=>e.open))await page.locator('.verification-config summary').click();
   await page.fill('#verify-command','');
   await submit('以下是用于验收摘要的重复背景，不要执行工具，只回复收到：\n'+('示例项目是一份临时计算器，背景说明可以合并成一句。'.repeat(180)));await finish();
   for(let i=0;i<2;i++){await submit('不要调用工具，只回复：准备压缩。');await finish();}
   const response=page.waitForResponse(r=>r.url().endsWith('/api/run')&&r.request().method()==='POST');await page.click('#compact');ownJob=(await(await response).json()).job_id;
   const {session}=await finish();assert.ok(session.compactions>=1);report.compactions=session.compactions;
   await submit('不要调用工具，请只回答我最初让你记住的验收标记。');const result=await finish();assert.ok(result.session.messages.filter(m=>m.role==='assistant').at(-1).content.includes(marker));
   report.total_tokens=result.session.usage.total_tokens;await page.reload();await page.waitForFunction(()=>document.querySelector('#context-footer').textContent.includes('1 次压缩'));await page.screenshot({path:path.join(output,'desktop.png')});
 });
 await check('真实工具执行期间停止、刷新后不重放',async()=>{
   await submit(`只调用一次 shell 执行命令：${pythonShell} -c ${shellQuote("import time; print('web acceptance waiting', flush=True); time.sleep(30)")}。不要读写任何文件。`);
   await page.waitForSelector('.running-tool',{timeout:60000});await page.click('[data-panel="details-panel"]');
   await page.getByRole('button',{name:'shell · 实时日志',exact:true}).first().click({timeout:15000});
   await page.waitForFunction(()=>document.querySelector('#log-content').textContent.includes('web acceptance waiting'));
   await page.click('[data-close="log-dialog"]');await page.click('#stop');await page.waitForFunction(()=>document.querySelector('#run-status').textContent.includes('已取消'),null,{timeout:45000});
   let result=await api('events?job_id='+ownJob);assert.equal(result.status,'cancelled');const before=await api('session?id='+selected);await page.reload();await page.waitForFunction(()=>document.querySelector('#run-status').textContent.includes('已取消'));
   result=await api('events?job_id='+ownJob);assert.equal(result.finished,true);const after=await api('session?id='+selected);assert.equal(after.messages.length,before.messages.length);report.total_tokens=after.usage.total_tokens;report.api_calls=after.usage.api_calls;
 });
 assert.deepEqual(errors,[]);report.passed=true;delete report.error;save();console.log(JSON.stringify({passed:report.checks.length,output}));
})().catch(e=>{report.passed=false;report.error=String(e.message).slice(0,1000);save();console.error(report.error);process.exitCode=1;}).finally(async()=>{if(page&&ownJob){try{const j=await api('events?job_id='+ownJob);if(!j.finished)await api('cancel',{job_id:ownJob});}catch{}}if(browser)await browser.close();});
