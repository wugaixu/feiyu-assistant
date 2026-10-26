// Isolated mobile interaction checks. No real network, playback or owner messages.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('integrations/whale_gateway/chat-page.html','utf8');
function section(a,b){return source.slice(source.indexOf(a),source.indexOf(b,source.indexOf(a)));}
// File selection keeps retry IDs, rejects empty/oversize files and caps each batch.
const notices=[],uploads={window:{crypto:{randomUUID:()=> 'a'.repeat(32)}},crypto:{randomUUID:()=> 'a'.repeat(32)},toastText:v=>notices.push(v)};
vm.createContext(uploads);vm.runInContext(section('  var uploadItems=',"  document.getElementById('attachBtn')"),uploads);
uploads.renderUploads=()=>{};
const choice={target:{files:[{name:'photo.png',size:12*1048576},{name:'empty.txt',size:0},...Array.from({length:8},(_,i)=>({name:i+'.txt',size:100}))],value:'selected'}};
uploads.chooseFiles(choice);assert.equal(uploads.uploadItems.length,6);assert.equal(choice.target.value,'');assert.equal(notices.length,4);
const stable=uploads.uploadItems[0].id;uploads.renderUploads();assert.equal(uploads.uploadItems[0].id,stable);
function target(){return {events:{},addEventListener(k,fn,capture){(this.events[k]??=[]).push(fn);},setAttribute(){},emit(k,e={}){for(const fn of this.events[k]||[])fn(e);}};}
// Assistant voice uses the same bubble implementation as incoming voice and has no native controls.
let rendered;const ctx={voiceRecords:{},deviceId:'device &',encodeURIComponent,addVoiceRow(rec,opts){rendered={rec,opts};return rendered;}};
vm.createContext(ctx);vm.runInContext(section('  function addAssistantAudio(',"  function addIncomingFile("),ctx);
ctx.addAssistantAudio({id:'stable',audio:'/api/wechat/assistant/audio?id=7',text:'<script>plain text</script>',duration_ms:12000});
assert.equal(rendered.rec.audioUrl,'/api/wechat/assistant/audio?id=7&device=device%20%26');
assert.equal(rendered.opts.side,'other');assert.equal(rendered.rec.transcript,'<script>plain text</script>');assert.equal(rendered.rec.durationMs,12000);
// Dragging cancels long press; a fired long press suppresses its following synthetic click.
let callback=null,cancelled=false,opened=0;
const gesture={selectMode:false,Date,setTimeout(fn){callback=fn;cancelled=false;return 1;},clearTimeout(){cancelled=true;},openTextSheet(){opened++;},showAvatarMenu(){},toggleSelect(){}};
vm.createContext(gesture);vm.runInContext(section('  function attachLongPress(','  function enterSelectMode()'),gesture);
const bubble=target(),row={querySelector(){return bubble;}};gesture.attachLongPress(row);
bubble.emit('pointerdown',{isPrimary:true,clientX:0,clientY:0});bubble.emit('pointermove',{clientX:0,clientY:25});assert.equal(cancelled,true);assert.equal(opened,0);
bubble.emit('pointerdown',{isPrimary:true,clientX:0,clientY:0});callback();assert.equal(opened,1);
let suppressed=false;bubble.emit('click',{preventDefault(){},stopImmediatePropagation(){suppressed=true;}});assert.equal(suppressed,true);
// Opening and closing management preserves its iframe; foreign messages cannot close it.
const nodes=new Map(),listeners={},history={state:null,pushState(v){this.state=v;},back(){this.state=null;}};
function element(id){if(!nodes.has(id)){const e=target();e.hidden=true;e.dataset={};e.contentWindow={postMessage(){}};Object.defineProperty(e,'src',{set(v){e.loads=(e.loads||0)+1;e.url=v;}});nodes.set(id,e);}return nodes.get(id);}
const manage={document:{getElementById:element},window:{addEventListener(k,f){listeners[k]=f;}},closePopovers(){},setVoiceMode(){},deviceId:'fixture',encodeURIComponent,history,location:{origin:'http://fixture',href:'http://fixture/wechat'}};
vm.createContext(manage);vm.runInContext(section('  var managerLoaded=false','  function closePopovers(){'),manage);
manage.openManager();manage.closeManager();manage.openManager();assert.equal(element('managerFrame').loads,1);assert.equal(element('managerOverlay').hidden,false);
listeners.message({origin:'http://other',source:element('managerFrame').contentWindow,data:{type:'whale-close-manager'}});assert.equal(element('managerOverlay').hidden,false);
listeners.message({origin:'http://fixture',source:element('managerFrame').contentWindow,data:{type:'whale-close-manager'}});assert.equal(element('managerOverlay').hidden,true);
assert.doesNotMatch(source,/<audio\b|id="mascot"|id="balanceText"/);
new vm.Script(source.split('<script>')[1].split('</script>')[0]);
console.log('Mobile voice, long press, scroll cancellation, manager persistence, origin checks and syntax passed');
