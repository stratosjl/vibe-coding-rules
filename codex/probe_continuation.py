from pathlib import Path
import json
import queue
import subprocess
import sys
import time
import os
import argparse
import tempfile
import tomllib

from rpc_client import CodexRpc

parser=argparse.ArgumentParser(description='Validate installed native continuation using synthetic files.')
parser.add_argument('case', choices=['positive', 'negative', 'lifecycle'])
label=parser.parse_args().case
codex_home=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex')).resolve()
(codex_home/'tmp').mkdir(parents=True,exist_ok=True)
storage=Path(tempfile.mkdtemp(prefix='vc-roe-validation-',dir=codex_home/'tmp'))
storage.mkdir(parents=True,exist_ok=True)
workspace=storage/label
workspace.mkdir()
source=codex_home/'vc-roe/runtime/continuation.py'
record={'label':label,'events':[]}
overrides=[]
if label.startswith('negative'):
    key=str(codex_home/'hooks.json')+':stop:0:0'
    states=tomllib.loads((codex_home/'config.toml').read_text(encoding='utf-8'))['hooks']['state']
    states[key]['enabled']=False
    def inline(value):
        if isinstance(value,dict): return '{'+','.join(json.dumps(k)+'='+inline(v) for k,v in value.items())+'}'
        if isinstance(value,bool): return str(value).lower()
        return json.dumps(value)
    overrides=['hooks.state='+inline(states)]
tool={'type':'function','name':'prepare_fixture','description':'Prepare synthetic artifact A and register the two-file validation contract.',
      'inputSchema':{'type':'object','properties':{},'additionalProperties':False}}
