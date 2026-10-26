"""AstrBot tool adapter; the management app owns durable ComfyUI jobs."""
import asyncio
import copy
import importlib
import sqlite3
import sys
import json
import urllib.request
from pathlib import Path
from astrbot.api import llm_tool
from astrbot.api.event import filter
from astrbot.api.star import Star,register

PROJECT=Path(r'__PROJECT_DIR__')
if str(PROJECT) not in sys.path:sys.path.insert(0,str(PROJECT))
from app import local_images
# AstrBot keeps imported app modules across plugin reloads. This module never
# runs the backend loop here; the management process owns that separate module.
local_images=importlib.reload(local_images)

TOOL='douyin_draw_local_image'


def manager_supports_batches():
    """Do not enable new jobs against an older already-running management app."""
    try:
        op=urllib.request.build_opener(urllib.request.ProxyHandler({}),local_images._NoRedirect())
        with op.open('http://127.0.0.1:8793/api/app/ping',timeout=2) as response:
            value=json.loads(response.read(8192))
        return value.get('local_image_batch_version')==1
    except Exception:return False


@register('douyin_local_image','Local workflow','本地千问批量生图，无需API Key；逐张生成并进入抖音图片队列','1.1.0')
class LocalImage(Star):
    def __init__(self,context,config=None):super().__init__(context)

    @filter.on_llm_request(priority=-9010)
    async def instructions(self,event,req):
        token=local_images.extract_token(event.get_message_str())
        if event.get_platform_name()!='webchat' or not token or not local_images.settings().get('enabled'):
            if req.func_tool is not None:
                req.func_tool=copy.copy(req.func_tool);req.func_tool.remove_tool(TOOL)
            return
        if not await asyncio.to_thread(manager_supports_batches):
            req.system_prompt+='\n本地画图工具当前只支持单张，管理台尚未加载批量版本；不要承诺一次多张，也不要通过重复调用凑数量。可用prompt画一张，结果排队不等于已经发送。不输出内部生图许可。\n'
            return
        req.system_prompt+='\n本轮可使用 douyin_draw_local_image。你可以按聊天需要自主生图，也可以响应主人的画图请求，不必每轮生图。每轮只调用一次工具提交整批，单批1–10张。同一描述画多张用prompt和count；各张不同用prompts列表，每项写完整画面描述（prompt可补充共同风格），不要通过重复调用工具凑数量。不指定数量时默认一张。工具返回排队不等于已出图；程序逐张生成、逐张发图，不并行占显存，无需反复查询或再次调用。不输出内部生图许可、任务编号或本地路径。\n'

    @llm_tool(name=TOOL)
    async def draw(self,event,prompt:str='',count:int=1,prompts:list[str]|None=None):
        """Generate a serial batch of 1-10 images for this private conversation.

        Submit the entire batch in one call. Images are generated one at a time
        and sent automatically as they finish. Never call repeatedly to make a
        batch, and never claim queued images were delivered. Use count for
        variations of one description, or prompts for distinct per-image scenes.

        Args:
            prompt(string): Optional shared description/style. Required when prompts is omitted. Each final image description must be at most 3000 characters.
            count(number): Optional whole number of images, 1-10, default 1. If prompts is supplied, its length determines the quantity; omit count or match that length.
            prompts(list[string]): Optional distinct complete descriptions, one per image, 1-10 entries. Shared prompt is prepended to each. Omit for variations of the same prompt.
        """
        token=local_images.extract_token(event.get_message_str())
        if event.get_platform_name()!='webchat' or not token:return '本轮没有生图许可，请正常回复。'
        try:values=local_images.batch_prompts(prompt,count,prompts)
        except ValueError as exc:return '生图请求未受理：'+str(exc)
        if len(values)>1 and not await asyncio.to_thread(manager_supports_batches):
            return '当前管理台尚未加载批量生图版本，需要正常退出并重新打开管理台后才能一次画多张；未提交任务，不要声称已经排队。'
        def submit():
            db=sqlite3.connect(local_images.ROOT/'assistant.sqlite3',timeout=10)
            try:return local_images.submit_batch(db,token,prompt=prompt,count=count,prompts=prompts)
            finally:db.close()
        try:
            result=await asyncio.to_thread(submit)
            return (f"本轮{result['count']}张图片任务已存在，不要重复提交。" if result.get('duplicate') else f"已受理{result['count']}张图片，后台逐张生成，完成一张自动发一张。不要声称已经生成或送达，不要再次调用本工具。")
        except ValueError as exc:
            return '生图请求未受理：'+str(exc)
        except Exception:
            return '本地生图任务未确认受理，请如实说明暂时无法画图，不要重复调用。'
