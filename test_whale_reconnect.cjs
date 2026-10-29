// Real page functions in an isolated VM: no production HTTP or owner messages.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('integrations/whale_gateway/chat-page.html','utf8');
const section=(a,b)=>{const start=source.indexOf(a),end=source.indexOf(b,start);assert.ok(start>=0&&end>start);return source.slice(start,end);};
async function main(){
 let next=0,calls=0;const timers=new Map();
 const c={deviceId:'test-device',clientId:'test-client',AbortController,Promise,Error,
  setTimeout(fn,ms){timers.set(++next,{fn,ms});return next;},clearTimeout(id){timers.delete(id);},fetch(){calls++;return new Promise(()=>{});}};
 vm.createContext(c);vm.runInContext(section('  function api(path, opts)','  var assistantWorkflow=false;'),c);
 const hung=c.api('/api/wechat/poll?since=9').then(()=>{throw Error('must time out');},e=>e.message);
 [...timers.values()].find(t=>t.ms===12000).fn();assert.equal(await hung,'连接超时');assert.equal(calls,1);
 c.fetch=async()=>({json:async()=>({ok:true})});assert.equal((await c.api('/api/wechat/state')).ok,true);assert.equal(timers.size,0);
 c.fetch=()=>{calls++;return new Promise(()=>{});};const before=calls;
 const post=c.api('/api/wechat/send',{method:'POST'}).catch(e=>e.message);
 [...timers.values()].find(t=>t.ms===120000).fn();await post;assert.equal(calls,before+1,'timed-out writes must never replay automatically');

 let fail,network=[],handled=[],polls=0,scheduled;const p={pollTimer:null,chatShown:true,statusRow:null,lastSeq:9,
  api(){polls++;if(polls===1)return new Promise((_,reject)=>fail=reject);return Promise.resolve({ok:true,seq:10,events:[{id:'new'}]});},
  connectionNotice(value){network.push(value);},handleEvent(e){handled.push(e.id);},setStatus(){},loadState(){throw Error('must not rebuild chat');},
  setTimeout(fn){scheduled=fn;return 1;},clearTimeout(){},document:{hidden:false,addEventListener(){},getElementById(){return {addEventListener(){}};}},window:{addEventListener(){}}};
 vm.createContext(p);vm.runInContext(section('  var pollInFlight=false;','  /* ── 发送'),p);
 p.startPolling();p.startPolling();assert.equal(polls,1,'only one request at a time');
 fail(Error('offline'));await new Promise(setImmediate);assert.deepEqual(network,[true]);assert.equal(typeof scheduled,'function');
 scheduled();await new Promise(setImmediate);assert.equal(p.lastSeq,10);assert.deepEqual(handled,['new']);assert.deepEqual(network,[true,false]);
 p.resumeConnection();await new Promise(setImmediate);assert.equal(polls,3);
 const java=fs.readFileSync('integrations/whale_app/android-source/MainActivity.java','utf8');
 assert.doesNotMatch(java,/\.remove\("origin"\)/);assert.match(java,/showConnectionRecovery/);assert.match(java,/protected void onResume/);assert.match(java,/ERROR_UNKNOWN/);
 console.log('Reconnect: hanging requests time out, writes never replay, polling resumes serially, paired address survives native errors');
}
main().catch(e=>{console.error(e);process.exitCode=1;});
