"""Shared paragraph splitting for every agent reply path.

AstrBot results and DSH/Codex bridge replies must land in the queue the same
way: blank lines split messages, blank lines inside real code fences do not,
each part is at most MAX_TEXT characters, and a reply is at most MAX_PARTS
parts / MAX_TOTAL_TEXT characters.
"""
import json
import re

from app.reply_limits import MAX_PARTS, MAX_TEXT, MAX_TOTAL_TEXT


def paragraphs(text):
    """Only real line-start fences protect blank lines, not inline backticks."""
    parts = []
    lines = []
    fence = None
    for line in str(text or '').strip().splitlines():
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not marker.group(2).strip():
                fence = None
        if not line.strip() and fence is None:
            if lines:
                parts.append('\n'.join(lines).strip())
                lines = []
        else:
            lines.append(line)
    if lines:
        parts.append('\n'.join(lines).strip())
    return [part for part in parts if part]


def text_parts(text, warn=None):
    """Split text into 1..MAX_TEXT-character parts, reporting long paragraphs."""
    result = []
    for paragraph in paragraphs(text):
        if len(paragraph) > MAX_TEXT and warn:
            warn('长段落按每条 %d 字拆分，未丢弃文字' % MAX_TEXT)
        result.extend({'type': 'text', 'text': paragraph[i:i + MAX_TEXT]}
                      for i in range(0, len(paragraph), MAX_TEXT))
    return result


def finish_parts(text, warn=None):
    """Parts for one finished agent reply, bounded by the same limits as AstrBot."""
    return normalize([{'type':'text','text':text}],warn)


def normalize(parts,warn=None):
    """One text splitting/budget contract, retaining mixed attachment order."""
    if not isinstance(parts,list):raise ValueError('Reply parts must be a list')
    expanded=[]
    for part in parts:
        if not isinstance(part,dict):raise ValueError('Invalid reply part')
        if part.get('type')=='text':expanded.extend(text_parts(part.get('text',''),warn))
        elif part.get('type') in ('image','audio','share'):expanded.append(dict(part))
        else:raise ValueError('Unsupported outgoing part')
    total = 0
    bounded = []
    for part in expanded:
        if len(bounded)>=MAX_PARTS:
            if warn:warn('回复段数超过 %d，只发送前 %d 段' % (MAX_PARTS,MAX_PARTS))
            break
        if part['type'] not in ('text','audio'):bounded.append(part);continue
        text=part.get('text','')
        if not isinstance(text,str):raise ValueError('Invalid reply text')
        remaining=max(0,MAX_TOTAL_TEXT-total)
        if part['type']=='audio':remaining=min(remaining,MAX_TEXT)
        value=text[:remaining]
        if len(value)<len(text) and warn:warn('回复文字超过限制，未发送的文字已截断')
        total+=len(value)
        if value or part['type']=='audio':
            bounded.append({**part,'text':value} if 'text' in part or value else dict(part))
    return bounded


# A model may still announce internal steps before answering; that sentence is
# not a reply. Only a short leading text part is dropped, never the last part.
# The owner asked to keep this off for now: configured per runtime in
# reply-policy.json (drop_process_narration=true re-enables it).
PROCESS_NARRATION = re.compile(
    r'^(?:我(?:先|这就|先去)|好[，,]?我(?:先|这就)|先按流程|让我先|我先去)'
    r'.{0,24}(?:核对|领取|读资料|读一下|资料包|流程|看图|看一下资料)')


def narration_filter_enabled():
    try:
        from app.audio_sender import ROOT
        value = json.loads((ROOT / 'reply-policy.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(value, dict) and value.get('drop_process_narration') is True


def drop_process_narration(parts):
    """Drop one short leading process-announcement paragraph if present."""
    if not narration_filter_enabled():
        return parts
    if not isinstance(parts,list) or len(parts)<2:return parts
    first=parts[0]
    if not isinstance(first,dict) or first.get('type')!='text':return parts
    text=str(first.get('text') or '').strip()
    if len(text)>40 or not PROCESS_NARRATION.match(text):return parts
    return parts[1:]
