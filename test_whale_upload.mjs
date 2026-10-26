import test from 'node:test';
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {mkdtempSync,writeFileSync,readFileSync,existsSync,rmSync} from 'node:fs';
import {join} from 'node:path';
import {createAssistantBridge,deviceHash} from './integrations/whale_assistant_bridge.mjs';

test('upload spools exact bytes, authenticates bridge and preserves uncertain retries',async()=>{
  const root=mkdtempSync('__RUNTIME_ROOT__/tests/upload-');
  writeFileSync(join(root,'whale-channel.json'),JSON.stringify({installed:true}));
  writeFileSync(join(root,'whale-bridge-secret.txt'),'isolated-secret');
  const device='isolated-device',id='e'.repeat(32),bytes=Buffer.from('上传测试');
  let mode='success',calls=0,last;
  const server=createServer(async(req,res)=>{
    let raw='';for await(const chunk of req)raw+=chunk;
    assert.equal(req.headers['x-whale-bridge'],'isolated-secret');
    assert.equal(req.headers['x-whale-device'],deviceHash(device));
    last=JSON.parse(raw);calls++;
    assert.equal(last.action,'upload');
    assert.deepEqual(readFileSync(join(root,'whale-data/uploads',deviceHash(device),id+'.upload')),bytes);
    const status=mode==='reject'?400:mode==='uncertain'?503:200;
    res.writeHead(status,{'content-type':'application/json'});res.end(JSON.stringify(status===200?{ok:true,task_id:'isolated'}:{error:'isolated failure'}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const bridge=createAssistantBridge(root,{port:server.address().port});
  async function upload({live=true,tooLarge=false}={}){
    let result;
    await bridge.handle({req:{url:'/api/wechat/assistant/upload?id='+id+'&name='+encodeURIComponent('资料.txt'),method:'POST'},res:{},device,live:()=>live,
      readRawBody:async()=>tooLarge?{ok:false,tooLarge:true}:{ok:true,buffer:bytes},writeJson:(_res,status,data)=>{result={status,data};}});
    return result;
  }
  try{
    assert.equal((await upload({live:false})).status,403);assert.equal(calls,0);
    assert.equal((await upload({tooLarge:true})).status,413);assert.equal(calls,0);
    mode='uncertain';assert.equal((await upload()).status,503);
    const spool=join(root,'whale-data/uploads',deviceHash(device),id+'.upload');
    assert.equal(existsSync(spool),true);
    mode='success';assert.equal((await upload()).status,200);assert.equal(existsSync(spool),false);
    assert.equal(last.name,'资料.txt');assert.equal(last.id,id);assert.equal(last.sha256.length,64);
    mode='reject';assert.equal((await upload()).status,503);assert.equal(existsSync(spool),false);
  }finally{
    await new Promise(resolve=>server.close(resolve));
    // Only this newly allocated test directory is removed.
    assert.equal(root.startsWith('__RUNTIME_ROOT__/tests/upload-'),true);
    rmSync(root,{recursive:true,force:true});
  }
});
