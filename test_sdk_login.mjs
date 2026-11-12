import test from 'node:test';
import assert from 'node:assert/strict';
import {loginController} from './sdk_login.mjs';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
test('concurrent reconnects use native SDK once and never send messages',async()=>{
 let release,logins=0,logouts=0;
 const state={online:false},publish=x=>Object.assign(state,x);
 const client={logout:async()=>{logouts++;},login:()=>{logins++;return new Promise(r=>release=r);},pickAccount:()=>({online:true,uid:'test',nickname:'bot'})};
 const controller=loginController(client,publish,state);
 assert.equal(controller.start().state,'started');assert.equal(controller.start().state,'in_progress');await tick();
 release();await tick();assert.equal(logins,1);assert.equal(logouts,1);assert.equal(state.online,true);
 assert.equal(controller.start().state,'online');assert.equal(logins,1);
});
test('QR continuation is not falsely published as online or replaced while fresh',async()=>{
 const state={online:false},publish=x=>Object.assign(state,x);
 const client={logout:async()=>{},login:async()=>publish({note:'qr_required',qr_created_at:Date.now()/1000}),pickAccount:()=>({online:false})};
 const controller=loginController(client,publish,state);controller.start();await tick();
 assert.equal(state.online,false);assert.equal(state.note,'qr_required');assert.equal(controller.start().state,'waiting_qr');
});
test('login failure stays offline and can be explicitly retried',async()=>{
 const state={online:false};let calls=0;
 const client={logout:async()=>{},login:async()=>{calls++;throw Error('synthetic failure');}};
 const controller=loginController(client,x=>Object.assign(state,x),state);controller.start();await tick();
 assert.equal(state.online,false);assert.equal(state.note,'login_failed_manual_action_required');controller.start();await tick();assert.equal(calls,2);
});
