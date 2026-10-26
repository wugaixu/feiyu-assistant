import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {mkdtempSync,writeFileSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {once} from 'node:events';
import {createHandler,summaries} from './index.mjs';
const ROOT=String.raw`__RUNTIME_ROOT__`;
const SID='session-example',TID='a'.repeat(32),token='test-token-'.repeat(6);
test('session summaries omit history and subagents',()=>{
  const list=summaries([{sessionId:SID,updatedAt:100,projections:{values:{title:'标题',inbox:{secret:'never expose'}}}},{sessionId:'sub',origin:'subagent'}]);
  assert.equal(list.length,1);assert.equal(list[0].title,'标题');assert.equal(JSON.stringify(list).includes('secret'),false);
});
test('authenticated existing-session enqueue, persistent receipt and failure handling',async()=>{
  const root=mkdtempSync(join(ROOT,'dsh-connector-test-'));
  let calls=0,fail=false,presetAvailable=true;
  const controller={list:async()=>({items:[{sessionId:SID,projections:{values:{agentPreset:'standard'}}}]}),prompt:async req=>{calls++;assert.equal(req.mode,'queue');if(fail)throw Error('timeout');return {accepted:true};}};
  writeFileSync(join(root,'dsh-bridge.json'),JSON.stringify({token}));
  const target={provider:'dsh',dsh:{session_id:SID}};
  writeFileSync(join(root,'dispatch-target.json'),JSON.stringify(target));
  const server=http.createServer(createHandler(controller,root,{list:async()=>presetAvailable?[{id:'standard'}]:[]}));server.listen(0,'127.0.0.1');await once(server,'listening');
  const request=(path,body,headers={})=>new Promise((resolve,reject)=>{
    const r=http.request({hostname:'127.0.0.1',port:server.address().port,path,method:body?'POST':'GET',headers:{Host:'127.0.0.1:8794',Authorization:'Bearer '+token,...headers}},res=>{
      let text='';res.on('data',x=>text+=x);res.on('end',()=>resolve({status:res.statusCode,body:JSON.parse(text)}));
    });r.on('error',reject);r.end(body?JSON.stringify(body):undefined);
  });
  try {
    assert.equal((await request('/sessions')).body.items.length,1);
    assert.deepEqual((await request('/capabilities')).body.sources,['douyin','whale']);
    assert.equal((await request('/sessions',null,{Authorization:'Bearer wrong'})).status,403);
    assert.equal((await request('/sessions',null,{Origin:'http://evil.invalid'})).status,403);
    const body={session_id:SID,task_id:TID,message:'[抖音私信] 测试'};
    assert.equal((await request('/prompt',body)).body.accepted,true);
    assert.equal((await request('/prompt',body)).body.accepted,true);assert.equal(calls,1);
    target.provider='codex';writeFileSync(join(root,'dispatch-target.json'),JSON.stringify(target));
    assert.equal((await request('/prompt',{...body,task_id:'b'.repeat(32)})).status,409);assert.equal(calls,1);
    target.provider='dsh';writeFileSync(join(root,'dispatch-target.json'),JSON.stringify(target));
    fail=true;assert.equal((await request('/prompt',{...body,task_id:'c'.repeat(32)})).status,503);
    fail=false;assert.equal((await request('/prompt',{...body,task_id:'c'.repeat(32)})).status,409);assert.equal(calls,2);
    assert.equal((await request('/prompt',{...body,session_id:'other'})).status,409);
    const whale={...body,task_id:'d'.repeat(32),message:'[鲸聊私信]\n{"text":"你好"}'};
    assert.equal((await request('/prompt',whale)).body.accepted,true);
    assert.equal((await request('/prompt',whale)).body.accepted,true);
    assert.equal(calls,3); // distinct sources share stable once-only admission.
    assert.equal((await request('/prompt',{...body,task_id:'e'.repeat(32),message:'unrecognized envelope'})).status,400);
    assert.equal(calls,3);
    presetAvailable=false;
    assert.equal((await request('/sessions')).body.items[0].available,false);
    assert.equal((await request('/prompt',{...body,task_id:'f'.repeat(32)})).body.error,'session_unavailable');
    assert.equal(calls,3); // missing legacy preset is rejected before reservation/model execution.
  } finally {server.close();server.closeAllConnections();await once(server,'close');rmSync(root,{recursive:true,force:true});}
});
