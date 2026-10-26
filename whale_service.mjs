/** Management-owned independent Whale gateway. No DSH import, process or service. */
import {createServer} from 'node:http';
import {readFileSync,mkdirSync} from 'node:fs';
import {timingSafeEqual} from 'node:crypto';
import {pathToFileURL} from 'node:url';

export function gatewaySettings(config,{network,autoTunnel}={}){
  const mode=network??(autoTunnel===false?'disabled':config.network?.mode||'tailscale_funnel');
  if(!['tailscale_funnel','disabled'].includes(mode))throw new Error('Unsupported Whale network mode');
  const publicBaseUrl=config.network?.public_base_url;
  if(publicBaseUrl){
    const url=new URL(publicBaseUrl);
    if(url.protocol!=='https:'||!url.hostname.endsWith('.ts.net')||url.username||url.password||url.pathname!=='/'||url.search||url.hash||url.port)throw new Error('Invalid Tailscale public address');
  }
  return {nickname:'大肥鱼',autoTunnel:false,funnel:mode==='tailscale_funnel',...(publicBaseUrl?{publicBaseUrl}:{}),stt:{engine:'project_local'}};
}

export async function startService({port=8795,root=process.env.WHALE_RUNTIME_ROOT||'__RUNTIME_ROOT__',network,autoTunnel}={}){
  process.env.WHALE_RUNTIME_ROOT=root;
  process.env.WHALE_HOME=root+'/whale-data';
  mkdirSync(process.env.WHALE_HOME,{recursive:true});
  const config=JSON.parse(readFileSync(root+'/whale-channel.json','utf8'));
  if(!config.installed||!config.standalone)throw new Error('Independent gateway is not configured');
  const options=gatewaySettings(config,{network,autoTunnel});
  const {startGateway}=await import('./integrations/whale_gateway/core.mjs');
  const exact=new Map(),prefix=new Map(),disposers=[];
  let closing=false;
  const same=(a,b)=>typeof a==='string'&&Buffer.byteLength(a)===Buffer.byteLength(b)&&timingSafeEqual(Buffer.from(a),Buffer.from(b));
  const shutdown=async()=>{
    if(closing)return;closing=true;
    for(const dispose of disposers.reverse()){try{await dispose();}catch{}}
    server.closeIdleConnections?.();
    await new Promise(resolve=>{server.close(resolve);setTimeout(()=>{server.closeAllConnections?.();resolve();},2500).unref();});
  };
  const server=createServer(async(req,res)=>{
    const path=new URL(req.url||'/','http://whale.local').pathname;
    if(path==='/health'&&req.method==='GET'){
      res.writeHead(200,{'content-type':'application/json','cache-control':'no-store'});res.end(JSON.stringify({ok:true,standalone:true,service:'feiyu-whale',pid:process.pid,network:options.funnel?'tailscale_funnel':'disabled'}));return;
    }
    if(path==='/shutdown'&&req.method==='POST'){
      let secret='';try{secret=readFileSync(root+'/whale-bridge-secret.txt','utf8').trim();}catch{}
      if(!secret||!same(req.headers['x-whale-bridge'],secret)){res.writeHead(403);res.end();return;}
      res.writeHead(200,{'content-type':'application/json'});res.end('{"ok":true}');void shutdown();return;
    }
    const handler=exact.get(path)||[...prefix].sort((a,b)=>b[0].length-a[0].length).find(([p])=>path===p||path.startsWith(p+'/'))?.[1];
    if(!handler){res.writeHead(404);res.end();return;}
    try{await handler(req,res);}catch{if(!res.headersSent)res.writeHead(500,{'content-type':'application/json'});res.end('{"ok":false,"error":"Gateway request failed"}');}
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(port,'127.0.0.1',resolve);});
  const bound=server.address().port;
  try{
    startGateway({webServer:{port:bound,register({kind,path,handler}){const map=kind==='exact'?exact:prefix;if(map.has(path))throw new Error('Duplicate route');map.set(path,handler);return()=>map.delete(path);}},effect(fn){const dispose=fn();if(typeof dispose==='function')disposers.push(dispose);}},
      options);
  }catch(error){await shutdown();throw error;}
  process.once('SIGINT',()=>void shutdown());process.once('SIGTERM',()=>void shutdown());
  return {server,port:bound,shutdown};
}

if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href){
  startService().catch(error=>{console.error('Whale gateway startup failed:',error.message);process.exitCode=1;});
}
