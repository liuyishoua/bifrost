const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/home/ubuntu/leo-project/.local/leke-research/tools/rea-cli/node_modules/playwright-core');
const assert = require('node:assert/strict');
const center='https://d.58nft.net:9461', portal='https://d.58nft.net';
const upstream=process.env.HUB_ACCESS_PREVIEW, portalUpstream=process.env.PORTAL_ACCESS_PREVIEW;
const artifacts=process.env.HUB_ACCESS_ARTIFACTS;
(async()=>{
 const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});
 const errors=[];
 async function actor(width=1440){
  const context=await browser.newContext({viewport:{width,height:1000}});
  await context.route('**/*',async route=>{
   const u=new URL(route.request().url());
   const base=u.origin===center?upstream:u.origin===portal?portalUpstream:null;
   if(!base){await route.abort();return;}
   const headers=await route.request().allHeaders();
   // The fixture rewrites HTTPS navigations to loopback HTTP; retain the production browser origin for form submissions.
   if(!['GET','HEAD'].includes(route.request().method()))headers.origin=u.origin;
   const response=await route.fetch({url:base+u.pathname+u.search,maxRedirects:0,headers});
   // Playwright does not reroute every redirected navigation. Recreate only
   // the server's validated Location as a fresh navigation, keeping the whole
   // fixture isolated from production and preserving the real form/API writes.
   const location=response.headers().location;
   if(response.status()>=300&&response.status()<400&&location){
    const destination=new URL(location,u.origin);assert.ok([portal,center].includes(destination.origin));
    await route.fulfill({status:200,headers:response.headers()['set-cookie']?{'set-cookie':response.headers()['set-cookie']}:{},contentType:'text/html',body:'<script>location.replace('+JSON.stringify(destination.href)+')</script>'});return;
   }
   await route.fulfill({response});
  });
  const page=await context.newPage();page.setDefaultTimeout(12000);page.on('pageerror',e=>errors.push(e.message));return{context,page};
 }
 async function login(a,name,target='permissions',password='synthetic-browser-123'){
  await a.page.goto(center+'/login?return='+target);
  await a.page.locator('[name=username]').fill(name);await a.page.locator('[name=password]').fill(password);
  await a.page.locator('#login-form').getByRole('button',{name:'登录',exact:true}).click();
  await a.page.waitForURL(portal+(target==='portal-admin'?'/admin':'/permissions'));
 }
 async function register(a,name,password="synthetic-browser-123"){
  await a.page.goto(center+'/login?mode=register');
  await a.page.locator('[name=username]').fill(name);await a.page.locator('[name=password]').fill(password);await a.page.locator('[name=confirm]').fill(password);
  await a.page.getByRole('button',{name:'注册账号',exact:true}).click();await a.page.locator('.portal-card').first().waitFor();
  assert.equal(await a.page.getByRole('button',{name:'我的权限',exact:true}).count(),0);
  assert.equal(await a.page.getByRole('button',{name:'权限管理',exact:true}).count(),0);
  assert.match(await a.page.locator('.portal-brand').textContent(),/运营工作台/);
  assert.equal(await a.page.locator('[data-view]').count(),3);
  await a.page.goto(portal+'/permissions');await a.page.getByRole('heading',{name:'我的权限',exact:true}).waitFor();
 }

 const owner=await actor(1440), member=await actor(390), other=await actor(320);
 await login(owner,'leo','portal-admin');
 await register(member,'ux-member','1');
 const row=(a,id)=>a.page.locator(`[data-user-id="${id}"]`);
 assert.equal(await row(owner,1).locator('[name=role]').isDisabled(),true);
 await owner.page.reload();
 assert.equal(await row(owner,1).locator('[data-reveal-password]').isDisabled(),true);
 const passwordOutput = row(owner,2).locator('[data-saved-password]');
 const revealButton = row(owner,2).locator('[data-reveal-password]');
 assert.equal(await passwordOutput.textContent(),'••••••');
 await revealButton.click();await owner.page.waitForFunction(()=>document.querySelector('[data-user-id="2"] [data-saved-password]').textContent==='1');
 assert.equal(await revealButton.getAttribute('aria-pressed'),'true');
 await revealButton.click();assert.equal(await passwordOutput.textContent(),'••••••');
 await revealButton.click();await owner.page.waitForFunction(()=>document.querySelector('[data-user-id="2"] [data-saved-password]').textContent==='1');
 await owner.page.evaluate(()=>window.dispatchEvent(new Event('blur')));assert.equal(await passwordOutput.textContent(),'••••••');
 await owner.page.clock.install();await revealButton.click();
 await owner.page.waitForFunction(()=>document.querySelector('[data-user-id="2"] [data-saved-password]').textContent==='1');
 await owner.page.clock.fastForward(31000);assert.equal(await passwordOutput.textContent(),'••••••');
 await owner.page.clock.resume();

 await member.page.goto(portal+'/');
 await member.page.getByRole('button',{name:/可直接使用/}).click();
 assert.equal(await member.page.locator('[data-project-id]:visible').count(),2);
 const search=member.page.locator('#query');await search.fill('nothing-matches');
 await member.page.locator('[data-empty]').waitFor();await member.page.locator('[data-reset]').click();
 await member.page.locator('[data-favorite="rift"]').click();
 await member.page.getByRole('button',{name:/我的收藏/}).click();
 assert.equal(await member.page.locator('[data-project-id]:visible').count(),1);
 await member.page.reload();assert.equal(await member.page.locator('[data-project-id]:visible').count(),1);
 await owner.page.goto(portal+'/');await owner.page.getByRole('button',{name:/我的收藏/}).click();
 assert.equal(await owner.page.locator('[data-project-id]:visible').count(),0);
 await owner.page.locator('[data-reset]').click();
 await owner.page.screenshot({path:artifacts+'/ux-home-desktop.png',fullPage:true,animations:'disabled'});
 await member.page.locator('[data-access-filter=all]').click();await member.page.screenshot({path:artifacts+'/ux-home-mobile.png',fullPage:true,animations:'disabled'});
 await member.page.goto(portal+'/permissions');await member.page.screenshot({path:artifacts+'/ux-permissions-mobile.png',fullPage:true,animations:'disabled'});
 assert.equal(await member.page.locator('.permission-app-heading img').count(),18);
 await member.page.goto(portal+'/settings');
 await member.page.locator('#current-password').fill('1');await member.page.locator('#new-password').fill('2');
 await member.page.locator('[aria-controls=new-password]').click();assert.equal(await member.page.locator('#new-password').getAttribute('type'),'text');
 await member.page.locator('[aria-controls=new-password]').click();assert.equal(await member.page.locator('#new-password').getAttribute('type'),'password');
 await member.page.screenshot({path:artifacts+'/ux-settings-mobile.png',fullPage:true,animations:'disabled'});
 await member.page.getByRole('button',{name:'保存新密码',exact:true}).click();
 await member.page.locator('[data-toast]').waitFor();assert.equal(await member.page.locator('.notice-error').count(),0);
 await member.page.waitForFunction(()=>!location.search.includes('changed'));
 await member.page.locator('[data-toast]').waitFor({state:'hidden',timeout:7000});await member.page.reload();assert.equal(await member.page.locator('[data-toast]').count(),0);
 await owner.page.goto(portal+'/admin');
 await row(owner,2).getByRole('button',{name:'编辑账号',exact:true}).click();
 const dialog=owner.page.locator('#credentials-dialog');
 await dialog.locator('[name=username]').fill('ux-renamed');await dialog.locator('[name=new_password]').fill('7');
 await dialog.getByRole('button',{name:'显示',exact:true}).click();assert.equal(await dialog.locator('[name=new_password]').getAttribute('type'),'text');
 await owner.page.screenshot({path:artifacts+'/ux-edit-credentials-desktop.png'});
 await dialog.getByRole('button',{name:'保存账号',exact:true}).click();
 await owner.page.locator('[data-toast]').waitFor();await owner.page.waitForFunction(()=>!location.search.includes('message'));
 assert.match(await row(owner,2).innerText(),/ux-renamed/);
 await row(owner,2).locator('[data-reveal-password]').click();await owner.page.waitForFunction(()=>document.querySelector('[data-user-id="2"] [data-saved-password]').textContent==='7');
 await row(owner,2).locator('[data-reveal-password]').click();await owner.page.locator('[data-toast] .feedback-close').click();assert.equal(await owner.page.locator('[data-toast]').isVisible(),false);
 await owner.page.reload();assert.equal(await owner.page.locator('[data-toast]').count(),0);
 await login(member,'ux-renamed','permissions','7');await member.page.goto(portal+'/settings');
 assert.equal(await member.page.locator('.profile-panel h2').innerText(),'ux-renamed');
 await owner.page.goto(portal+'/admin');await row(owner,2).locator('[name=role]').selectOption('admin');await row(owner,2).getByRole('button',{name:'保存',exact:true}).click();
 await owner.page.locator('[data-toast]').waitFor();await login(member,'ux-renamed','portal-admin','7');
 assert.equal(await row(member,1).locator('[name=role]').isDisabled(),true);
 assert.equal(await row(member,1).getByRole('button',{name:'编辑账号',exact:true}).isDisabled(),true);
 assert.equal(await row(member,1).getByRole('button',{name:'删除',exact:true}).isDisabled(),true);
 assert.equal(await row(member,2).getByRole('button',{name:'管理权限',exact:true}).isDisabled(),true);
 assert.equal(await row(member,1).locator('[data-reveal-password]').isDisabled(),true);
 await member.page.goto(portal+'/permissions');assert.equal(await member.page.locator('.state-available').count(),18);

 for (const width of [320,390,768,1024,1440]) {
   await owner.page.setViewportSize({width,height:900});
   for (const path of ['/','/permissions','/settings','/admin']) {
     await owner.page.goto(portal+path);
     assert.equal(await owner.page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${path} overflow at ${width}`);
   }
 }
 await owner.page.setViewportSize({width:390,height:900});await owner.page.locator('.theme-toggle').click();
 await owner.page.screenshot({path:artifacts+'/ux-admin-mobile-dark.png',fullPage:true,animations:'disabled'});
 await row(owner,2).getByRole('button',{name:'编辑账号',exact:true}).click();
 await owner.page.screenshot({path:artifacts+'/ux-credentials-mobile-dark.png',animations:'disabled'});
 assert.equal(await owner.page.evaluate(()=>document.querySelector('#credentials-dialog').scrollWidth>document.querySelector('#credentials-dialog').clientWidth),false);
 await owner.page.keyboard.press('Escape');
 await row(owner,2).getByRole('button',{name:'管理权限',exact:true}).click();
 await owner.page.screenshot({path:artifacts+'/ux-permissions-dialog-mobile-dark.png',animations:'disabled'});await owner.page.keyboard.press('Escape');
 // A tab must follow the current workspace account after another tab switches it.
 await member.page.goto(portal+'/settings');
 const previousStamp = await member.page.locator('#portal-session-state').textContent();
 await member.context.addCookies((await owner.context.cookies()).filter(cookie=>cookie.name==='ops_workspace_session'));
 await member.page.waitForTimeout(5100);await member.page.evaluate(()=>window.dispatchEvent(new Event('focus')));
 await member.page.waitForFunction(()=>document.querySelector('.profile-panel h2')?.textContent==='leo');
 assert.notEqual(await member.page.locator('#portal-session-state').textContent(),previousStamp);
 assert.deepEqual(errors,[]);await browser.close();console.log('PASS portal UX: saved-password reveal/hide, automatic masking on blur/timeout, current password after reset, separate user favorites, availability filters, settings visibility and one-character password, transient success feedback with clean refresh, username/password reset and identity preservation, protected superadmin, all-app administrator, light/dark and 320–1440 widths');
})().catch(e=>{console.error(e.stack);process.exit(1)});
