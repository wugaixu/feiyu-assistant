// No production requests: exercise real page helpers with an isolated DOM.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('integrations/whale_gateway/chat-page.html','utf8');
function section(a,b){const start=source.indexOf(a);assert.ok(start>=0);const end=source.indexOf(b,start);assert.ok(end>start);return source.slice(start,end);}
function element(){return {children:[],dataset:{},style:{},classList:{toggle(){},remove(){},add(){}},setAttribute(){},addEventListener(){},appendChild(child){child.parentNode=this;this.children.push(child);},insertBefore(child,before){child.parentNode=this;this.children.splice(this.children.indexOf(before),0,child);},querySelector(selector){let found;function visit(node){if((' '+(node.className||'')+' ').includes(' '+selector.slice(1)+' '))found??=node;for(const child of node.children||[])visit(child);}visit(this);return found;}};}
async function main(){
  const msgs=element(),c={document:{createElement:element},msgs,cfg:{},selectMode:false,wireAvatar(){},attachLongPress(){},openVoiceSheet(){},openTextSheet(){},scrollBottom(){},toggleSelect(){},voiceRecords:{},voiceRows:{},hiddenSet:{},fmtDuration:ms=>ms+'',renderVoiceStatus(){},setVoiceText(){}};
  vm.createContext(c);vm.runInContext(section('  function addVoiceRow(','  /* 手机松手后'),c);
  const user={id:'user-old',status:'sent',durationMs:1000},bot={id:'assistant:old',status:'sent',durationMs:2000};
  c.upsertVoice(user,{id:'message-user'});msgs.appendChild(element());c.upsertVoice(bot,{id:'message-bot',side:'other'});
  const original=msgs.children.slice();
  c.addVoiceRow({...user,durationMs:3000},{id:'message-user'});c.addVoiceRow(bot,{id:'message-bot',side:'other'});
  assert.deepEqual(msgs.children,original);assert.equal(c.voiceRows[bot.id].className,'row other');

  // Status endpoint deliberately returns the whole store including deleted and
  // out-of-window voices. Only visible rows may be updated, never appended.
  c.voiceRows.deleted={dataset:{id:'hidden'},parentNode:msgs};c.hiddenSet.hidden=true;
  c.voicePollTimer=null;c.api=async()=>({ok:true,voice:[user,bot,{id:'deleted',status:'pending'},{id:'historical-unrendered',status:'sent'}]});
  let tick,cleared=false;c.setInterval=fn=>(tick=fn,1);c.clearInterval=()=>{cleared=true;};
  vm.runInContext(section('  function startVoicePolling()','  /* 载入历史语音条'),c);
  c.startVoicePolling();tick();await new Promise(setImmediate);
  assert.deepEqual(msgs.children,original);assert.equal(c.voiceRows['historical-unrendered'],undefined);assert.equal(cleared,true);

  // Bind by stable message key even when metadata has extra/failed recordings
  // or arrives out of order; hidden entries never shift the next association.
  const binding={api:async()=>({ok:true,voice:[{id:'extra',messageId:'extra'},{id:'b',messageId:'key-b'},{id:'a',messageId:'key-a'}]}),hiddenSet:{'whale-in:device:key-b':true}};
  vm.createContext(binding);vm.runInContext(section('  function bindVoiceHistory(','  /* 乐观插入'),binding);
  const mapped=await binding.bindVoiceHistory([{role:'user',id:'whale-in:device:key-a',text:'【语音】A'},{role:'user',id:'whale-in:device:key-b',text:'【语音】B'}]);
  assert.equal(mapped['whale-in:device:key-a'].id,'a');assert.equal(mapped['whale-in:device:key-b'],undefined);
  binding.api=async()=>{throw Error('offline');};assert.deepEqual(Object.keys(await binding.bindVoiceHistory([])),[]);

  // Do not poll since zero while asynchronous voice history is still loading.
  let resolveVoices,polls=0,draws=[];
  const initial={document:{getElementById:()=>({hidden:false})},clearTimeout(){},setTimeout(){},api:async()=>({paired:true,workflow:true,seq:42,history:[{id:'bot',role:'assistant',kind:'text',text:'old'},{id:'user',role:'user',kind:'text',text:'hi'}]}),cfg:{},titleText:{},showChat(){},showGuide(){throw Error('unexpected guide');},applyBackground(){},hiddenSet:{},seenMsgIds:{bot:true},bindVoiceHistory:()=>new Promise(resolve=>{resolveVoices=resolve;}),addRow:(_side,_text,_avatar,id)=>draws.push(id),addAssistant:(_text,id)=>draws.push(id),lastSeq:0,startPolling(){polls++;},setStatus(){},removeStatus(){},showDispatchAttention(){}};
  vm.createContext(initial);vm.runInContext(section('  var assistantWorkflow=false;','  var lastDispatchNotice='),initial);
  initial.loadState();await new Promise(setImmediate);assert.equal(polls,0);
  resolveVoices({});await new Promise(setImmediate);assert.equal(polls,1);assert.equal(initial.lastSeq,42);assert.deepEqual(draws,['user']);
  console.log('Voice order: existing rows stable, no historical resurrection, stable ID mapping, history/poll race prevented');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
