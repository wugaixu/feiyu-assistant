/** Probe real Funnel ingress IPs, bypassing MagicDNS/self access. No credentials. */
import {connect as tlsConnect} from 'node:tls';
import {isIP} from 'node:net';

export async function resolvePublicAddresses(host) {
  const response=await fetch('https://dns.google/resolve?name='+encodeURIComponent(host)+'&type=A',{signal:AbortSignal.timeout(8000)});
  if(!response.ok)return null;
  const value=await response.json();
  if(value.Status!==0&&value.Status!==3)return null;
  return [...new Set((value.Answer||[]).filter(x=>x.type===1&&isIP(x.data)===4).map(x=>x.data))].slice(0,2);
}

export function handshake(ip,host) {
  return new Promise(resolve=>{
    let settled=false;
    const finish=value=>{if(settled)return;settled=true;socket.destroy();resolve(value);};
    const socket=tlsConnect({host:ip,port:443,servername:host,rejectUnauthorized:true});
    socket.setTimeout(5000,()=>finish(false));
    socket.once('secureConnect',()=>finish(socket.authorized));
    socket.once('error',()=>finish(false));
    socket.once('close',()=>finish(false));
  });
}

export async function probePublicIngress(host,{resolveAddresses=resolvePublicAddresses,connect=handshake}={}) {
  if(typeof host!=='string'||!host.endsWith('.ts.net')||!host.split('.').every(x=>/^[a-z0-9-]+$/i.test(x)))return {reachable:null,state:'unverified'};
  try {
    const addresses=await resolveAddresses(host);
    if(addresses===null)return {reachable:null,state:'unverified'};
    if(!addresses.length)return {reachable:false,state:'dns_missing'};
    for(const ip of addresses)if(await connect(ip,host))return {reachable:true,state:'reachable'};
    return {reachable:false,state:'tls_unreachable'};
  } catch {return {reachable:null,state:'unverified'};}
}
