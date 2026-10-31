// QQ maintenance UI is read-only remotely; no real requests or credentials.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app/console.html','utf8').split('<script>')[1].split('</script>')[0].replace(/\nrenderNav\(\);load\(\);/,'');
const ctx=vm.createContext({console,AbortController,document:{getElementById(){return {textContent:'',classList:{add(){}}};},addEventListener(){}},window:{addEventListener(){}},sessionStorage:{getItem(){return null;}},setInterval(){}});
vm.runInContext(source,ctx);
let html=vm.runInContext(`qqAccountMaintenanceHtml({enabled:true,health:{online:false,api_reachable:false},maintenance:{state:'login_required',label:'需要扫码登录',webui:{reachable:true,waiting_qrcode:true}}})`,ctx);
assert.match(html,/等待扫码/);assert.match(html,/OneBot接口暂不可用/);assert.match(html,/扫码 \/ 登录维护/);assert.match(html,/启动 NapCat/);
assert.equal(vm.runInContext("TABS.some(x=>x[0]==='channels')",ctx),true);
ctx.window.__WHALE_REMOTE__=true;
html=vm.runInContext(`qqAccountMaintenanceHtml({health:{online:true,bound:true,nickname:'<img src=x>'},maintenance:{state:'ready',label:'登录正常',webui:{reachable:true}}})`,ctx);
assert.match(html,/与已绑定小号一致/);assert.match(html,/&lt;img src=x&gt;/);assert.doesNotMatch(html,/onclick="qqAction/);assert.match(html,/检查登录状态/);
assert.match(vm.runInContext('connectionCards({}, {})',ctx),/QQ/);
console.log('QQ UI: connection account entry, stage-specific status, escaped labels, local-only login controls and overview entry passed');
