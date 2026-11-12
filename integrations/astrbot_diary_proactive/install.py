"""Apply the small, checked diary integration patch; never reload/restart here."""
import ast,shutil,sys
from pathlib import Path
PROJECT=Path(__file__).resolve().parents[2]
DATA=Path(r'__RUNTIME_ROOT__\astrbot\runtime\data')

def context_patch(main):
    main=main.replace('        return Session.from_event(event)','        return workflow_bridge.session_for_event(event, Session)',1)
    main=main.replace('text=str(getattr(event, "message_str", "") or "")','text=workflow_bridge.owner_text(event)')
    main=main.replace('strong_text=str(getattr(event, "message_str", "") or "")','strong_text=workflow_bridge.owner_text(event)')
    main=main.replace('        message = str(getattr(event, "message_str", "") or "")',
                      '        message = workflow_bridge.owner_text(event)')
    main=main.replace('        if query_text is not None:\n            message = str(query_text)',
                      '        if query_text is not None and not workflow_bridge.has_workflow_event(event):\n            message = str(query_text)')
    return main


def patched(main,api):
    if '    async def _patrol_runtime(' in main:
        return patched_runtime(main,api)
    if '# FEIYU_DIARY_PROACTIVE' in main:
        if 'workflow_bridge.mark_loaded(self)' not in main:
            needle='            logger.info("[%s] 主动消息巡检已重建：每 %d 分钟一次", PLUGIN_NAME, minutes)'
            main=main.replace(needle,needle+'\n            workflow_bridge.mark_loaded(self)',1)
        main=context_patch(main);ast.parse(main)
        return main,api
    main=main.replace('from .core.proactive import Proactive','from .core.proactive import Proactive\nfrom . import workflow_bridge  # FEIYU_DIARY_PROACTIVE',1)
    main=main.replace('getattr(job, "name", "") == PROACTIVE_JOB_NAME','getattr(job, "name", "") in (PROACTIVE_JOB_NAME, ACTIVE_JOB_NAME)',1)
    needle='            logger.info("[%s] 主动消息巡检已重建：每 %d 分钟一次", PLUGIN_NAME, minutes)'
    main=main.replace(needle,needle+'\n            workflow_bridge.mark_loaded(self)',1)
    start=main.index('        if not self.settings.subsystem("proactive"):',main.index('    async def _proactive_patrol('))
    end=main.index('\n    async def ',start)
    main=main[:start]+'        await workflow_bridge.patrol(self)\n'+main[end:]
    needle='        session = self._session(event)'
    pos=main.index(needle,main.index('    async def inject_diary_hint('))
    main=main[:pos]+'        if workflow_bridge.prepare_request(event, req):\n            return\n'+main[pos:]
    api=api.replace('async def patrol(self, *, now: datetime | None = None)', 'async def patrol(self, *, now: datetime | None = None, allowed_umos: set[str] | None = None)',1)
    api=api.replace('self._signal_candidate(doc, cfg, moment)','self._signal_candidate(doc, cfg, moment, allowed_umos)',1)
    needle='        allowed = self._allowed_sessions(doc, cfg, moment, mode=self._scope_mode())'
    api=api.replace(needle,needle+'\n        if allowed_umos is not None:\n            allowed = {umo: kind for umo, kind in allowed.items() if umo in allowed_umos}',1)
    api=api.replace('self, doc: dict[str, Any], cfg: dict[str, Any], moment: datetime\n    ) -> triggers.Candidate | None:',
                    'self, doc: dict[str, Any], cfg: dict[str, Any], moment: datetime, allowed_umos: set[str] | None = None\n    ) -> triggers.Candidate | None:',1)
    api=api.replace('            if not umo:\n                continue\n            entry = gate.entry_of(doc, umo)',
                    '            if not umo or (allowed_umos is not None and umo not in allowed_umos):\n                continue\n            entry = gate.entry_of(doc, umo)',1)
    assert 'allowed_umos: set[str]' in api and 'await workflow_bridge.patrol(self)' in main
    main=context_patch(main)
    ast.parse(main);ast.parse(api)
    return main,api


