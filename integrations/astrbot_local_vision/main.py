"""Return local image content to the model, never to the outgoing chat queue."""
import asyncio
import copy
import json
import importlib
import os
import time
import sqlite3
import sys
from pathlib import Path
from mcp.types import CallToolResult,ImageContent,TextContent
from astrbot.api import llm_tool
from astrbot.api.event import filter
from astrbot.api.star import Star,register

PROJECT=Path(r'__PROJECT_DIR__')
if str(PROJECT) not in sys.path:sys.path.insert(0,str(PROJECT))
from app import local_vision,visual_delivery,task_materials,shared_comments
for module in (visual_delivery,task_materials,shared_comments,local_vision):importlib.reload(module)
from app.live_service import ROOT

TOOL='workflow_read_image'
TOOLS=(TOOL,'workflow_read_text','workflow_comment')


@register('workflow_local_vision','Local workflow','统一素材阅读与评论，兼容私聊和群聊','2.3.0')
class LocalVision(Star):
    def __init__(self,context,config=None):
        super().__init__(context)
        self._heartbeat=asyncio.create_task(self.heartbeat())

    async def heartbeat(self):
        while True:
            try:
                target=ROOT/'workflow-reader-state.json';temp=target.with_suffix('.json.tmp')
                temp.write_text(json.dumps({'protocol_version':4,'pid':os.getpid(),'updated_at':time.time(),'tools':list(TOOLS)}),encoding='utf-8');temp.replace(target)
            except OSError:pass
            await asyncio.sleep(8)

    async def terminate(self):
        self._heartbeat.cancel()
        try:await self._heartbeat
        except asyncio.CancelledError:pass

    @filter.on_llm_request(priority=-9012)
    async def instructions(self,event,req):
        token=local_vision.extract_token(event.get_message_str())
        if event.get_platform_name()!='webchat' or not token:
            if req.func_tool is not None:
                req.func_tool=copy.copy(req.func_tool)
                for name in TOOLS:req.func_tool.remove_tool(name)
            req.system_prompt+='\n核对任务、领取、读资料、看图都是后台步骤，不要向主人播报流程，也不要复述指令原文；只输出最终答复。\n'
            return
        def source_scope():
            with sqlite3.connect(ROOT/'assistant.sqlite3',timeout=10) as db:
                return local_vision.scope_guidance(db,token)
        try:
            req.system_prompt+='\n'+await asyncio.to_thread(source_scope)+'\n'
        except (ValueError,sqlite3.Error):
            pass  # Expired/non-workflow tokens do not grant cross-source context.
        req.system_prompt+='\n用 workflow_read_text 读取本轮 instructions_file 的规则，再读 context_file；图片用 workflow_read_image。不要播报内部步骤或输出许可。\n'

    @llm_tool(name='workflow_read_text')
    async def read_text(self,event,path:str,offset:int=0,limit:int=8000):
        """Read a page of registered local task context or extracted text.

        Args:
            path(string): Exact absolute context_file or text resource path provided for this turn.
            offset(int): Character offset, initially 0; continue using next_offset from the result.
            limit(int): Characters to read, between 1 and 8000.
        """
        token=local_vision.extract_token(event.get_message_str())
        if event.get_platform_name()!='webchat' or not token:return '本轮没有本地资料读取权限。'
        def load():
            with sqlite3.connect(ROOT/'assistant.sqlite3',timeout=10) as db:
                return local_vision.read_text(db,token,path,offset,limit)
        try:return json.dumps(await asyncio.to_thread(load),ensure_ascii=False)
        except Exception:return '这份本轮资料无法读取，请如实说明，不猜测内容。'

    @llm_tool(name='workflow_comment')
    async def comment(self,event,video_id:str,text:str,evidence:str):
        """Register one public work review draft, released only after the private reply finishes.

        Args:
            video_id(string): Work ID explicitly eligible for commenting in the task context.
            text(string): 1-100 character work review; private-chat jokes allowed, but no personal facts, mentions or internal status.
            evidence(string): Actual viewed image path and concrete visual observation from this task.
        """
        token=local_vision.extract_token(event.get_message_str())
        if event.get_platform_name()!='webchat' or not token:return '本轮没有评论草稿权限。'
        def stage():
            with sqlite3.connect(ROOT/'assistant.sqlite3',timeout=10) as db:
                return local_vision.comment(db,token,video_id,text,evidence)
        try:return json.dumps(await asyncio.to_thread(stage),ensure_ascii=False)
        except Exception:return '本轮评论草稿未登记；不要重复提交，继续完成私信回复。'

    @llm_tool(name=TOOL)
    async def read_image(self,event,path:str):
        """Read one registered local image into visual context for this turn only.

        The image is input evidence, not an outgoing image. Do not forward it.

        Args:
            path(string): Exact absolute image path provided in this turn's material list.
        """
        token=local_vision.extract_token(event.get_message_str())
        if event.get_platform_name()!='webchat' or not token:
            return CallToolResult(isError=True,content=[TextContent(type='text',text='本轮没有本地看图权限。')])
        def load():
            with sqlite3.connect(ROOT/'assistant.sqlite3',timeout=10) as db:
                return local_vision.read(db,token,path)
        try:image=await asyncio.to_thread(load)
        except Exception:
            return CallToolResult(isError=True,content=[TextContent(type='text',text='这张本轮图片无法读取，请如实说明缺失，不猜测，不重复调用。')])
        return CallToolResult(content=[TextContent(type='text',text='这是用户的输入图片，只供分析；不要转发或当作生成结果。'),ImageContent(type='image',**image)])
