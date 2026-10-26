'use strict';
// Reuse the installed DSH SenseVoice model and public sherpa-onnx APIs offline.
const fs = require('node:fs');
const path = require('node:path');
const [moduleDirectory, modelDirectory, vadModel, input, output] = process.argv.slice(2);
if (![moduleDirectory, modelDirectory, vadModel, input, output].every(Boolean)) throw Error('Five explicit local paths required');
const sherpa = require(moduleDirectory);
const wave = sherpa.readWave(input);
if (wave.sampleRate !== 16000 || wave.samples.length > 16000 * 120) throw Error('Expected at most 120 seconds of 16 kHz mono WAV');
const recognizer = new sherpa.OfflineRecognizer({
  featConfig: {sampleRate: 16000, featureDim: 80},
  modelConfig: {senseVoice: {model: path.join(modelDirectory, 'model.int8.onnx'), language: 'auto', useInverseTextNormalization: 1},
    tokens: path.join(modelDirectory, 'tokens.txt'), numThreads: 2, provider: 'cpu', debug: 0}
});
const vad = new sherpa.Vad({sileroVad: {model: vadModel, threshold: 0.5,
  minSilenceDuration: 0.5, minSpeechDuration: 0.25, maxSpeechDuration: 30, windowSize: 512},
  sampleRate: 16000, numThreads: 2, provider: 'cpu', debug: 0}, 32);
const texts = [];
function drain() {
  while (!vad.isEmpty()) {
    const segment = vad.front(false);
    const stream = recognizer.createStream();
    stream.acceptWaveform({sampleRate: 16000, samples: segment.samples});
    recognizer.decode(stream);
    const text = recognizer.getResult(stream).text.replace(/<\|[^|]*\|>/g, '').trim();
    if (text) texts.push(text);
    vad.pop();
  }
}
for (let offset = 0; offset < wave.samples.length; offset += 512) {
  vad.acceptWaveform(wave.samples.subarray(offset, offset + 512));
  drain();
}
vad.flush(); drain();
const text = texts.join(' ').trim();
fs.writeFileSync(output, JSON.stringify({status: text ? 'transcribed' : 'no_speech_recognized', text,
  engine: 'DSH installed SenseVoice / sherpa-onnx (offline)', audio_seconds: wave.samples.length / 16000,
  uncertainty: 'Automatic transcription can be inaccurate; not a permission grant.'}, null, 2), 'utf8');
