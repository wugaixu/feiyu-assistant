"""Local stdio MCP or one-shot UTF-8 JSON tool calls; no extra dependencies."""
import argparse
import json
import sqlite3
import sys
from app import douyin_toolkit as kit


def handle(db, request):
    if not isinstance(request, dict) or request.get('jsonrpc') != '2.0' or not isinstance(request.get('method'), str):
        return {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32600, 'message': 'Invalid request'}}
    if 'id' not in request:
        return None
    key = request['id']
    method = request['method']
    if method == 'initialize':
        value = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                 'serverInfo': {'name': 'local-douyin-tools', 'version': '0.1.0'}}
    elif method == 'ping':
        value = {}
    elif method == 'tools/list':
        value = {'tools': kit.TOOLS}
    elif method == 'tools/call':
        try:
            params = request.get('params', {})
            if not isinstance(params, dict):
                raise ValueError('Expected tool call parameters object')
            output = kit.call(db, params.get('name'), params.get('arguments', {}))
            value = {'content': [{'type': 'text', 'text': json.dumps(output, ensure_ascii=False)}], 'isError': False}
        except (ValueError, TypeError, sqlite3.Error) as exc:
            value = {'content': [{'type': 'text', 'text': str(exc)}], 'isError': True}
    else:
        return {'jsonrpc': '2.0', 'id': key, 'error': {'code': -32601, 'message': 'Method not found'}}
    return {'jsonrpc': '2.0', 'id': key, 'result': value}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request-file', help='JSON {name,arguments}; omit for stdio MCP')
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding='utf-8')
    with sqlite3.connect(kit.ROOT / 'assistant.sqlite3', timeout=10) as db:
        if args.request_file:
            with open(args.request_file, encoding='utf-8-sig') as source:
                value = json.load(source)
            print(json.dumps(kit.call(db, value['name'], value.get('arguments', {})), ensure_ascii=False))
            return
        for line in sys.stdin:
            try:
                response = handle(db, json.loads(line))
            except ValueError:
                response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