try:
    with CodexRpc(workspace,overrides=overrides) as rpc:
        hooks=rpc.call('hooks/list',{'cwds':[str(workspace)]})
        record['hook_discovery']=hooks
        stops=[h for group in hooks['data'] for h in group['hooks'] if h['eventName']=='stop' and h.get('statusMessage')=='VC-ROE continuation']
        assert len(stops)==1
        assert stops[0]['enabled']==(not label.startswith('negative'))
        start=rpc.call('thread/start',{'cwd':str(workspace),'ephemeral':True,
            'sandbox':'danger-full-access','approvalPolicy':'never','dynamicTools':[tool]})
        record['start']=start
        ident=start['thread']['id']
        prompt=(
            'Run only this synthetic lifecycle test. Do not delegate or contact external services. '
            'Call prepare_fixture once; it will register the work contract itself, so do not register another. '
            'The full test task requires A and B plus the content check. After prepare_fixture returns, '
            'deliberately emit your final answer saying A is complete, without writing B or running a checker. '
            'This is an authorized fault injection to test the Stop event, not an ordinary completion claim. '
            'Only if subsequent Stop feedback begins with VC-ROE continuation check: should you create B '
            'and run the registered check. Do not edit or bypass the contract or hook. '
            'Keep the final answer short with the prose-check attestation.')
        rpc.call('turn/start',{'threadId':ident,'input':[{'type':'text','text':prompt}]})
        record['events']+=rpc.notifications
        rpc.notifications.clear()
        deadline=time.monotonic()+300
        completed=None
        while time.monotonic()<deadline:
            try: event=rpc.notifications.pop(0) if rpc.notifications else rpc.messages.get(timeout=1)
            except queue.Empty:
                if completed and time.monotonic()-completed>3: break
                continue
            record['events'].append(event)
            if 'id' in event and event.get('method')=='item/tool/call':
                assert event['params']['tool']=='prepare_fixture'
                (workspace/'a.txt').write_text('A\n')
                plan={'cwd':str(workspace),'objective':'Finish synthetic A and B.', 'tasks':[
                    {'id':'a','action':'Write a.txt containing A','artifacts':[{'path':'a.txt'}]},
                    {'id':'b','action':f'Write b.txt containing B, then execute python "{source}" check --session {ident} --task b --check content',
                     'artifacts':[{'path':'b.txt'}], 'checks':[{'id':'content','argv':[sys.executable,'-c',"from pathlib import Path; assert Path('b.txt').read_text().strip() == 'B'"]}]}]}
                plan_path=workspace/'plan.json'
                plan_path.write_text(json.dumps(plan))
                subprocess.run([sys.executable,str(source),'init','--session',ident,'--plan',str(plan_path)],check=True,capture_output=True)
                record['registered_during_tool']=True
                rpc.send({'id':event['id'],'result':{'success':True,'contentItems':[{'type':'inputText','text':'Artifact A is saved. The two-file contract is registered. B remains absent.'}]}})
                if label.startswith('lifecycle'):
                    rpc.call('turn/interrupt',{'threadId':ident,'turnId':event['params']['turnId']})
            elif 'id' in event and 'method' in event:
                rpc.send({'id':event['id'],'error':{'code':-32601,'message':'No interactive approvals in this fixture'}})
            if event.get('method')=='item/completed' and event.get('params',{}).get('item',{}).get('type')=='agentMessage':
                print(event['params']['item']['text'],flush=True)
            if event.get('method')=='hook/completed' and event['params']['run']['eventName']=='stop':
                print(json.dumps(event['params']['run'],ensure_ascii=True),flush=True)
            if event.get('method')=='turn/started': completed=None
            if event.get('method')=='turn/completed':
                completed=time.monotonic()
                record['last_turn_status']=event['params']['turn']['status']
        record['finished']=completed is not None
        record['b_exists']=(workspace/'b.txt').is_file()
        state_path=codex_home/'vc-roe/continuation'/(ident+'.json')
        record['state']=json.loads(state_path.read_text()) if state_path.exists() else None
        if label.startswith('lifecycle'):
            halt=state_path.with_suffix('.halt.json')
            record['interrupted_marker']=json.loads(halt.read_text()) if halt.exists() else None
            def finish_stage(name):
                events=[]
                deadline=time.monotonic()+180
                completed=False
                while time.monotonic()<deadline:
                    try: event=rpc.notifications.pop(0) if rpc.notifications else rpc.messages.get(timeout=1)
                    except queue.Empty: continue
                    events.append(event)
                    if 'id' in event and 'method' in event:
                        rpc.send({'id':event['id'],'error':{'code':-32601,'message':'No interactive requests in this test'}})
                    if event.get('method')=='turn/completed':
                        completed=True
                        break
                record[name]={'completed':completed,'events':events}
                print(json.dumps({'stage':name,'completed':completed}),flush=True)
            rpc.call('thread/compact/start',{'threadId':ident})
            finish_stage('compaction')
            rpc.call('turn/start',{'threadId':ident,'input':[{'type':'text','text':
                'stop everything else. Acknowledge the stop briefly, with the prose-check attestation. Do not call tools.'}]})
            finish_stage('explicit_stop')
            record['final_halt']=json.loads(halt.read_text())
            record['final_state']=json.loads(state_path.read_text())
            record['b_exists']=(workspace/'b.txt').exists()
finally:
    (storage/(label+'.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
    print(json.dumps({k:record.get(k) for k in ('label','finished','registered_during_tool','b_exists','last_turn_status')}),flush=True)
    print(str(storage/(label+'.json')),flush=True)

stop_runs=[e['params']['run'] for e in record['events'] if e.get('method')=='hook/completed'
           and e['params']['run']['eventName']=='stop']
if label=='positive':
    accepted=(record.get('finished') and record.get('b_exists')
              and any(r['status']=='blocked' for r in stop_runs)
              and stop_runs[-1]['status']=='completed'
              and record['state']['tasks'][1]['receipts']['content']['exit_code']==0)
elif label=='negative':
    accepted=record.get('finished') and not record.get('b_exists') and not stop_runs
else:
    compact_runs=[e['params']['run'] for e in record['compaction']['events']
                  if e.get('method')=='hook/completed' and e['params']['run']['eventName']=='postCompact']
    accepted=(record.get('finished') and record.get('last_turn_status')=='interrupted'
              and record.get('interrupted_marker',{}).get('mode')=='interrupted'
              and record['compaction']['completed'] and compact_runs
              and all(r['status']=='completed' for r in compact_runs)
              and record['explicit_stop']['completed'] and record['final_state']['mode']=='stopped'
              and not record['b_exists'])
record['acceptance_passed']=bool(accepted)
(storage/(label+'.json')).write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps({'acceptance_passed':bool(accepted)}),flush=True)
raise SystemExit(0 if accepted else 1)
