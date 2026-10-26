"""Use the pinned MIT upstream DOM reader without another browser or model."""
from pathlib import Path

READER = (Path(__file__).resolve().parents[1] / 'vendor' / 'dsh-douyin-agent' / 'read-page-state.js').read_text(encoding='utf-8')


def observe(page):
    state = page.evaluate('(' + READER.strip() + ')')
    state['verificationRequired'] = bool(state.get('verificationRequired') or page.locator('input[placeholder="请输入验证码"]:visible').count())
    state['reader_source'] = 'LockyBee/dsh-douyin-agent@59134e5'
    return state


def compact(state):
    """Bound routine observations; detailed DOM probes remain opt-in."""
    return {
        'url': state.get('url'), 'title': state.get('title'),
        'text': state.get('visibleText', '')[:4000],
        'video': state.get('video'),
        'cards': [{'index': item['index'], 'video_id': item.get('id'),
                   'text': item.get('title', '')[:400], 'author': item.get('author')}
                  for item in state.get('searchResults', [])[:20]],
        'login_required': bool(state.get('loginRequired')),
        'verification_required': bool(state.get('verificationRequired')),
        'private_chat_visible': bool(state.get('privateChatVisible')),
        'reader_source': state.get('reader_source'),
    }
