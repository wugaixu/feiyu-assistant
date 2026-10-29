import test from 'node:test';
import assert from 'node:assert/strict';
import {probePublicIngress} from './integrations/whale_gateway/public-health.mjs';
const host='phone.test.ts.net';
test('real ingress errors remain failures even with configured/public DNS',async()=>{
 let attempts=0;const result=await probePublicIngress(host,{resolveAddresses:async()=>['203.0.113.1','203.0.113.2'],connect:async()=>{attempts++;return false;}});
 assert.equal(result.reachable,false);assert.equal(result.state,'tls_unreachable');assert.equal(attempts,2);
});
test('DNS failure is distinct from an unavailable DNS probe',async()=>{
 assert.equal((await probePublicIngress(host,{resolveAddresses:async()=>[]})).state,'dns_missing');
 assert.equal((await probePublicIngress(host,{resolveAddresses:async()=>null})).reachable,null);
 assert.equal((await probePublicIngress(host,{resolveAddresses:async()=>{throw Error('offline');}})).reachable,null);
});
test('healthy fallback ingress succeeds without looking up host locally',async()=>{
 const calls=[];const result=await probePublicIngress(host,{resolveAddresses:async()=>['203.0.113.1','203.0.113.2'],connect:async(ip,sni)=>{calls.push([ip,sni]);return ip.endsWith('.2');}});
 assert.equal(result.reachable,true);assert.equal(result.state,'reachable');assert.equal(calls[1][1],host);
});
test('invalid host never starts a network probe',async()=>{
 const result=await probePublicIngress('https://bad.example/path',{resolveAddresses:()=>{throw Error('must not call');}});assert.equal(result.state,'unverified');
});