def patched_runtime(main,api):
    """1.12.x per-runtime patrol and integration-service request preparation."""
    marker='from . import workflow_bridge  # FEIYU_DIARY_PROACTIVE'
    if marker not in main:
        needle='from .core.proactive import Proactive'
        if main.count(needle)!=1:raise ValueError('Unsupported diary import structure')
        main=main.replace(needle,needle+'\n'+marker,1)
    start=main.index('    async def _patrol_runtime(')
    end=main.index('\n    async def _wake(',start)
    main=main[:start]+'''    async def _patrol_runtime(self, runtime) -> None:
        """Preserve native runtime scope and timing; workflow owns delivery."""
        await workflow_bridge.patrol_runtime(self, runtime)
'''+main[end:]
    needle='            logger.info("[%s] 主动消息巡检已重建：每 %d 分钟一次", PLUGIN_NAME, minutes)'
    if 'workflow_bridge.mark_loaded(self)' not in main:
        if main.count(needle)!=1:raise ValueError('Unsupported diary scheduler structure')
        main=main.replace(needle,needle+'\n            workflow_bridge.mark_loaded(self)',1)
    main=main.replace('getattr(job, "name", "") == PROACTIVE_JOB_NAME',
                      'getattr(job, "name", "") in (PROACTIVE_JOB_NAME, ACTIVE_JOB_NAME)',1)
    needle='        try:\n            await self.integration_service_v1.inject_request(event, req)'
    if 'if workflow_bridge.prepare_request(event, req):' not in main:
        if main.count(needle)!=1:raise ValueError('Unsupported diary request preparation')
        main=main.replace(needle,'        if workflow_bridge.prepare_request(event, req):\n            return\n'+needle,1)
    main=context_patch(main)
    # Reuse the legacy kernel-only patch, without replacing the new host lifecycle.
    if 'allowed_umos: set[str]' not in api:
        # Apply exactly the same checked core edits as older supported releases.
        api=api.replace('async def patrol(self, *, now: datetime | None = None)', 'async def patrol(self, *, now: datetime | None = None, allowed_umos: set[str] | None = None)',1)
        api=api.replace('self._signal_candidate(doc, cfg, moment)','self._signal_candidate(doc, cfg, moment, allowed_umos)',1)
        needle='        allowed = self._allowed_sessions(doc, cfg, moment, mode=self._scope_mode())'
        if api.count(needle)!=1:raise ValueError('Unsupported diary target admission')
        api=api.replace(needle,needle+'\n        if allowed_umos is not None:\n            allowed = {umo: kind for umo, kind in allowed.items() if umo in allowed_umos}',1)
        api=api.replace('self, doc: dict[str, Any], cfg: dict[str, Any], moment: datetime\n    ) -> triggers.Candidate | None:',
                        'self, doc: dict[str, Any], cfg: dict[str, Any], moment: datetime, allowed_umos: set[str] | None = None\n    ) -> triggers.Candidate | None:',1)
        needle='            if not umo:\n                continue\n            entry = gate.entry_of(doc, umo)'
        if api.count(needle)!=1:raise ValueError('Unsupported diary signal admission')
        api=api.replace(needle,'            if not umo or (allowed_umos is not None and umo not in allowed_umos):\n                continue\n            entry = gate.entry_of(doc, umo)',1)
    ast.parse(main);ast.parse(api)
    assert 'allowed_umos: set[str]' in api and 'await workflow_bridge.patrol_runtime(self, runtime)' in main
    return main,api

def install(folder):
    main=folder/'main.py';api=folder/'core/proactive/api.py'
    m,a=patched(main.read_text(encoding='utf-8'),api.read_text(encoding='utf-8'))
    main.write_text(m,encoding='utf-8');api.write_text(a,encoding='utf-8')
    shutil.copyfile(Path(__file__).with_name('bridge.py'),folder/'workflow_bridge.py')

if __name__=='__main__':install(DATA/'plugins/astrbot_plugin_denia_diary')
