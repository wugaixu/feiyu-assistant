/** Whale Chat -> local assistant adapter. No DSH model is invoked in bridge mode. */
import {readFileSync,writeFileSync,renameSync,mkdirSync,existsSync,unlinkSync,readdirSync,statSync} from 'node:fs';
import {join} from 'node:path';
import {createHash,randomUUID} from 'node:crypto';
import {request} from 'node:http';
const ROOT='__RUNTIME_ROOT__';
export const deviceHash=device=>createHash('sha256').update(device).digest('hex');
export const allowedManage=(method,path)=>{
  const get=['/','/favicon.ico','/api/overview','/api/threads','/api/agents/dsh','/api/agents/astrbot','/api/activity','/api/tasks','/api/outbound','/api/comments','/api/comment-login','/api/storage','/api/channels','/api/app/ping'];
  const post=['/api/target','/api/agent','/api/astrbot/session','/api/policy','/api/activity','/api/channels','/api/local-image'];
  return (method==='GET'?get:method==='POST'?post:[]).includes(path);
};
export function createAssistantBridge(root=ROOT,{port=8793}={}){
  if(!Number.isInteger(port)||port<1||port>65535)throw new Error('Invalid local bridge port');
  const uploading=new Set();
  const announce=port=>{
    if(!Number.isInteger(port)||port<1024||port>65535)return;
    const path=root+'/whale-endpoint.json',tmp=path+'.tmp';
    writeFileSync(tmp,JSON.stringify({port,version:1,loaded_at:Date.now()/1000}));renameSync(tmp,path);
  };
  const installed=()=>{try{return JSON.parse(readFileSync(root+'/whale-channel.json','utf8')).installed===true;}catch{return false;}};
  async function call(device,path,body,method='POST'){
    const token=readFileSync(root+'/whale-bridge-secret.txt','utf8').trim();
    const data=body===undefined?undefined:Buffer.from(JSON.stringify(body));
    return await new Promise((resolve,reject)=>{
      const req=request({hostname:'127.0.0.1',port,path,method,
        headers:{'X-Whale-Bridge':token,'X-Whale-Device':deviceHash(device),'Host':'127.0.0.1:8793','Origin':'http://127.0.0.1:8793','Content-Type':'application/json',...(data?{'Content-Length':data.length}:{})}},res=>{
          let size=0;const chunks=[];
          const maxBytes=path==='/api/whale-bridge'&&['audio','attachment'].includes(body?.action)?32*1024*1024:12*1024*1024;
          res.on('data',c=>{size+=c.length;if(size>maxBytes){res.destroy();reject(new Error('Bridge response too large'));}else chunks.push(c);});
          res.on('error',reject);res.on('end',()=>resolve({status:res.statusCode,type:res.headers['content-type']||'application/json',data:Buffer.concat(chunks)}));
        });
      req.setTimeout(15000,()=>req.destroy(new Error('本地管理台连接超时')));
      req.on('error',reject);req.end(data);
    });
  }
  const jsonCall=async(device,body)=>{
    const response=await call(device,'/api/whale-bridge',body);
    const data=JSON.parse(response.data.toString('utf8'));
    if(response.status!==200){const error=new Error(data.error||'助手通道不可用，请在电脑管理台检查设备授权');error.status=response.status;throw error;}
    return data;
  };
  const enroll=async device=>{
    const response=await call(device,'/api/whale-bridge/enroll',{},'POST');
    if(response.status!==200)throw new Error('配对设备自动登记失败');
    return JSON.parse(response.data.toString('utf8'));
  };
  const receive=(device,text,id=randomUUID())=>jsonCall(device,{action:'receive',id,text});
  async function handle({req,res,device,live,readJsonBody,readRawBody,writeJson,sttInfo={ready:false},nickname='大肥鱼',hiddenIds=[]}){
    const url=new URL(req.url,'http://whale.invalid'),path=url.pathname;
    const supported=['/api/wechat/state','/api/wechat/send','/api/wechat/poll','/api/wechat/cancel'];
    if(!installed()||(!supported.includes(path)&&!path.startsWith('/api/wechat/assistant/')))return false;
    if(!device||!live(device)){writeJson(res,403,{ok:false,error:'设备未配对或已失效'});return true;}
    try{
      if(path==='/api/wechat/state'){
        if(req.method!=='GET')throw new Error('GET required');
        const result=await jsonCall(device,{action:'state'});
        writeJson(res,200,{...result,paired:true,nickname,hiddenIds,stt:sttInfo,avatarOther:'/api/wechat/avatar/other',avatarMe:'/api/wechat/avatar/me'});
      }else if(path==='/api/wechat/assistant/upload'){
        if(req.method!=='POST')throw new Error('POST required');
        if(uploading.has(device)||uploading.size>=2){writeJson(res,429,{ok:false,error:'上一份文件仍在上传，请稍后再试'});return true;}
        const id=url.searchParams.get('id'),name=url.searchParams.get('name');
        if(!/^[0-9a-f-]{32,36}$/.test(id||'')||!name||name.length>160||/[\\/\u0000-\u001f\u007f]/.test(name))throw new Error('文件名或上传编号无效');
        uploading.add(device);
        try{
          const body=await readRawBody(req,32*1024*1024);
          if(!body.ok){writeJson(res,body.tooLarge?413:400,{ok:false,error:'文件上传失败，单文件上限32MiB'});return true;}
          if(!body.buffer.length)throw new Error('不能发送空文件');
          const digest=createHash('sha256').update(body.buffer).digest('hex');
          const folder=join(root,'whale-data/uploads',deviceHash(device));mkdirSync(folder,{recursive:true});
          const spool=join(folder,id+'.upload');
          if(existsSync(spool)){
            if(createHash('sha256').update(readFileSync(spool)).digest('hex')!==digest)throw new Error('上传编号与文件内容冲突');
          }else{
            const queuedBytes=readdirSync(folder).filter(n=>/^[0-9a-f-]{32,36}\.upload$/.test(n)).reduce((sum,n)=>sum+statSync(join(folder,n)).size,0);
            if(queuedBytes+body.buffer.length>128*1024*1024)throw new Error('有较多结果未确认的上传，请先重试原文件或检查电脑管理台');
            writeFileSync(spool,body.buffer,{flag:'wx'});
          }
          let result;
          try{result=await jsonCall(device,{action:'upload',id,name,sha256:digest});}
          catch(error){if([400,403,413].includes(error.status)){try{unlinkSync(spool);}catch{}}throw error;}
          try{unlinkSync(spool);}catch{}writeJson(res,200,result);
        }finally{uploading.delete(device);}
      }else if(path==='/api/wechat/send'){
        if(req.method!=='POST')throw new Error('POST required');
        const body=await readJsonBody(req,32000);
        writeJson(res,200,await receive(device,body.text,body.id||randomUUID()));
      }else if(path==='/api/wechat/poll'){
        if(req.method!=='GET')throw new Error('GET required');
        const result=await jsonCall(device,{action:'poll',since:Number(url.searchParams.get('since')||0)});
        const events=result.history.map(m=>({...m,type:m.role==='user'?'user':'assistant'}));
        events.push({type:'busy',value:result.busy});
        if(result.dispatch_attention)events.push({type:'dispatch_attention',...result.dispatch_attention});
        writeJson(res,200,{ok:true,seq:result.seq,busy:result.busy,events});
      }else if(path==='/api/wechat/cancel'){
        writeJson(res,409,{ok:false,error:'任务已进入统一队列，请在电脑管理台查看；此入口不会取消其他通道任务'});
      }else if(path==='/api/wechat/assistant/image'||path==='/api/wechat/assistant/audio'||path==='/api/wechat/assistant/file'){
        if(req.method!=='GET')throw new Error('GET required');
        const response=await call(device,'/api/whale-bridge',{action:path.endsWith('/file')?'attachment':path.endsWith('/audio')?'audio':'image',seq:Number(url.searchParams.get('id'))});
        res.writeHead(response.status,{'content-type':response.type,'content-length':response.data.length,'cache-control':response.status===200?'private, max-age=3600':'no-store','vary':'Cookie','x-content-type-options':'nosniff',...(response.type.startsWith('application/octet-stream')?{'content-disposition':'attachment'}:{})});res.end(response.data);
      }else if(path==='/api/wechat/assistant/manager'||path==='/api/wechat/assistant/manage'){
        const target=path.endsWith('/manager')?'/':url.searchParams.get('path');
        if(!allowedManage(req.method,target)){writeJson(res,403,{ok:false,error:'此操作仅限电脑管理台'});return true;}
        const body=req.method==='POST'?await readJsonBody(req,32000):undefined;
        const response=await call(device,target,body,req.method);
        let data=response.data;
        if(target==='/'&&response.status===200){
          let provider=null;try{provider=JSON.parse(readFileSync(root+'/dispatch-target.json','utf8')).provider;}catch{}
          if(!['codex','dsh','astrbot'].includes(provider))provider=null;
          const boot='<script>window.__WHALE_PROVIDER__='+JSON.stringify(provider)+';window.__WHALE_REMOTE__=true;window.__WHALE_REMOTE_DEVICE__='+JSON.stringify(device).replace(/</g,'\\u003c')+';</script>';
          const mobile='<style>button[onclick*="quitApp"],button[onclick*="wf("],button[onclick*="openAstrbotUi"],button[onclick*="startAstrbot"],button[onclick*="checkCommentLogin"],button[onclick*="Cleanup"],button[onclick*="saveResources"]{display:none!important}header{flex-wrap:wrap}table{display:block;overflow:auto}</style>';
          const banner='<div id="whaleRemoteBanner" style="padding:8px 14px;background:#173653;color:#d6eaff;font-size:12px"><a id="whaleReturnChat" style="color:inherit" href="/wechat">← 返回聊天</a> · 手机远程管理</div><script>if(parent!==window)document.getElementById("whaleRemoteBanner").hidden=true;document.getElementById("whaleReturnChat").onclick=function(){if(parent!==window){parent.postMessage({type:"whale-close-manager"},location.origin);return false;}};</script>';
          data=Buffer.from(data.toString('utf8').replace('<head>','<head>'+boot+mobile).replace('<body>','<body>'+banner).replace('href="/favicon.ico"','href="/api/wechat/avatar/other"'));
        }
        res.writeHead(response.status,{'content-type':response.type,'cache-control':'no-store','x-content-type-options':'nosniff','referrer-policy':'no-referrer'});res.end(data);
      }else writeJson(res,404,{ok:false,error:'Unknown assistant route'});
    }catch(error){writeJson(res,503,{ok:false,error:error.message||'本地助手连接失败'});}
    return true;
  }
  return {installed,receive,handle,announce,enroll};
}
