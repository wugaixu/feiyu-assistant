const MAX_WAV_BYTES=2100000, MAX_SECONDS=60;
export function inspectPcmWav(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 44 || buffer.length > MAX_WAV_BYTES) throw new Error('WAV 文件大小无效')
  if (buffer.toString('ascii', 0, 4) !== 'RIFF' || buffer.toString('ascii', 8, 12) !== 'WAVE') throw new Error('不是有效的 WAV 文件')
  if (buffer.readUInt32LE(4) + 8 !== buffer.length) throw new Error('WAV RIFF 长度无效')
  let offset = 12
  let format
  let data
  while (offset + 8 <= buffer.length) {
    const id = buffer.toString('ascii', offset, offset + 4)
    const size = buffer.readUInt32LE(offset + 4)
    const start = offset + 8
    const end = start + size
    if (end > buffer.length) throw new Error('WAV 数据不完整')
    if (id === 'fmt ' && size >= 16) {
      format = {
        codec: buffer.readUInt16LE(start),
        channels: buffer.readUInt16LE(start + 2),
        sampleRate: buffer.readUInt32LE(start + 4),
        byteRate: buffer.readUInt32LE(start + 8),
        blockAlign: buffer.readUInt16LE(start + 12),
        bits: buffer.readUInt16LE(start + 14),
      }
    } else if (id === 'data') data = { start, size }
    offset = end + (size % 2)
  }
  if (!format || !data) throw new Error('WAV 缺少 fmt 或 data 区块')
  if (format.codec !== 1 || format.channels !== 1 || format.sampleRate !== 16000 || format.bits !== 16 || format.blockAlign !== 2 || format.byteRate !== 32000) {
    throw new Error('仅支持 16kHz、16-bit、单声道 PCM WAV')
  }
  if (data.size < 3200 || data.size % 2 !== 0) throw new Error('录音过短或数据无效')
  const durationMs = Math.round(data.size / format.byteRate * 1000)
  if (durationMs > MAX_SECONDS * 1000 + 250) throw new Error(`录音不能超过 ${MAX_SECONDS} 秒`)
  return { durationMs, dataBytes: data.size }
}

