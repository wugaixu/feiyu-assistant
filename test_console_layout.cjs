// Redesigned information architecture: isolated VM, no browser or production I/O.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync('app/console.html','utf8');
const source=html.split('<script>')[1].split('</script>')[0].replace(/\nrenderNav\(\);load\(\);/,'');
const nodes=new Map(),classes=new Set(),reads=[],posts=[];
const element=()=>({innerHTML:'',textContent:'',dataset:{},classList:{add(){},remove(){}},setAttribute(){},querySelectorAll(){return [];}});
const ctx=vm.createContext({console,AbortController,Date,document:{body:{classList:{contains:k=>classes.has(k),toggle(k,on){on?classes.add(k):classes.delete(k);}}},getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},querySelector(){return element();},querySelectorAll(){return [];},addEventListener(){}},window:{addEventListener(){},scrollTo(){}},sessionStorage:{getItem(){},setItem(){}},confirm:()=>false,setTimeout(){},clearTimeout(){},setInterval(){}});
vm.runInContext(source,ctx);const run=s=>vm.runInContext(s,ctx);
(async()=>{
 run('renderNav()');const nav=nodes.get('nav').innerHTML;
 for(const title of ['工作空间','自动运行','记录与维护','连接与账号','智能体与会话','能力与工具'])assert.ok(nav.includes(title));
 run('toggleNavigation(true)');assert.ok(classes.has('nav-open'));run('toggleNavigation(false)');assert.ok(!classes.has('nav-open'));
 run('load=()=>{};openTab("qq")');assert.equal(run('current'),'channels');assert.equal(run('channelView'),'qq');
 run('current="comments";settingsDirty=true;openTab("overview")');assert.equal(run('current'),'comments');assert.equal(run('settingsDirty'),true);
 run('settingsDirty=false');
 ctx.responses={
 '/api/channels':{enabled:true,gateway_online:true,devices:[],device_info:[],active:{channel:'qq'},qq:{enabled:true,health:{online:true,bound:true}}},
 '/api/overview':{workflow:{sdk:{online:true}},policies:{shared_comment:{enabled:true,daily_limit:8},dm:{enabled:true,min_send_gap_seconds:1}}},
 '/api/qq':{enabled:true,health:{online:true,bound:true,api_reachable:true},maintenance:{state:'ready',label:'登录正常',webui:{reachable:true}}},
 '/api/tasks':{items:[],counts:{}},'/api/outbound':{window:30,items:[{channel:'qq',kind:'audio',status:'server_accepted',text:'原文',task_id:'fixture'}]},
 '/api/comments':{outbox:[],legacy:[]},'/api/comment-login':{state:'not_checked'},
 '/api/agents/astrbot':{online:true,local_image:{enabled:true,max_batch_images:10},voice:{gsv_enabled:true,gsv_reference_configured:true,gsv_voice:'测试音色'}}
 };
 ctx.reads=reads;ctx.posts=posts;run('api=async(path,opts)=>{if(opts?.method==="POST")posts.push(path);reads.push(path);return responses[path];};');
 ctx.responses['/api/overview'].health={modules:[{id:'comment',title:'作品评论与主人提及',state:'off',label:'未启用',detail:'公开评论关闭',tab:'settings'}]};
 let overviewEl=element();ctx.el=overviewEl;await run('renderOverview(el)');assert.match(overviewEl.innerHTML,/openTab\('comments'\)/);assert.doesNotMatch(overviewEl.innerHTML,/openTab\('settings'\)/);
 let el=element();ctx.el=el;await run('renderChannels(el)');assert.match(el.innerHTML,/QQ账号/);assert.match(el.innerHTML,/主动消息去向/);
 el=element();ctx.el=el;await run('renderCapabilities(el)');assert.match(el.innerHTML,/测试音色/);assert.match(el.innerHTML,/打开手动生图网页/);
 el=element();ctx.el=el;await run('renderTasks(el)');assert.match(el.innerHTML,/QQ/);assert.match(el.innerHTML,/原文/);assert.match(el.innerHTML,/回复与发送记录/);
 el=element();ctx.el=el;await run('renderComments(el)');assert.match(el.innerHTML,/id="sc_enabled"/);assert.match(el.innerHTML,/保存评论设置/);
 ctx.window.__WHALE_REMOTE__=true;run('renderNav()');assert.ok(!nodes.get('nav').innerHTML.includes('data-tab="logs"'));
 el=element();ctx.el=el;await run('renderChannels(el)');assert.doesNotMatch(el.innerHTML,/onclick="qqAction/);
 el=element();ctx.el=el;await run('renderCapabilities(el)');assert.doesNotMatch(el.innerHTML,/manual-image-entry/);
 assert.deepEqual(posts,[],'rendering or browsing must never change workflow configuration');
 assert.doesNotMatch(html,/语音发送尚未接通|选择“本地 Gemma/);
 assert.doesNotMatch(html,/Codex 自动跟随|candidate_add|candidate_remove/);
 assert.match(html,/新会话请先在对应应用中创建/);
 assert.match(html,/onclick="saveActivity\(\)"/);
 console.log('Console redesign: navigation groups, legacy QQ route, drawer, dirty edits, feature placement, QQ receipts and remote restrictions passed');
})().catch(error=>{console.error(error);process.exitCode=1;});

