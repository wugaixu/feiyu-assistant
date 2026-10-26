"""Offline tests of the actual pinned plugin methods, without model/API calls."""
import ast
import asyncio
import copy
import logging
from pathlib import Path
import types
import unittest
from unittest.mock import patch
from integrations.gpt_image_patch import adapt

SOURCE = Path(__file__).resolve().parent/'third_party/gpt_image_upstream/main.py'


def plugin_class():
    tree=ast.parse(adapt(SOURCE.read_text(encoding='utf-8')))
    cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and any(getattr(m,'name','')=='generate_image_with_gpt_image_2' for m in n.body))
    names={'generate_image_with_gpt_image_2','teach_llm_when_to_use_tool','_local_image_allowed'}
    methods=[n for n in cls.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    for n in methods:n.decorator_list=[]
    module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),ast.ClassDef(name='Plugin',bases=[],keywords=[],body=methods,decorator_list=[])],type_ignores=[])
    ns={'asyncio':asyncio,'copy':copy,'logger':logging.getLogger(__name__),'TOOL_NAME':'generate_image_with_gpt_image_2','DEFAULT_TOOL_GUIDE':'guide'}
    exec(compile(ast.fix_missing_locations(module),'<adapted-plugin>','exec'),ns)
    return ns['Plugin']


class ImageAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.plugin=plugin_class()()
        self.plugin._str_cfg=lambda group,key,default='': 'test-key' if (group,key)==('api','api_key') else default
        self.plugin._message_allows_image_tool=lambda *a,**kw:True
        self.plugin._tool_turn_key=lambda e:'turn'
        self.plugin._tool_request_key=lambda *a,**kw:'request'
        self.plugin._inflight_tool_turns={};self.plugin._inflight_tool_tasks={}
        self.plugin._build_result_chain=lambda path,prompt:[path]
        self.plugin._friendly_error=str
        self.sent=[]
        async def send(value):self.sent.append(value)
        self.event=types.SimpleNamespace(get_platform_name=lambda:'webchat',get_message_str=lambda:'这是已核验主人的抖音私聊。用户原话：随便聊聊',send=send,chain_result=lambda x:x)

    async def test_natural_dm_returns_image_in_same_turn(self):
        async def generate(**kwargs):return '/mock/image.png',None
        self.plugin._generate_image=generate
        result=await self.plugin.generate_image_with_gpt_image_2(self.event,'一条小鲸鱼')
        self.assertIsNone(result);self.assertEqual(self.sent,[['/mock/image.png']])
        await asyncio.sleep(0)
        self.assertFalse(self.plugin._inflight_tool_tasks)

    async def test_missing_explicit_key_hides_tool_without_mutating_shared_set(self):
        self.plugin._str_cfg=lambda *args:''
        class ToolSet:
            tools=['generate_image_with_gpt_image_2','other']
            def remove_tool(self,name):self.tools=[t for t in self.tools if t!=name]
        shared=ToolSet();req=types.SimpleNamespace(func_tool=shared,system_prompt='persona')
        await self.plugin.teach_llm_when_to_use_tool(self.event,req)
        self.assertEqual(req.func_tool.tools,['other']);self.assertEqual(len(shared.tools),2)
        self.assertEqual(req.system_prompt,'persona')

    async def test_background_and_public_tasks_cannot_generate(self):
        for text in ['公开作品简评任务。','这是主人授权的主动联系决策检查','这是主人授权的主动分享作品任务']:
            self.event.get_message_str=lambda:text
            self.assertFalse(self.plugin._local_image_allowed(self.event))
            self.assertIn('未开放',await self.plugin.generate_image_with_gpt_image_2(self.event,'test'))

    async def test_timeout_cancels_generation_without_late_delivery(self):
        cancelled=asyncio.Event()
        async def generate(**kwargs):
            try:await asyncio.sleep(10)
            finally:cancelled.set()
        self.plugin._generate_image=generate
        original=asyncio.wait_for
        async def brief(task,timeout):return await original(task,.01)
        with patch('asyncio.wait_for',side_effect=brief):
            result=await self.plugin.generate_image_with_gpt_image_2(self.event,'test')
        self.assertIn('不要自动重试',result);self.assertTrue(cancelled.is_set());self.assertFalse(self.sent)

    def test_idempotence_and_unknown_version_fail_closed(self):
        source=SOURCE.read_text(encoding='utf-8');adapted=adapt(source)
        self.assertEqual(adapt(adapted),adapted)
        with self.assertRaises(ValueError):adapt(source.replace('import asyncio\n','import asyncio as other\n'))
