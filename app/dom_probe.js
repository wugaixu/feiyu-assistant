// Read-only DOM extraction. Observed selectors, not private application state.
() => {
  const root = document.getElementById('imSaasContainerId');
  if (!root) return {source: 'douyin_web', page_url: location.href, status: 'panel_missing', messages: []};
  const chat = root.querySelector('[data-stack-layer="chat"]') ?? root;
  return {
    source: 'douyin_web',
    page_url: location.href,
    visibility: document.visibilityState,
    observed_at: new Date().toISOString(),
    sender_profile: null, // Verified separately via normal avatar-to-profile navigation.
    conversation_title: chat?.querySelector('.StackLayoutStackChatHeadertitle')?.textContent ?? null,
    conversations: [...root.querySelectorAll('[data-e2e="conversation-item"]')].map(e => e.innerText),
    messages: [...(chat?.querySelectorAll('[data-e2e="msg-item-content"]') ?? [])].map(e => ({
      platform_id: null, // No stable ID observed yet. Do not substitute data-index.
      direction: e.closest('.messageMessageBoxcontentBox')?.classList.contains('messageMessageBoxisFromMe') ? 'outgoing' : 'unverified',
      kind: e.querySelector('.MessageItemShareAwemecontainer,.BulletBulletVideocontainer') ? 'video_card' : e.querySelector('.MessageItemAudioaudioBoxWrapper') ? 'audio' : e.querySelector('.TextMessageTextpureText') ? 'text' : e.querySelector('video') ? 'video' : e.querySelector('img') ? 'image' : 'other',
      images: [...e.querySelectorAll('img')].map(img=>({url:img.currentSrc || img.src, width:img.naturalWidth, height:img.naturalHeight, alt:img.alt})),
      media_sources: [...e.querySelectorAll('audio,video')].map(m=>({kind:m.tagName.toLowerCase(),url:m.currentSrc || m.src})),
      audio_duration: e.querySelector('.MessageItemAudioduration')?.textContent ?? null,
      visible_transcript: e.querySelector('.MessageItemAudiovoiceText')?.textContent ?? null,
      card_author: e.querySelector('.MessageItemShareAwemeauthorName,.BulletBulletVideoauthorName')?.textContent ?? null,
      raw_text: e.innerText,
      links: [...e.querySelectorAll('a[href]')].map(a => ({text: a.innerText, href: a.href})),
      dom: e.outerHTML
      ,ancestors: [...(function*(n) {for(let i=0;n && i<4;i++,n=n.parentElement) yield n;})(e.parentElement)].map(n=>({tag:n.tagName,cls:n.className,attrs:[...n.attributes].filter(a=>a.name.startsWith('data-')).map(a=>[a.name,a.value])}))
    }))
  };
}
