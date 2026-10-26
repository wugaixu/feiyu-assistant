"""Normalize explicit work cards without executing dynamic card schemas."""
import re
from urllib.parse import urlsplit

WORK_ID=re.compile(r'\d{15,25}')

def normalize(content):
    if not isinstance(content,dict):return {}
    if content.get('kind') not in ('unknown','share'):return content
    value=content.get('value')
    if not isinstance(value,dict):return content
    info=value.get('aweme_info')
    if not isinstance(info,dict):return content
    work=str(info.get('item_id') or '')
    if not WORK_ID.fullmatch(work):return content
    outer=str(value.get('item_id') or work)
    if outer!=work:return content
    patch=value.get('im_dynamic_patch') or {}
    if not isinstance(patch,dict):return content
    card_type=patch.get('card_type')
    # Require a recognized card type OR its explicit work detail schema.
    # Never infer work identity from POI IDs, arbitrary links or raw_data scripts.
    schema=value.get('schema')
    detail=False
    if isinstance(schema,str):
        try:parsed=urlsplit(schema)
        except ValueError:return content
        detail=parsed.scheme=='aweme' and parsed.netloc=='aweme' and parsed.path=='/detail/'+work
    if card_type!='life_msg_video_poi' and not detail:return content
    existing=content.get('share') or {}
    if not isinstance(existing,dict):return content
    if existing.get('itemId') and str(existing['itemId'])!=work:return content
    title=value.get('description')
    if not isinstance(title,str):title=''
    author=str(info.get('author_uid') or value.get('author_uid') or '')
    prior=content.get('share_normalization') or {}
    return {**content,'kind':'share','share':{**existing,'itemId':work,'authorUid':author,'title':title},
            'share_normalization':{'source':'structured_aweme_info','card_type':card_type,
                'original_kind':prior.get('original_kind',content.get('kind')) if isinstance(prior,dict) else content.get('kind')}}
