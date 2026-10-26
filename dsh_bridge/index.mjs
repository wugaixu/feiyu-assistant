// In-process DSH connector: existing SessionController only; no Agent creation.
import http from 'node:http';
import {readFileSync, mkdirSync, writeFileSync, renameSync, existsSync} from 'node:fs';
import {join} from 'node:path';
import {timingSafeEqual} from 'node:crypto';

export const name = 'douyin-local-session-bridge';
export const inject = ['sessionController','agentPresets'];
const ROOT = String.raw`__RUNTIME_ROOT__`;
const ID = /^[A-Za-z0-9_-]{1,128}$/;
export const capabilities = Object.freeze({version:4,protocol_version:4,sources:['douyin','whale'],mode:'queue',creates_agent:false,native_images:false,materials:'shared_local_paths'});
export const validEnvelope = message => typeof message==='string' && message.length<=30000 &&
  ['[抖音私信]','[鲸聊私信]','[抖音选片任务]'].some(prefix=>message.startsWith(prefix));
function read(path) {return JSON.parse(readFileSync(path, 'utf8').replace(/^\uFEFF/, ''));}
export function summaries(items) {
  return items.filter(x => ID.test(x.sessionId) && x.origin !== 'subagent' && x.formatStatus !== 'migration-required').map(x => ({
    id:x.sessionId, title:String(x.projections?.values?.title || '(无标题)').slice(0,160),
    recency:Number(x.updatedAt)||0, running:!!x.running, agent_available:!!x.agentAvailable,
    cwd:String(x.cwd || x.projections?.values?.workingDirectory || '').slice(0,1024),
    preset:String(x.projections?.values?.agentPreset || '').slice(0,160),
  })).sort((a,b) => b.recency-a.recency).slice(0,100);
}
export function createHandler(controller, root=ROOT, presets=null) {
  const credential=read(join(root,'dsh-bridge.json'));
  const key=Buffer.from(String(credential.token),'utf8');
  if (key.length < 32) throw new Error('Connector credential is missing or too short');
  const listSessions=async()=>{
    const items=summaries((await controller.list({},AbortSignal.timeout(10000))).items);
    if (!presets) return items;
    // Registry metadata only: no Agent creation, model call or session mutation.
    const available=await presets.list();
    const defaultId=items.some(x=>!x.preset)?(await presets.resolve()).id:null;
    return items.map(item=>{
      const preset=available.find(x=>x.id===(item.preset || defaultId));
      const usable=item.agent_available || !!preset && !preset.broken;
      return {...item,available:usable,unavailable_reason:usable?null:'agent_preset_unavailable'};
    });
  };
  return async (req,res) => {
    const send=(status,value) => {if (!res.destroyed) {res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(value));}};
    const auth=Buffer.from((req.headers.authorization || '').replace(/^Bearer /,''),'utf8');
    if (req.headers.origin || req.headers.host !== '127.0.0.1:8794' || auth.length!==key.length || !timingSafeEqual(auth,key)) return send(403,{error:'unauthorized'});
    try {
      if (req.method==='GET' && req.url==='/capabilities') return send(200,{ok:true,...capabilities});
      if (req.method==='GET' && req.url==='/sessions') {
        return send(200,{ok:true,items:await listSessions()});
      }
      if (req.method!=='POST' || req.url!=='/prompt') return send(404,{error:'not_found'});
      let bytes=0;const chunks=[];
      for await (const chunk of req) {bytes+=chunk.length;if(bytes>65536)return send(413,{error:'too_large'});chunks.push(chunk);}
      const body=JSON.parse(Buffer.concat(chunks).toString('utf8'));
      if (!ID.test(body.session_id) || !/^[0-9a-f]{32}$/.test(body.task_id) || !validEnvelope(body.message)) return send(400,{error:'invalid_request'});
      const target=read(join(root,'dispatch-target.json'));
      if (target.provider!=='dsh' || target.dsh?.session_id!==body.session_id) return send(409,{error:'destination_changed'});
      const items=await listSessions();
      if (!items.some(x=>x.id===body.session_id && x.available!==false)) return send(409,{error:'session_unavailable'});
      if(body.images)return send(400,{error:'invalid_request'});
      const dir=join(root,'dsh-dispatch-receipts');mkdirSync(dir,{recursive:true});
      const path=join(dir,body.task_id+'.json');
      // Exclusive persistent reservation survives connector/host reloads.
      if (existsSync(path)) {
        const prior=read(path);
        if(prior.session_id!==body.session_id)return send(409,{error:'already_reserved_elsewhere'});
        return prior.status==='accepted' ? send(200,{accepted:true,request_id:prior.request_id}) : send(409,{error:'outcome_uncertain'});
      }
      const receipt={task_id:body.task_id,session_id:body.session_id,request_id:'douyin-'+body.task_id,status:'attempted',at:Date.now()};
      writeFileSync(path,JSON.stringify(receipt),{flag:'wx',mode:0o600});
      const request={requestId:receipt.request_id,sessionId:body.session_id,mode:'queue',content:[{type:'text',text:body.message}],clientTimeZone:'Asia/Shanghai'};
      const result=await controller.prompt(request,AbortSignal.timeout(25000));receipt.visual_mode='shared_local_paths';
      if(result.accepted!==true)throw new Error('Prompt acknowledgement missing');
      receipt.status='accepted';writeFileSync(path+'.tmp',JSON.stringify(receipt));renameSync(path+'.tmp',path);
      return send(200,{accepted:true,request_id:receipt.request_id});
    } catch {return send(503,{error:'connector_request_failed'});}
  };
}
export function apply(ctx) {
  ctx.effect(() => {
    const server=http.createServer(createHandler(ctx.sessionController,ROOT,ctx.agentPresets));
    server.requestTimeout=30000;server.headersTimeout=10000;
    server.on('error',err=>console.error('douyin bridge:',err.code || 'startup_failed'));
    server.listen(8794,'127.0.0.1');
    return () => {server.close();server.closeAllConnections();};
  });
}
