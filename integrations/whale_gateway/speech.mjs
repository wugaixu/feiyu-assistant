/** Offline speech adapter into this project's own SenseVoice installation. */
import {existsSync} from 'node:fs';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const ROOT='__RUNTIME_ROOT__';
export class LocalSpeech {
  constructor(){this.child=null;}
  ready(){return existsSync(ROOT+'/tools/sensevoice/models/model.int8.onnx')&&existsSync(ROOT+'/tools/sensevoice/node_modules/sherpa-onnx-node/package.json');}
  info(){return {ready:this.ready(),engine:'local_sensevoice',phase:this.child?'transcribing':'idle'};}
  startInstall(){return false;}
  cancelInstall(){return false;}
  dispose(){this.child?.kill();this.child=null;}
  async transcribe(wav){
    if(!this.ready())throw new Error('项目本地 SenseVoice 未就绪');
    if(this.child)throw new Error('正在识别上一条语音');
    if(!Buffer.isBuffer(wav)||wav.length>2100000)throw new Error('语音格式或大小无效');
    return await new Promise((resolve,reject)=>{
      const child=spawn('__PYTHON__',['-B','-X','utf8','-m','app.whale_speech'],{cwd:fileURLToPath(new URL('../../',import.meta.url)),windowsHide:true,stdio:['pipe','pipe','pipe']});
      this.child=child;let stdout='',stderr='';
      const timer=setTimeout(()=>{child.kill();reject(new Error('本地语音识别超时'));},150000);
      child.stdout.on('data',c=>{stdout+=c;if(stdout.length>200000)child.kill();});
      child.stderr.on('data',c=>{stderr=(stderr+c).slice(-1000);});
      child.on('error',error=>{clearTimeout(timer);this.child=null;reject(error);});
      child.on('close',code=>{clearTimeout(timer);this.child=null;try{if(code!==0)throw new Error('本地语音识别失败');resolve(JSON.parse(stdout));}catch(e){reject(e);}});
      child.stdin.on('error',()=>{});child.stdin.end(JSON.stringify({audio:wav.toString('base64')}));
    });
  }
}
