// Local SDK transport. MIT dependency: zhinjs/douyin-im; no browser automation.
import {createServer} from 'node:http';
import {readFileSync,writeFileSync,realpathSync,mkdirSync} from 'node:fs';
import {createHash} from 'node:crypto';
import {join,relative,isAbsolute,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {installInbox} from './sdk_inbox.mjs';
import {validatedWork} from './sdk_share.mjs';
const root='__RUNTIME_ROOT__';
const project=dirname(fileURLToPath(import.meta.url));
const {createClient,segment}=await import(pathToFileURL(join(root,'node_modules/douyin-im/lib/index.js')).href);
const dataDir=join(root,'sdk-data');mkdirSync(dataDir,{recursive:true});
const ownerUid='__OWNER_UID__';
const state={ok:false,online:false,uid:null,nickname:null,note:'starting',updated_at:0};
function publish(patch){Object.assign(state,patch,{updated_at:Date.now()/1000});writeFileSync(join(root,'sdk-sender-status.json'),JSON.stringify(state,null,2));}
const client=createClient({dataDir});
const inbox=installInbox(client,dataDir,ownerUid);
client.on('system.online',({account})=>publish({ok:true,online:true,uid:account.uid,nickname:account.nickname,note:''}));
client.on('system.offline',()=>publish({ok:false,online:false,note:'offline'}));
client.on('system.reconnecting',()=>publish({ok:false,online:false,note:'reconnecting'}));
client.on('system.login.error',()=>publish({ok:false,online:false,note:'login_error'}));
client.on('system.login.qrcode',async({account,qrcodeBase64})=>{
  writeFileSync(join(root,'sdk-login.png'),Buffer.from(qrcodeBase64,'base64'));
  publish({ok:false,online:false,note:'qr_required: sdk-login.png'});await account.continueLogin();
});
client.on('system.login.verification',async({verification})=>{
  publish({ok:false,online:false,note:'manual_verification_required'});await verification.open();
});
function imageBytes(body){
  const path=realpathSync(body.path);
  const roots=[join(root,'media'),join(root,'outgoing-images'),join(project,'账号形象')];
  if(!roots.some(r=>{try{const rel=relative(realpathSync(r),path);return rel!==''&&!rel.startsWith('..')&&!isAbsolute(rel);}catch{return false;}})){throw Error('Unapproved image path');}
  const bytes=readFileSync(path);
  if(!bytes.length||bytes.length>10*1024*1024||createHash('sha256').update(bytes).digest('hex')!==body.sha256)throw Error('Image changed or too large');
  return bytes;
}
const server=createServer((req,res)=>{
  const reply=(status,value)=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8'});res.end(JSON.stringify(value,(_,v)=>typeof v==='bigint'?String(v):v));};
  try{if(inbox(req,res))return;}catch{reply(500,{ok:false,error:'inbox_unavailable'});return;}
  const url=req.url?.split('?')[0];
  if(req.method==='GET'&&url==='/health'){reply(200,{...state,capabilities:{share:true}});return;}
  if(req.method!=='POST'||!['/send','/send-image','/send-share','/resolve-video'].includes(url)){reply(404,{ok:false});return;}
  let raw='';req.on('data',chunk=>{raw+=chunk;if(raw.length>20000)req.destroy();});
  req.on('end',async()=>{
    try{
      const body=JSON.parse(raw);
      if(!state.online||state.uid!=='__ACCOUNT_UID__')throw Error('Expected SDK account offline');
      if(url==='/resolve-video'){
        const video=inbox.lookupVideo(body.message_id);
        const address=await client.pickAccount().im.resolveVideoUrl(video.tkey);
        // Recipient-provided IM attachment key, returned only over loopback.
        reply(200,{ok:true,url:address.mainUrl,key:video.skey});return;
      }
      let message;
      if(url==='/send-image')message=segment.image(imageBytes(body));
      else if(url==='/send-share'){const work=validatedWork(body.work);message=work.imageCount?segment.photos(work):segment.share(work);}
      else{if(typeof body.text!=='string'||!body.text.trim()||body.text.length>1500)throw Error('Invalid text');message=body.text;}
      const account=client.pickAccount();const friends=await account.getFriendList();
      const friend=friends.find(f=>f.uid===ownerUid)??account.pickFriend(ownerUid);
      const result=await friend.sendMsg(message);
      // A check code must not be hidden by statusCode=0 and a server ID.
      // Python independently applies the same conservative boundary, including
      // while this long-lived SDK process is running an older service revision.
      const ok=Number(result.statusCode)===0&&String(result.serverMessageId??'0')!=='0'&&
        (result.checkCode==null||Number(result.checkCode)===0);
      reply(200,{...result,ok});
    }catch(error){reply(200,{ok:false,error_class:error?.name??'Error',statusCode:error?.statusCode??null,checkCode:error?.checkCode??null,response:error?.response??null});}
  });
});
server.listen(8791,'127.0.0.1');
try{await client.login();const account=client.pickAccount();publish({ok:true,online:true,uid:account.uid,nickname:account.nickname,note:''});}
catch{publish({ok:false,online:false,note:'login_failed_manual_action_required'});}
