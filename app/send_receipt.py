"""Read bounded status fields from a normal webpage's IM send response.

No requests, credentials, signing or retry logic lives here.
"""
import json


class SendRejected(RuntimeError):
    def __init__(self,receipt):
        self.receipt=receipt
        super().__init__('Platform acceptance not confirmed: '+json.dumps(receipt,ensure_ascii=False))


def fields(data):
    if len(data)>65536:raise ValueError('Receipt too large')
    offset=0;result={}
    def number():
        nonlocal offset
        value=0
        for shift in range(0,70,7):
            if offset>=len(data):raise ValueError('Truncated receipt')
            byte=data[offset];offset+=1;value|=(byte&127)<<shift
            if byte<128:return value
        raise ValueError('Invalid varint')
    while offset<len(data):
        key=number();tag,wire=key>>3,key&7
        if not tag:raise ValueError('Invalid field')
        if wire==0:value=number()
        elif wire==2:
            size=number()
            if size>len(data)-offset:raise ValueError('Truncated field')
            value=data[offset:offset+size];offset+=size
        elif wire in (1,5):
            size=8 if wire==1 else 4
            if size>len(data)-offset:raise ValueError('Truncated field')
            value=data[offset:offset+size];offset+=size
        else:raise ValueError('Unsupported wire type')
        result[tag]=value
    return result


def decode(data):
    outer=fields(data);body=fields(outer.get(6,b''));send=fields(body.get(100,b''))
    text=lambda x:x.decode('utf-8','replace')[:1000] if isinstance(x,bytes) else ''
    result={'envelope_status':outer.get(3,0),'error':text(outer.get(4)),
            'body_present':100 in body,'send_status':send.get(3,0),
            'check_code':send.get(5,0),'check_message':text(send.get(6)),
            'server_message_id':str(send.get(1,0))}
    check=result['check_code']
    try:
        business=json.loads(result['check_message'])
        if isinstance(business,dict) and 'status_code' in business:check=int(business['status_code'])
    except (ValueError,TypeError):pass
    result['business_status']=check
    result['accepted']=bool(result['body_present'] and not result['envelope_status'] and not result['send_status'] and not check and int(result['server_message_id'])>0)
    return result
