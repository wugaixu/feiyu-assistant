import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,mkdirSync,writeFileSync,readFileSync,rmSync} from 'node:fs';
import {join,resolve} from 'node:path';
import {startService,gatewaySettings} from './whale_service.mjs';
import {funnelArgs,createCliOutput,sendAvatarAsset} from './integrations/whale_gateway/core.mjs';

test('avatar cache is private, validates ETags and invalidates changed images',()=>{
  function response(){return {writeHead(status,headers){this.status=status;this.headers=headers;},end(data){this.data=data;}};}
  const first=response();sendAvatarAsset({headers:{}},first,Buffer.from('image1'),'image/png');
  assert.match(first.headers['cache-control'],/private/);assert.equal(first.status,200);
  const unchanged=response();sendAvatarAsset({headers:{'if-none-match':first.headers.etag}},unchanged,Buffer.from('image1'),'image/png');
  assert.equal(unchanged.status,304);assert.equal(unchanged.data,undefined);
  const changed=response();sendAvatarAsset({headers:{'if-none-match':first.headers.etag}},changed,Buffer.from('image2'),'image/png');
  assert.equal(changed.status,200);assert.notEqual(changed.headers.etag,first.headers.etag);
});

test('Tailscale JSON larger than 8KB is preserved, excessive output is rejected',()=>{
  const text=JSON.stringify({BackendState:'Running',Peer:{large:'x'.repeat(30000)}}),output=createCliOutput();
  output.add(text.slice(0,12000));output.add(text.slice(12000));
  assert.equal(JSON.parse(output.value()).BackendState,'Running');
  assert.equal(output.value(),text);
  assert.throws(()=>createCliOutput(16).add('x'.repeat(17)));
});

test('independent gateway inherits Tailscale, never falls back to Cloudflare',()=>{
  assert.equal(gatewaySettings({}).funnel,true);
  assert.equal(gatewaySettings({}).autoTunnel,false);
  assert.equal(gatewaySettings({},{autoTunnel:false}).funnel,false);
  assert.equal(gatewaySettings({network:{mode:'tailscale_funnel'}}).funnel,true);
  assert.throws(()=>gatewaySettings({network:{mode:'cloudflare'}}));
  assert.throws(()=>gatewaySettings({network:{public_base_url:'https://wrong.example/'}}));
  assert.deepEqual(funnelArgs(8796),['funnel','--bg','8796']);
  for(const port of ['8796;whoami',0,65536,1.5])assert.throws(()=>funnelArgs(port));
});

test('independent phone host runs with no DSH services or files',async()=>{
  const root=mkdtempSync('__RUNTIME_ROOT__/tests/whale-host-');
  mkdirSync(join(root,'whale-data'),{recursive:true});
  writeFileSync(join(root,'whale-channel.json'),JSON.stringify({installed:true,standalone:true,enabled:true,devices:[]}));
  writeFileSync(join(root,'whale-bridge-secret.txt'),'f'.repeat(64));
  const service=await startService({port:0,root,autoTunnel:false});
  try{
    const base='http://127.0.0.1:'+service.port;
    const health=await (await fetch(base+'/health')).json();
    assert.equal(health.standalone,true);assert.equal(health.service,'feiyu-whale');
    assert.equal((await fetch(base+'/whale-panel')).status,200);
    assert.equal((await fetch(base+'/api/wechat/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:'must not enter any real queue'})})).status,403);
    assert.equal((await fetch(base+'/api/wechat/assistant/upload?id='+('e'.repeat(32))+'&name=test.txt',{method:'POST',body:'unpaired file'})).status,403);
    assert.equal((await fetch(base+'/shutdown',{method:'POST'})).status,403);
    assert.equal((await fetch(base+'/api/auth/login')).status,404);
    const code=readFileSync('integrations/whale_gateway/core.mjs','utf8');
    for(const dependency of ['sessionController','speechToText','DSH_HOME','.credentials.yaml'])assert.equal(code.includes(dependency),false);
    assert.equal(JSON.parse(readFileSync(join(root,'whale-endpoint.json'),'utf8')).port,service.port);
    const result=await fetch(base+'/shutdown',{method:'POST',headers:{'X-Whale-Bridge':'f'.repeat(64)}});
    assert.equal(result.status,200);
  }finally{await service.shutdown();rmSync(root,{recursive:true,force:true});}
});
