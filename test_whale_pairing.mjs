import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,rmSync} from 'node:fs';
import {resolve} from 'node:path';
const base=resolve('__RUNTIME_ROOT__/tests');
const home=mkdtempSync(base+'/pairing-');
process.env.WHALE_HOME=home;
const {PairingService}=await import('./integrations/whale_gateway/core.mjs');
const config={maxDevices:16,idleExpireMs:30*86400000,tokenTtlMs:600000};

test('fresh QR reuses valid cookie, consumes token and survives reload',()=>{
  const service=new PairingService(config);
  const first=service.accept(service.issue().token,'same Android phone');
  const token=service.issue().token;
  const again=service.accept(token,'same Android phone',first.deviceId);
  assert.equal(again.deviceId,first.deviceId);
  assert.equal(again.reused,true);
  assert.equal(service.devices.size,1);
  assert.equal(service.tokens.has(token),false);
  assert.equal(service.accept(token,'same Android phone').deviceId,first.deviceId);
  assert.equal(new PairingService(config).hasDevice(first.deviceId),true);
});

test('matching phone label never authenticates or merges a different client',()=>{
  const service=new PairingService(config);
  const oldCount=service.devices.size;
  const other=service.accept(service.issue().token,'same Android phone','invalid cookie');
  assert.equal(other.ok,true);
  assert.equal(other.reused,undefined);
  assert.equal(service.devices.size,oldCount+1);
  assert.equal(service.accept('invalid-token','same Android phone',other.deviceId).ok,false);
  service.revoke(other.deviceId);
  const replacement=service.accept(service.issue().token,'same Android phone',other.deviceId);
  assert.notEqual(replacement.deviceId,other.deviceId);
});

test.after(()=>{if(!resolve(home).startsWith(base+'/')&&!resolve(home).startsWith(base+'\\'))throw Error('Unsafe cleanup path');rmSync(home,{recursive:true,force:true});});
