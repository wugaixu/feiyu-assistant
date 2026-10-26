// Local speech recognizer only. No network, browser, session or LLM calls.
const fs=require('node:fs'),path=require('node:path');
let input='';process.stdin.setEncoding('utf8');process.stdin.on('data',x=>input+=x);
process.stdin.on('end',()=>{
  try {
    const req=JSON.parse(input);
    const sherpa=require(path.join(req.runtime,'node_modules','sherpa-onnx-node'));
    const recognizer=new sherpa.OfflineRecognizer({featConfig:{sampleRate:16000,featureDim:80},modelConfig:{
      senseVoice:{model:path.join(req.runtime,'models','model.int8.onnx'),language:'auto',useInverseTextNormalization:1},
      tokens:path.join(req.runtime,'models','tokens.txt'),numThreads:2,provider:'cpu',debug:0}});
    const clean=x=>String(x||'').replace(/<\|([^|]+)\|>/g,'$1');
    const clips=req.files.map(file=>{
      const wave=sherpa.readWave(file);
      if(wave.sampleRate!==16000)throw Error('Expected 16kHz WAV');
      const segments=[];
      for(let from=0;from<wave.samples.length;from+=30*16000){
        const samples=wave.samples.subarray(from,Math.min(from+30*16000,wave.samples.length));
        if(samples.length<1600)continue;
        let power=0;for(const x of samples)power+=x*x;
        if(power/samples.length<1e-10)continue;
        const stream=recognizer.createStream();stream.acceptWaveform({sampleRate:16000,samples});recognizer.decode(stream);
        const r=recognizer.getResult(stream);
        segments.push({start:from/16000,end:(from+samples.length)/16000,text:String(r.text||'').replace(/<\|[^|]+\|>/g,'').trim(),
          language:clean(r.lang),emotion:clean(r.emotion),event:clean(r.event)});
      }
      return {file,segments};
    });
    process.stdout.write(JSON.stringify({ok:true,clips}));
  }catch(error){process.stderr.write(String(error.message));process.exitCode=1;}
});
