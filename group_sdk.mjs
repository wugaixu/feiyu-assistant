// Thin SDK adapter: group discovery, member refresh, sendMsg remain in douyin-im.
import {DatabaseSync} from 'node:sqlite';
import {existsSync} from 'node:fs';
const numeric=value=>typeof value==='string'&&/^\d{1,26}$/.test(value);
export function groupSdk(database,accountUid){
  function allowed(groupId,uid,timestamp){
    if(!numeric(groupId)||!numeric(uid)||uid===accountUid||!existsSync(database))return false;
    const db=new DatabaseSync(database,{readOnly:true});
    try{return !!db.prepare(`SELECT 1 FROM group_reply_policy g JOIN group_reply_members m ON m.group_id=g.group_id
      WHERE g.group_id=? AND m.uid=? AND g.enabled=1 AND g.enabled_since<=? AND m.enabled_since<=?`).get(groupId,uid,timestamp,timestamp);}
    catch(error){if(String(error.message).includes('no such table'))return false;throw error;}finally{db.close();}
  }
  const groupInfo=g=>({group_id:String(g.groupId),name:String(g.name||g.groupId).slice(0,100),member_count:g.memberCount??null,is_participant:g.isParticipant!==false});
  async function list(account){
    return (await account.getGroupList(true)).filter(g=>g.isParticipant!==false).map(groupInfo);
  }
  async function members(account,gid){
    if(!numeric(gid))throw Error('Invalid group ID');
    // Refresh via the public SDK; do not invent a group address or add members.
    await account.getGroupList(true);
    const group=account.pickGroup(gid);
    if(!group||group.isParticipant===false)throw Error('Not a current SDK group');
    const members=await group.getMemberList(true);
    return {group:groupInfo(group),members:[...members.values()].filter(m=>String(m.uid)!==accountUid).map(m=>({uid:String(m.uid),name:String(m.alias||m.nickname||m.uid).slice(0,100),role:m.role}))};
  }
  async function send(account,outboxId,makeMessage){
    if(String(account.uid)!==accountUid||typeof outboxId!=='string'||outboxId.length>150)throw Error('Invalid group send');
    const db=new DatabaseSync(database);let row,group,message;
    try{
      db.exec('BEGIN IMMEDIATE');
      row=db.prepare(`SELECT o.id,o.kind,o.text,o.path,o.sha256,o.work_json,t.raw_json
        FROM reply_outbox o JOIN live_tasks t ON t.id=o.task_id
        JOIN group_reply_policy g ON g.group_id=json_extract(t.raw_json,'$.group_id')
        JOIN group_reply_members m ON m.group_id=g.group_id AND m.uid=json_extract(t.raw_json,'$.sender_uid')
        WHERE o.id=? AND o.status='attempted' AND g.enabled=1 AND t.status IN ('processing','done','needs_input','failed')
        AND json_extract(t.raw_json,'$.origin')='group_chat'
        AND json_extract(t.raw_json,'$.is_group')=1
        AND g.revision=json_extract(t.raw_json,'$.group_policy_revision')
        AND g.enabled_since<=json_extract(t.raw_json,'$.association_received_at')
        AND m.enabled_since<=json_extract(t.raw_json,'$.association_received_at')`).get(outboxId);
      if(!row)throw Error('Group reply not authorized');
      const raw=JSON.parse(row.raw_json);
      if(raw.sender_uid===accountUid||!numeric(raw.group_id)||raw.group_id!==raw.sdk_thread_id)throw Error('Group identity mismatch');
      group=account.pickGroup(raw.group_id);
      if(!group||group.isParticipant===false)throw Error('SDK group unavailable');
      message=makeMessage(row);
      const reserved=db.prepare('INSERT OR IGNORE INTO group_send_attempts VALUES(?,?)').run(outboxId,Date.now()/1000);
      if(reserved.changes!==1)throw Error('Group reply already attempted');
      db.exec('COMMIT');
    }catch(error){try{db.exec('ROLLBACK');}catch{}throw error;}finally{db.close();}
    return group.sendMsg(message);
  }
  return {allowed,list,members,send};
}
