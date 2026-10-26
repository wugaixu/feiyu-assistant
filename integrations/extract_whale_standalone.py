"""One-time source extraction from the user's MIT Whale plugin.

The resulting gateway is checked into this project. This generator is a developer
tool only: running the gateway never loads DSH files, modules, services or keys.
"""
from pathlib import Path
import shutil

SOURCE=Path(r'__DSH_HOME__\wechat-chat\plugin')
DEST=Path(__file__).resolve().parent/'whale_gateway'


def remove_between(text,start,end):
    a=text.index(start);b=text.index(end,a)
    return text[:a]+text[b:]


def main():
    DEST.mkdir(exist_ok=True)
    original=(SOURCE/'lib/index.js').read_text(encoding='utf-8')
    # Pairing, password protection, restricted tunnel and metadata persistence.
    prefix=original[original.index('import { readFileSync'):original.index('/* ── session-map persistence')]
    stores=original[original.index('const HIDDEN_FILE'):original.index('/* ── DeepSeek 余额')]
    app=original[original.index('export function apply(ctx, config = {}) {'):]
    prefix=prefix.replace("import { inspectPcmWav, LocalSttManager } from './stt.js'","import { inspectPcmWav } from './wav.mjs'\nimport { LocalSpeech } from './speech.mjs'")
    prefix=prefix.replace("import { createDshSttEngine, dshSpeechService } from './host-stt.js'\n",'')
    prefix=prefix.replace("import { createAssistantBridge } from './assistant-bridge.mjs'","import { createAssistantBridge } from '../whale_assistant_bridge.mjs'\nimport { pathToFileURL } from 'node:url'")
    prefix=prefix.replace("export const name = 'wechat-chat'\nexport const inject = ['sessionController', 'webServer']",'')
    prefix=prefix.replace("const home = process.env.DSH_HOME || join(homedir(), '.dsh')","const home = process.env.WHALE_HOME || '__RUNTIME_ROOT__/whale-data'")
    prefix=prefix.replace("const CREDENTIALS_FILE = join(home, '.credentials.yaml')",'')
    prefix=prefix.replace("const SESSION_MAP_FILE = join(home, 'wechat-chat-devices.json')",'')
    prefix=remove_between(prefix,'function extractText(msg)', 'export class PairingService')
    prefix=prefix.replace("const mod = await import('cloudflared')","const mod = await import(pathToFileURL('__RUNTIME_ROOT__/tools/whale/cloudflared/lib/lib.js').href)")
    prefix=prefix.replace("const CLOUDFLARED_BIN = join(home, 'wechat-chat', process.platform === 'win32' ? 'cloudflared.exe' : 'cloudflared')","const CLOUDFLARED_BIN = '__RUNTIME_ROOT__/tools/whale/cloudflared.exe'")
    app=app.replace('export function apply(ctx, config = {}) {','export function startGateway(host, config = {}) {')
    app=app.replace('const assistantBridge = createAssistantBridge()','const assistantBridge = createAssistantBridge(process.env.WHALE_RUNTIME_ROOT)')
    for line in ["  const sessionTitle = config.title || DEFAULT_TITLE\n","  const reuseSession = config.reuseSession !== false\n","  const provider = config.provider || 'deepseek-official'\n","  const model = config.model || 'deepseek-v4-flash'\n","  const reasoningEffort = config.reasoningEffort || 'low'\n"]:app=app.replace(line,'')
    app=remove_between(app,'  // 语音识别引擎：','  tunnel.onPhase(')
    app=app.replace('  tunnel.onPhase(','  const stt = new LocalSpeech()\n  tunnel.onPhase(',1)
    app=remove_between(app,'  const running = new Map()','  let voiceUploadActive')
    app=remove_between(app,'  function broadcast(device, payload) {','  /* ── 语音条：')
    app=remove_between(app,'      if (running.has(sessionKey)) {','    } catch (error) {\n      voice.update')
    voice_point='    } catch (error) {\n      voice.update'
    app=app.replace(voice_point,"      if (!deviceId || !service.touchDevice(deviceId)) throw new Error('配对设备已失效')\n      await assistantBridge.receive(deviceId, `【语音】${text}`, createHash('sha256').update('voice:'+id).digest('hex').slice(0,32))\n      voice.update(id, { status: 'sent' })\n"+voice_point,1)
    app=remove_between(app,"    if (pathname === '/api/wechat/state') {","    if (pathname === '/api/wechat/hide') {")
    app=remove_between(app,"    if (pathname === '/api/wechat/cancel') {","    writeJson(res, 404, { ok: false, code: 'not-found' })\n  }\n\n  /* ── 固定公网地址")
    app=remove_between(app,"    if (pathname === '/api/wechat/balance') {","    if (pathname === '/api/wechat/voice/send') {")
    app=app.replace('ctx.webServer','host.webServer').replace('ctx.effect','host.effect')
    app=app.replace('  assistantBridge.announce(webPort)','  assistantBridge.announce(webPort)')
    text='// Derived from dsh-wechat-chat (MIT). Independent transport, no agent runtime.\n'+prefix+stores+app
    # The newer plugin's Funnel helper lives between stores and apply().
    # Preserve it; otherwise an extracted gateway silently cannot start Funnel.
    helper=original[original.index('export function funnelArgs('):original.index('export function apply(')]
    text=text.replace('export function startGateway(',helper+'export function startGateway(',1)
    # Remove obsolete preset declarations/comments; only transport code remains.
    a=text.index('// 手机端专用 agent preset') if '// 手机端专用 agent preset' in text else -1
    if a>=0:text=text[:a]+text[text.index('const COOKIE_NAME',a):]
    text=text.replace("const AGENT_PRESET = 'wechat-chat'",'')
    if any(word in text for word in ('ctx.','sessionController','speechToText','DSH_HOME','.credentials.yaml')):raise ValueError('Runtime dependency remains')
    (DEST/'core.mjs').write_text(text,encoding='utf-8')
    stt=(SOURCE/'lib/stt.js').read_text(encoding='utf-8')
    wav='const MAX_WAV_BYTES=2100000, MAX_SECONDS=60;\n'+stt[stt.index('export function inspectPcmWav'):stt.index('export class LocalSttManager')]
    (DEST/'wav.mjs').write_text(wav,encoding='utf-8')
    for name in ('voice.js','qrcode.js','chat-page.html','panel-page.html'):
        # Pages have independent features now (including outgoing audio). Never
        # overwrite maintained project UI by rerunning this historical extractor.
        if name.endswith('.html') and (DEST/name).exists():continue
        shutil.copy2(SOURCE/'lib'/name,DEST/name)
    page=DEST/'chat-page.html'
    page.write_text(page.read_text(encoding='utf-8').replace("nickname: 'DSH助手'","nickname: '大肥鱼'"),encoding='utf-8')
    panel=DEST/'panel-page.html';ui=panel.read_text(encoding='utf-8')
    start=ui.index('  function renderStt(info) {');end=ui.index('  function pollStt()',start)
    ui=ui[:start]+'''  function renderStt(info) {
    info=info||{};
    sttStatus.textContent=info.ready?'已就绪':'需在电脑修复模型';
    sttStatus.className=info.ready?'on':'';
    sttProgress.style.width=info.ready?'100%':'0%';
    sttInstall.style.display='none';sttCancel.style.display='none';
    sttMsg.textContent='使用抖音助手项目本地的 SenseVoice，不需要启动 DSH，也不需要 API Key。';
  }

'''+ui[end:]
    panel.write_text(ui,encoding='utf-8')
    shutil.copy2(SOURCE/'LICENSE',DEST/'LICENSE')
    print('Independent gateway sources extracted; no DSH runtime references')


if __name__=='__main__':main()
