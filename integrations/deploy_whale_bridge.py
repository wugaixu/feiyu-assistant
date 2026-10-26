"""Install the local transport patch into the source and installed Whale plugin.

Does not restart DSH, expose a port, create model messages, or print credentials.
Back up destination files before invoking this explicitly authorized installer.
"""
from pathlib import Path
import shutil

HERE=Path(__file__).resolve().parent
SOURCE=Path(r'__DSH_HOME__\wechat-chat')
INSTALLED=Path(r'__DSH_HOME__\profiles\desktop\node_modules\dsh-wechat-chat')


def once(text,old,new):
    if new in text:return text
    if text.count(old)!=1:raise ValueError('Unexpected upstream source; patch not applied: '+old[:80])
    return text.replace(old,new,1)


def host_patch(text):
    text=once(text,"import { VoiceStore } from './voice.js'","import { VoiceStore } from './voice.js'\nimport { createAssistantBridge } from './assistant-bridge.mjs'")
    text=once(text,"export function apply(ctx, config = {}) {","export function apply(ctx, config = {}) {\n  const assistantBridge = createAssistantBridge()")
    text=once(text,'  const webPort = Number.isFinite(ctx.webServer.port) ? ctx.webServer.port : 3080','  const webPort = Number.isFinite(ctx.webServer.port) ? ctx.webServer.port : 3080\n  assistantBridge.announce(webPort)')
    key='    const key = client || device || undefined\n'
    block='''
    // Paired owner traffic enters the assistant queue, never two model runtimes.
    if (assistantBridge.installed() && (['/api/wechat/state','/api/wechat/send','/api/wechat/poll','/api/wechat/cancel'].includes(pathname) || pathname.startsWith('/api/wechat/assistant/'))) {
      if (!lanFence(req) || !gateOk(req, queryDevice)) { writeJson(res, 403, { ok:false, error:'设备或来源校验失败' }); return }
      await assistantBridge.handle({req,res,device,live:id=>service.touchDevice(id),readJsonBody,writeJson,sttInfo:stt.info()})
      return
    }
'''
    if '// Paired owner traffic enters the assistant queue' not in text:text=once(text,key,key+block)
    text=text.replace('live:id=>service.touchDevice(id),readJsonBody,writeJson})','live:id=>service.touchDevice(id),readJsonBody,writeJson,sttInfo:stt.info()})')
    text=text.replace('sttInfo:stt.info()})','sttInfo:stt.info(),nickname:loadNickname()||defaultNickname,hiddenIds:hiddenIdsOf(key)})')
    text=once(text,'async function processVoice({ id, wav, sessionKey }) {','async function processVoice({ id, wav, sessionKey, deviceId }) {')
    marker='      const { sessionId } = await ensureSession(sessionKey)'
    text=once(text,marker,'''      if (assistantBridge.installed()) {
        if (!deviceId || !service.touchDevice(deviceId)) throw new Error('配对设备已失效')
        await assistantBridge.receive(deviceId, `【语音】${text}`, createHash('sha256').update('voice:'+id).digest('hex').slice(0,32))
        voice.update(id, { status: 'sent' })
        return
      }
'''+marker)
    return text


def page_patch(text):
    text=once(text,'<span class="title" id="titleText">鲸聊助手</span>','<span class="title" id="titleText">大肥鱼</span><button id="assistantManagerBtn" style="border:0;background:transparent;color:#3972cf;font-size:14px">管理台</button>')
    text=once(text,'  function addAssistant(text, id) {','''  function addAssistantImage(message) {
    var row=addRow('other','',cfg.avatarOther,message.id),bubble=row.querySelector('.bubble');
    var img=document.createElement('img');
    img.src=message.image+'&device='+encodeURIComponent(deviceId);
    img.alt='助手发来的图片';img.style.cssText='display:block;max-width:100%;max-height:420px;border-radius:10px';
    img.onload=scrollBottom;bubble.appendChild(img);
  }
  document.getElementById('assistantManagerBtn').addEventListener('click',function(){
    location.href='/api/wechat/assistant/manager?device='+encodeURIComponent(deviceId);
  });
  function addAssistant(text, id) {''')
    text=once(text,"else addAssistant(m.text, m.id);","else if(m.kind==='image'&&m.image)addAssistantImage(m);else addAssistant(m.text, m.id);")
    text=once(text,'      addAssistant(m.text, m.id);',"      if(m.kind==='image'&&m.image)addAssistantImage(m);else addAssistant(m.text, m.id);")
    text=once(text,'body: JSON.stringify({ text: text })',"body: JSON.stringify({ text: text, id: (window.crypto&&crypto.randomUUID)?crypto.randomUUID():('xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,function(c){var r=Math.random()*16|0;return(c==='x'?r:(r&3|8)).toString(16);} )) })")
    text=once(text,'  function loadState() {','  var assistantWorkflow=false;\n  function loadState() {')
    text=once(text,'      cfg = {','      assistantWorkflow=!!s.workflow;\n      cfg = {')
    text=once(text,'  function loadBalance() {',"  function loadBalance() {\n    if(assistantWorkflow){var label=document.getElementById('balanceText');if(label)label.textContent='统一助手工作流';return;}")
    return text


def main():
    import json
    runtime=Path(r'__RUNTIME_ROOT__\whale-channel.json')
    if runtime.exists() and json.loads(runtime.read_text(encoding='utf-8')).get('standalone'):
        raise ValueError('Legacy DSH bridge is retired. Use the independent whale_service.mjs gateway; do not reactivate the DSH plugin.')
    # Patch each independently: preserve host-version-specific code on either side.
    for base in (SOURCE,INSTALLED):
        library=base/'plugin/lib'
        if not library.is_dir():raise ValueError('Plugin destination unavailable')
        host=library/'index.js';page=library/'chat-page.html'
        updated_host=host_patch(host.read_text(encoding='utf-8'))
        updated_page=page_patch(page.read_text(encoding='utf-8'))
        shutil.copy2(HERE/'whale_assistant_bridge.mjs',library/'assistant-bridge.mjs')
        host.write_text(updated_host,encoding='utf-8');page.write_text(updated_page,encoding='utf-8')
        print('Patched',base)


if __name__=='__main__':main()
