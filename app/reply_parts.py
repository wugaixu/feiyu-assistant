"""Shared paragraph splitting for every agent reply path.

AstrBot results and DSH/Codex bridge replies must land in the queue the same
way: blank lines split messages, blank lines inside real code fences do not,
each part is at most MAX_TEXT characters, and a reply is at most MAX_PARTS
parts / MAX_TOTAL_TEXT characters.
"""
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
    parts = text_parts(text, warn)
    total = 0
    bounded = []
    for part in parts:
        remaining = MAX_TOTAL_TEXT - total
        if remaining <= 0:
            if warn:
                warn('回复文字超过 %d 字，仅保留前 %d 字' % (MAX_TOTAL_TEXT, MAX_TOTAL_TEXT))
            break
        value = part['text'][:remaining]
        total += len(value)
        bounded.append({'type': 'text', 'text': value})
    if len(bounded) > MAX_PARTS:
        if warn:
            warn('回复段数超过 %d，只发送前 %d 段' % (MAX_PARTS, MAX_PARTS))
        bounded = bounded[:MAX_PARTS]
    return bounded
