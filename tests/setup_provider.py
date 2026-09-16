"""Synthetic Lark process for packaged setup tests; never contacts Lark."""
import json
import os
from pathlib import Path
import sys

path = Path(os.environ['P2L_SETUP_STATE'])
state = json.loads(path.read_text(encoding='utf-8'))
args = sys.argv[1:]
op = args[1]
state.setdefault('calls', []).append(args)
def arg(name, default=None):
    return args[args.index(name) + 1] if name in args else default
def save():
    path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
def output(data):
    save()
    if state.get('lose') == op:
        state.pop('lose'); save(); print('lost response'); raise SystemExit(1)
    print(json.dumps({'ok':True, 'identity':'user', 'data':data}, ensure_ascii=False))
    raise SystemExit
if args[:2] == ['auth','status']:
    save()
    print(json.dumps({'appId':'setupApp','brand':'lark','verified':True,'identities':{'user':{'status':'ready','available':True,'openId':'setupUser','tokenStatus':'valid','verified':True}}}))
    raise SystemExit
if op == '+space-create':
    state['space'] = {'space_id':'123','name':arg('--name'),'description':arg('--description','')}
    output(state['space'])
if op == '+space-list': output({'spaces':[state['space']],'has_more':False,'page_token':''})
if op in ('+node-create','+create'):
    nodes = state.setdefault('nodes',{})
    token = 'node' + str(len(nodes)+1)
    obj = ('base' if arg('--obj-type') == 'bitable' else 'doc') + str(len(nodes)+1)
    parent = arg('--parent-node-token',arg('--parent-token',''))
    node = {'space_id':'123','node_token':token,'obj_token':obj,'obj_type':arg('--obj-type','docx'),'title':arg('--title'),'parent_node_token':parent,'node_type':'origin'}
    nodes[token] = node
    if op == '+create':
        state.setdefault('documents',{})[obj] = {'document_id':obj,'revision_id':1,'content':Path('publication.md').read_text(encoding='utf-8')}
        output({'document':{'document_id':obj,'revision_id':1,'url':'https://example.test/docx/'+obj}})
    output({**node,'resolved_space_id':'123'})
if op in ('+node-get','+inspect'):
    token = arg('--node-token',arg('--url','')).rsplit('/',1)[-1]
    node = next(n for n in state['nodes'].values() if token in (n['node_token'],n['obj_token']))
    output(node if op == '+node-get' else {'type':node['obj_type'],'token':node['obj_token']})
if op == '+table-create':
    state['table'] = {'id':'tbl1','name':arg('--name')}
    state['fields'] = [dict(field,id='fld'+str(i)) for i,field in enumerate(json.loads(arg('--fields')))]
    output({'table':state['table']})
if op == '+table-get': output({'table':state['table']})
if op == '+field-list': output({'fields':state['fields'],'total':len(state['fields'])})
if op == '+field-get': output({'field':next(f for f in state['fields'] if f['id']==arg('--field-id'))})
if op == '+field-create':
    field = dict(json.loads(arg('--json')),id='fldAdded'+str(len(state['fields'])))
    state['fields'].append(field)
    output({'field':field})
if op == '+fetch': output({'document':state['documents'][arg('--doc')]})
raise RuntimeError('Unsupported synthetic operation')
