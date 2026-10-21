const {browserSession}=require('./browser_session.cjs');let cleanupSession;
// 本机网页验证：不提交新闻任务，不使用 DeepSeek/Tavily 额度。
const {chromium}=require('C:/Users/Lenovo/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
 try {
  const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/api/status',async route=>{
   const response=await route.fetch();const data=await response.json();
   data.search={provider:'bing',label:'Bing 新闻'};
   await route.fulfill({response,json:data});
  });
  cleanupSession=await browserSession(page);await page.goto('http://127.0.0.1:8765/');
  await page.waitForFunction(()=>document.getElementById('search-provider').textContent.includes('Bing'));
  assert.match(await page.locator('#search-budget').innerText(),/最多4次模型请求/);
  const state=await (await page.request.get('http://127.0.0.1:8765/api/status')).json();
  assert(['bing','tavily_mcp','configuration_error'].includes(state.search.provider));assert.equal('api_key' in state.search,false);
  assert.equal((await page.request.get('http://127.0.0.1:8765/mcp_config.ini')).status(),404);
  await page.unroute('**/api/status');
  // 只模拟服务器返回给网页的公开方式名称，不写入真实配置，不发起搜索。
  await page.route('**/api/status',async route=>{
   const response=await route.fetch();const data=await response.json();
   data.search={provider:'tavily_mcp',label:'Tavily MCP · Agent 自主搜索'};
   await route.fulfill({response,json:data});
  });
  await page.reload();
  await page.waitForFunction(()=>document.getElementById('search-provider').textContent.includes('Tavily MCP'));
  assert.match(await page.locator('#search-budget').innerText(),/最多10次模型请求/);
  await page.locator('#search-depth').selectOption('standard');await page.locator('#max-articles').selectOption('8');
  assert.match(await page.locator('#search-budget').innerText(),/最多6次模型请求/);
  await page.setViewportSize({width:390,height:844});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  assert.deepEqual(errors,[]);
  console.log('Browser checks passed: provider, budget, secret path, mobile width; no paid requests.');
 }finally{if(cleanupSession)cleanupSession();await browser.close();}
})().catch(e=>{console.error(e.message);process.exitCode=1;});
