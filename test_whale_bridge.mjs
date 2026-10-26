import test from 'node:test';
import assert from 'node:assert/strict';
import {allowedManage,deviceHash} from './integrations/whale_assistant_bridge.mjs';
test('remote management paths are exact and method scoped',()=>{
  assert.equal(allowedManage('GET','/api/overview'),true);
  assert.equal(allowedManage('POST','/api/agent'),true);
  for(const path of ['/api/app/quit','/api/../app/quit','http://evil/','/api/whale-bridge','/api/storage','/api/astrbot/start'])assert.equal(allowedManage('POST',path),false);
  assert.equal(allowedManage('DELETE','/api/agent'),false);
});
test('public device labels are stable hashes, not pairing credentials',()=>{
  assert.equal(deviceHash('private-cookie').length,64);
  assert.notEqual(deviceHash('private-cookie'),deviceHash('other-cookie'));
  assert.equal(deviceHash('private-cookie'),deviceHash('private-cookie'));
});
