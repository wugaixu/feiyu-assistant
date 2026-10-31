// Isolated UI regression checks: no browser, workflow, network or real messages.
const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const source=readFileSync('app/console.html','utf8').split('<script>')[1].split('</script>')[0].replace(/\nrenderNav\(\);load\(\);/, '');
function setup(){
 const nodes=new Map(),timers=new Map();let next=0;
 const element=()=>({innerHTML:'',textContent:'',dataset:{},childNodes:[],classList:{add(){},remove(){}},replaceChildren(...children){this.childNodes=children;}});
 const ctx=vm.createContext({console,AbortController,sessionStorage:{getItem(){},setItem(){}},
  document:{getElementById(id){if(!nodes.has(id))nodes.set(id,element());return nodes.get(id);},createElement:element,querySelectorAll(){return [];},addEventListener(){}},
  window:{addEventListener(){}},location:{reload(){ctx.reloads++;}},reloads:0,confirm:()=>true,
  setInterval(){},setTimeout(fn){timers.set(++next,fn);return next;},clearTimeout(id){timers.delete(id);},
  fetch:async()=>({ok:true,json:async()=>({})})});
 vm.runInContext(source,ctx);
 return {ctx,nodes,timers,run:s=>vm.runInContext(s,ctx)};
}
(async()=>{
 let t=setup();
 t.run("api=async()=>({ui_revision:'new'}); current='overview'");
 await t.run('checkUiVersion()');assert.equal(t.ctx.reloads,1);
 t=setup();t.run("api=async()=>({ui_revision:'new'});current='settings';settingsDirty=true");
 await t.run('checkUiVersion()');assert.equal(t.ctx.reloads,0);assert.match(t.nodes.get('ui-update').innerHTML,/先保存/);
 t.ctx.confirm=()=>false;t.run('refreshUi()');assert.equal(t.ctx.reloads,0);
 t=setup();t.run("api=async()=>{throw Error('offline')}");await t.run('checkUiVersion()');assert.equal(t.ctx.reloads,0);
 t=setup();t.run("api=async()=>({ui_revision:UI_REVISION})");await t.run('checkUiVersion()');assert.equal(t.ctx.reloads,0);
 t=setup();t.run("renderOverview=async el=>{await new Promise(r=>{globalThis.finishOld=r});el.childNodes=['old'];};renderTasks=async el=>{el.childNodes=['tasks'];};");
 const old=t.run('load()');t.run("current='tasks'");await t.run('load()');t.run('finishOld()');await old;
 assert.deepEqual(t.nodes.get('main').childNodes.filter(x=>typeof x==='string'),['tasks']);
 t=setup();t.run("document.getElementById('main').childNodes=['last-good']");
 t.run("renderOverview=async()=>{throw Error('offline')}");await t.run('load()');
 assert.deepEqual(Array.from(t.nodes.get('main').childNodes),['last-good']);assert.match(t.nodes.get('load-error').innerHTML,/上次成功/);
 t=setup();t.ctx.fetch=(_url,{signal})=>new Promise((_r,reject)=>signal.addEventListener('abort',()=>reject(Object.assign(Error(),{name:'AbortError'}))));
 const pending=t.run("api('/never')");[...t.timers.values()][0]();await assert.rejects(pending,/超过15秒/);assert.equal(t.timers.size,0);
 assert.doesNotMatch(source,/reviewControl|post\('\/api\/review'/);
 assert.match(source,/自动判断依据/);
 assert.match(source,/当前生效 · 新私信将投递到/);
 assert.match(source,/打开 AstrBot 配置页/);
 assert.match(source,/Ctrl\+Alt\+A/);
 assert.match(source,/astrbot-config/);
 assert.match(source,/公开评论 @主人/);
 assert.match(source,/sc_mention/);
 assert.match(source,/已发送图片缓存/);
 assert.match(source,/'outgoing'/);
 t=setup();
 let opened=null;const win={location:{}};
 t.ctx.window.open=url=>{opened=url;return win;};
 t.run('openAstrbotUi()');
 await new Promise(r=>setTimeout(r,0));
 assert.equal(opened,'about:blank');
 assert.equal(win.location.href,'http://127.0.0.1:6185/');
 t=setup();
 t.ctx.activityResponse={config:{browse_enabled:true,windows:[['10:30','12:30']],window_limits:[6],window_wakes:[2],max_per_wake:5,daily_shares:3},diary_proactive:{selected_astrbot:true,plugin:{enabled:true,patrol_minutes:15,daily_limit_private:3,min_interval_minutes:120,updated_at:1},recent:[]},plans:{},jobs:[],videos_today:0};
 t.run('api=async()=>activityResponse');await t.run("renderActivity(document.getElementById('main'))");
 assert.match(t.nodes.get('main').innerHTML,/情绪日记 · 主动消息/);
 assert.doesNotMatch(t.nodes.get('main').innerHTML,/id="act_mode"|id="act_min"|id="act_proactive"/);
 assert.match(t.nodes.get('main').innerHTML,/act_browse/);
 assert.doesNotMatch(t.nodes.get('main').innerHTML,/新版接入尚待/);
 // Remote read requests are deduplicated, and any write invalidates their cache.
 t=setup();t.ctx.window.__WHALE_REMOTE__=true;
 let requests=0;t.ctx.fetch=async()=>{requests++;return {ok:true,json:async()=>({version:requests})};};
 await t.run("Promise.all([api('/api/channels'),api('/api/channels')])");assert.equal(requests,1);
 await t.run("api('/api/channels')");assert.equal(requests,1);
 await t.run("post('/api/channels',{channel:'douyin'})");
 await t.run("api('/api/channels')");assert.equal(requests,3);
 // Returning to an already-rendered remote tab displays its DOM before refresh completes.
 t=setup();t.ctx.window.__WHALE_REMOTE__=true;
 t.run("renderOverview=async el=>{el.childNodes=['cached overview'];};renderTasks=async el=>{el.childNodes=['tasks'];};");
 await t.run('load()');t.run("current='tasks'");await t.run('load()');
 t.run("current='overview';renderOverview=async el=>{await new Promise(r=>{globalThis.releaseRefresh=r;});el.childNodes=['fresh overview'];};");
 const refresh=t.run('load()');assert.deepEqual(t.nodes.get('main').childNodes.filter(x=>typeof x==='string'),['cached overview']);
 t.run('releaseRefresh()');await refresh;assert.deepEqual(t.nodes.get('main').childNodes.filter(x=>typeof x==='string'),['fresh overview']);
 // Voice labels follow configuration even when the on-demand engine is idle.
 t=setup();
 let voice=t.run("voiceCapabilitiesHtml({gsv_enabled:true,gsv_reference_configured:true,gsv_voice:'大肥鱼 / OhWhale',max_audio_seconds:600,max_audio_mb:32})");
 assert.match(voice,/大肥鱼 \/ OhWhale/);assert.match(voice,/按需待机/);assert.match(voice,/查看文字/);
 assert.doesNotMatch(voice,/小叽|系统女声|服务已连接/);
 voice=t.run("voiceCapabilitiesHtml({gsv_plugin_disabled:true,gsv_voice:'另一个音色 <img src=x>',music_plugin_disabled:true})");
 assert.match(voice,/插件已停用/);assert.match(voice,/&lt;img src=x&gt;/);assert.doesNotMatch(voice,/<img src=x>/);
 const flow=t.run("workflowFlowHtml('codex')");
 assert.match(flow,/鲸聊独立网关/);assert.match(flow,/Codex 分析与回复/);assert.match(flow,/原通道 · 原设备/);
 const audio=t.run("outboundContentHtml({kind:'audio',file_name:'voice.wav',text:'合成原文 <tag>'})");
 assert.match(audio,/语音附件/);assert.match(audio,/查看原文/);assert.match(audio,/合成原文 &lt;tag&gt;/);
 assert.match(t.run("outboundContentHtml({kind:'image',file_name:'picture.png'})"),/picture\.png/);
 assert.match(t.run("outboundContentHtml({kind:'share',work_title:'有趣作品'})"),/有趣作品/);
 console.log('20 UI behavior checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
