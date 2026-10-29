import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DatabaseSync} from 'node:sqlite';
import {mkdtempSync,rmSync} from 'node:fs';
import {join,resolve,sep} from 'node:path';
import {EventEmitter} from 'node:events';
import {groupSdk} from './group_sdk.mjs';
import {installInbox} from './sdk_inbox.mjs';
const own='__ACCOUNT_UID__',owner='__OWNER_UID__',uid='123456789',gid='50001';
function fixture(t){
 const root='__TEMP_PARENT__/',dir=mkdtempSync(join(root,'group-sdk-')),cleanup=[];
 const db=new DatabaseSync(join(dir,'test.sqlite3'));
 t.after(()=>{for(const close of cleanup)close();db.close();assert.ok(resolve(dir).startsWith(resolve(root)+sep));rmSync(dir,{recursive:true,force:true});});
 db.exec(`CREATE TABLE group_reply_policy(group_id TEXT PRIMARY KEY,name TEXT,enabled INTEGER,enabled_since REAL,revision INTEGER);
 CREATE TABLE group_reply_members(group_id TEXT,uid TEXT,name TEXT,enabled_since REAL,PRIMARY KEY(group_id,uid));
 CREATE TABLE group_send_attempts(outbox_id TEXT PRIMARY KEY,started REAL);
 CREATE TABLE live_tasks(id TEXT PRIMARY KEY,status TEXT,raw_json TEXT);
 CREATE TABLE reply_outbox(id TEXT PRIMARY KEY,task_id TEXT,status TEXT,kind TEXT,text TEXT,path TEXT,sha256 TEXT,work_json TEXT);`);
 const raw={origin:'group_chat',is_group:true,group_id:gid,sdk_thread_id:gid,sender_uid:uid,group_policy_revision:1,association_received_at:200};
 db.prepare('INSERT INTO group_reply_policy VALUES(?,?,?,?,?)').run(gid,'测试群',1,100,1);
 db.prepare('INSERT INTO group_reply_members VALUES(?,?,?,?)').run(gid,uid,'甲',100);
 db.prepare('INSERT INTO live_tasks VALUES(?,?,?)').run('task','done',JSON.stringify(raw));
 db.prepare('INSERT INTO reply_outbox VALUES(?,?,?,?,?,?,?,?)').run('out','task','attempted','text','你好群友',null,null,null);
 const sends=[],calls=[];
 const group={groupId:gid,name:'测试群',memberCount:2,isParticipant:true,
  getMemberList:async force=>{calls.push(['members',force]);return new Map([[uid,{uid,nickname:'甲',role:0}],[own,{uid:own,nickname:'bot',role:0}]]);},
  sendMsg:async message=>{sends.push(message);return {statusCode:0,serverMessageId:'88'};}};
 const account={uid:own,pickGroup:id=>id===gid?group:undefined,getGroupList:async force=>{calls.push(['groups',force]);return [group];}};
 return {dir,db,raw,cleanup,sends,calls,group,account,adapter:groupSdk(join(dir,'test.sqlite3'),own)};
}
test('public SDK list/member methods are reused; bot excluded; no sends',async t=>{
 const f=fixture(t);assert.equal((await f.adapter.list(f.account))[0].group_id,gid);
 const value=await f.adapter.members(f.account,gid);
 assert.deepEqual(value.members.map(m=>m.uid),[uid]);assert.ok(f.calls.some(x=>x[0]==='members'&&x[1]===true));assert.equal(f.sends.length,0);
});
test('enabled groups and selected members only; own messages and old messages denied',t=>{
 const f=fixture(t);assert.equal(f.adapter.allowed(gid,uid,200),true);
 for(const args of [[gid,uid,1],['50002',uid,200],[gid,'999',200],[gid,own,200]])assert.equal(f.adapter.allowed(...args),false);
});
test('sendMsg targets original group and reserves exactly once',async t=>{
 const f=fixture(t);await f.adapter.send(f.account,'out',row=>row.text);
 await assert.rejects(f.adapter.send(f.account,'out',row=>row.text),/attempted/);
 assert.deepEqual(f.sends,['你好群友']);
});
test('paused/removed members, changed policy, wrong thread or private source cannot send',async t=>{
 for(const mutate of [f=>f.db.exec('UPDATE group_reply_policy SET enabled=0'),f=>f.db.exec('DELETE FROM group_reply_members'),f=>f.db.exec('UPDATE group_reply_policy SET revision=2'),
  f=>f.db.prepare('UPDATE live_tasks SET raw_json=?').run(JSON.stringify({...f.raw,sdk_thread_id:'50002'})),
  f=>f.db.prepare('UPDATE live_tasks SET raw_json=?').run(JSON.stringify({...f.raw,is_group:false})),
  f=>f.db.prepare('UPDATE live_tasks SET raw_json=?').run(JSON.stringify({...f.raw,origin:'private'}))]){
  const f=fixture(t);mutate(f);await assert.rejects(f.adapter.send(f.account,'out',row=>row.text));assert.equal(f.sends.length,0);
 }
});
test('departed group and wrong account cannot fall back to private sending',async t=>{
 const f=fixture(t);await assert.rejects(f.adapter.send({...f.account,uid:'wrong'},'out',row=>row.text));
 f.group.isParticipant=false;await assert.rejects(f.adapter.send(f.account,'out',row=>row.text));assert.equal(f.sends.length,0);
});
test('concurrent send attempts call SDK once; network failure is not replayed',async t=>{
 const f=fixture(t);f.group.sendMsg=async()=>{f.sends.push('attempt');throw Error('lost response');};
 const results=await Promise.allSettled([f.adapter.send(f.account,'out',row=>row.text),f.adapter.send(f.account,'out',row=>row.text)]);
 assert.equal(results.filter(x=>x.status==='rejected').length,2);assert.equal(f.sends.length,1);
});
test('SDK event spool attributes groups/members, ignores self and namespaces equal IDs',t=>{
 const f=fixture(t),client=new EventEmitter(),inbox=installInbox(client,f.dir,owner,{allowedGroup:f.adapter.allowed});f.cleanup.push(inbox.close);
 const event={isGroup:true,senderUid:uid,threadId:gid,serverMessageId:'71',createTime:Math.ceil(Date.now()/1000)+1,
  account:{uid:own},group:f.group,member:{nickname:'甲'},content:{kind:'text'},text:'hello'};
 client.emit('message',event);client.emit('message',event);
 client.emit('message',{...event,senderUid:own,serverMessageId:'72'});
 client.emit('message',{...event,senderUid:'999',serverMessageId:'73'});
 client.emit('message',{...event,group:{groupId:'50002'},threadId:'50002',serverMessageId:'74'});
 client.emit('message',{...event,isGroup:false,senderUid:owner,serverMessageId:'71'});
 let value;inbox({method:'GET',url:'/inbox?after=0'},{writeHead(){},end(raw){value=JSON.parse(raw);}});
 assert.equal(value.events.length,2);assert.equal(value.events[0].group_id,gid);assert.equal(value.events[0].sender_name,'甲');assert.equal(value.events[1].is_group,undefined);
});
test('group video resolution uses the group identity and revocation, never private lookup',t=>{
 const f=fixture(t),client=new EventEmitter(),inbox=installInbox(client,f.dir,owner,{allowedGroup:f.adapter.allowed});f.cleanup.push(inbox.close);
 client.emit('message',{isGroup:true,senderUid:uid,threadId:gid,group:f.group,serverMessageId:'91',createTime:Math.ceil(Date.now()/1000)+1,
  account:{uid:own},content:{kind:'video',video:{tkey:'group-key',skey:'0'.repeat(32)}}});
 assert.equal(inbox.lookupVideo('91',gid).tkey,'group-key');assert.throws(()=>inbox.lookupVideo('91'));
 f.db.exec('UPDATE group_reply_policy SET enabled=0');assert.throws(()=>inbox.lookupVideo('91',gid));
});
