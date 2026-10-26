import { EventEmitter } from 'node:events';
import { mkdtempSync } from 'node:fs';
import { join } from 'node:path';
import assert from 'node:assert/strict';
import { installInbox } from './sdk_inbox.mjs';
const dir=mkdtempSync(join('__RUNTIME_ROOT__/','sdk-inbox-test-'));
const client=new EventEmitter();
const serve=installInbox(client,dir,'owner');
const event={isGroup:false,senderUid:'owner',serverMessageId:'1',createTime:Math.ceil(Date.now()/1000)+1,
    account:{uid:'account'},content:{kind:'emoji',url:'tos-cn/storage-id'},
    rawContent:JSON.stringify({url:{uri:'tos-cn/storage-id',url_list:['https://example.invalid/image.gif']}})};
client.emit('message',event);client.emit('message',event);
client.emit('message',{...event,serverMessageId:'2',senderUid:'stranger'});
client.emit('message',{...event,serverMessageId:'3',createTime:1});
let result;
serve({method:'GET',url:'/inbox?after=0'},{writeHead(){},end(value){result=JSON.parse(value);}});
assert.equal(result.events.length,1);
assert.equal(result.events[0].content.url,'https://example.invalid/image.gif');
client.emit('message',{...event,serverMessageId:'4',account:{uid:'__ACCOUNT_UID__'},
  content:{kind:'video',video:{tkey:'owned-key',skey:'0'.repeat(32)}}});
assert.equal(serve.lookupVideo('4').tkey,'owned-key');
assert.throws(()=>serve.lookupVideo('2'));
assert.throws(()=>serve.lookupVideo('../file'));
console.log('SDK inbox URL normalization, replay dedup, sender and history filters passed');
