"""Reproducible local WebChat adaptation for the pinned GPT Image plugin.

Run against a reviewed upstream main.py, never silently patch a new version.
"""
import ast

UPSTREAM_COMMIT = 'bd7adb7db539c3223b4ad1bdaad9a309ab2cca43'
MARKER = '# douyin-local-image-adapter-v1'


def adapt(source):
    if MARKER in source:
        return source
    def replace(old, new):
        nonlocal source
        if source.count(old) != 1:
            raise ValueError('GPT Image upstream changed; review before applying adapter')
        source = source.replace(old, new)
    replace('import asyncio\n', 'import asyncio\nimport copy\n'+MARKER+'\n')
    replace('        if not prompt or not prompt.strip():', '''        if not self._local_image_allowed(event):
            return "本轮未开放图片生成；请继续正常文字回复。"
        if not prompt or not prompt.strip():''')
    replace('''        Use this tool only when the user explicitly asks you to generate or edit an
        image now.''', '''        You may generate an image when it naturally helps the current private
        conversation, or when the user asks for one. Do not generate on every turn.''')
    replace('''        background-task status, respond in your persona and naturally let the user know
        you accepted the image task and started working on it.''', '''        a failure, explain it briefly in your persona. An image attachment is
        returned within this request; do not claim it arrived on the owner's phone.''')
    replace('        wait_seconds = max(0, self._int_cfg("runtime", "llm_tool_foreground_wait_seconds", 0))\n', '')
    start = source.index('        try:\n            if wait_seconds == 0:')
    end = source.index('        except Exception as exc:', start)
    source = source[:start]+'''        try:
            # WebChat consumes attachments only while this request is open.
            # Never leave a charged generation detached from its reply stream.
            image_path, revised_prompt = await asyncio.wait_for(task, timeout=110)
        except asyncio.TimeoutError:
            return "图片生成等待超时，结果未确认；不要自动重试或声称图片已发出。"
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
'''+source[end:]
    replace('''        guide = self._str_cfg("runtime", "tool_instruction", "") or DEFAULT_TOOL_GUIDE''', '''        if not self._local_image_allowed(event):
            if req.func_tool is not None:
                req.func_tool = copy.copy(req.func_tool)
                req.func_tool.remove_tool(TOOL_NAME)
            return
        guide = self._str_cfg("runtime", "tool_instruction", "") or DEFAULT_TOOL_GUIDE''')
    replace('    async def _generate_image(\n', '''    def _local_image_allowed(self, event):
        # This prefix is produced by the local worker, before untrusted owner
        # text/materials. Scheduled/public tasks have different outer prompts.
        # Restriction can only disable calls; it never grants system privileges.
        return bool(self._str_cfg("api", "api_key", "").strip()) and (
            event.get_platform_name() == "webchat"
            and event.get_message_str().startswith("这是已核验主人的抖音私聊。")
        )

    async def _generate_image(
''')
    ast.parse(source)
    return source
