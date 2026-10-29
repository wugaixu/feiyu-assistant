// MIT SDK public message events -> durable local spool. No automatic replies.
import { DatabaseSync } from 'node:sqlite';
import { join } from 'node:path';
export function installInbox(client, dataDir, ownerUid, options={}) {
  const db = new DatabaseSync(join(dataDir, 'assistant-inbox.sqlite3'));
  db.exec(`PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
      message_id TEXT UNIQUE NOT NULL,payload TEXT NOT NULL);`);
  db.prepare('INSERT OR IGNORE INTO settings VALUES(?,?)').run('baseline', String(Math.ceil(Date.now()/1000)));
  const baseline = Number(db.prepare('SELECT value FROM settings WHERE key=?').get('baseline').value);
  client.on('message', event => {
    if (!event.serverMessageId || String(event.senderUid)===String(event.account.uid) || (event.account.imUid&&String(event.senderUid)===String(event.account.imUid))) return;
    // Require an actual wire timestamp: the SDK's fallback "now" is not proof of freshness.
    const rawTime = Number(event.createTime);
    const timestamp = rawTime > 1e10 ? rawTime/1000 : rawTime;
    if (!Number.isFinite(timestamp) || timestamp < baseline) return;
    const isGroup=event.isGroup===true;
    const groupId=isGroup?String(event.group?.groupId||event.threadId||''):null;
    if(isGroup){if(!options.allowedGroup?.(groupId,String(event.senderUid),timestamp))return;}
    else if(event.senderUid!==ownerUid)return;
    const content = {...event.content};
    // The upstream parser may prefer a storage URI over a usable HTTPS URL.
    if (content.kind === 'emoji') {
      try {
        const original = JSON.parse(event.rawContent);
        const urls = original.url?.url_list ?? [];
        content.url = urls.find(url=>typeof url==='string' && url.startsWith('https://')) ?? content.url;
      } catch { /* keep unavailable resources explicit */ }
    }
    const payload = {account_uid:event.account.uid,sender_uid:event.senderUid,
      sender_sec_uid:event.senderSecUid,thread_id:event.threadId,
      message_id:String(event.serverMessageId),timestamp,content,text:event.text};
    if(isGroup)Object.assign(payload,{is_group:true,group_id:groupId,
      group_name:String(event.group?.name||groupId).slice(0,100),sender_name:String(event.member?.alias||event.member?.nickname||event.senderUid).slice(0,100),message_type:event.messageType});
    db.prepare('INSERT OR IGNORE INTO events(message_id,payload) VALUES(?,?)')
      .run(isGroup?'group:'+groupId+':'+event.serverMessageId:String(event.serverMessageId), JSON.stringify(payload));
  });
  const handler = (req,res) => {
    const url = new URL(req.url,'http://127.0.0.1');
    if (req.method !== 'GET' || url.pathname !== '/inbox') return false;
    const after=Number(url.searchParams.get('after') ?? 0);
    if (!Number.isSafeInteger(after) || after<0) {res.writeHead(400);res.end();return true;}
    const events=db.prepare('SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT 100').all(after);
    res.writeHead(200,{'Content-Type':'application/json; charset=utf-8'});
    res.end(JSON.stringify({baseline,events:events.map(r=>({seq:r.seq,...JSON.parse(r.payload)}))}));
    return true;
  };
  handler.lookupVideo = (messageId,groupId=null) => {
    if(!/^\d{1,26}$/.test(String(messageId)))throw Error('Invalid message ID');
    if(groupId!==null&&!/^\d{1,26}$/.test(groupId))throw Error('Invalid group ID');
    const row=db.prepare('SELECT payload FROM events WHERE message_id=?').get(groupId?'group:'+groupId+':'+messageId:String(messageId));
    if(!row)throw Error('Video message not in authenticated inbox');
    const message=JSON.parse(row.payload);
    const attributed=message.is_group===true?options.allowedGroup?.(message.group_id,message.sender_uid,message.timestamp):message.sender_uid===ownerUid;
    if(!attributed||message.account_uid!=='__ACCOUNT_UID__'||message.content?.kind!=='video')throw Error('Unattributed video');
    return message.content.video;
  };
  handler.close=()=>db.close();
  return handler;
}
