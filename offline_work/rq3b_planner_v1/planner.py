"""One actual model planning call. Never executes a generated plan.

Saved-observation integration only: this is not a live RQ3b process adapter.
The CLI does not expose a verified token cap, so formal registration stays
disabled until that protocol requirement is resolved. Raw attempts persist.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time

ROOT=Path(__file__).resolve().parents[2]
SOURCE=Path(__file__).resolve().parent
CLI=Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
MODEL='gpt-6.1-sol'
MAX_FINAL_BYTES=32768
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p,x): p.write_text(json.dumps(x,indent=2)+'\n')

def permitted_envelope(source, setup):
    # Remove historical applied-configuration annotations and all private
    # sources, candidate/assessor decisions, labels and independent outcomes.
    o=json.loads(source.read_text())[0];s=json.loads(setup.read_text())
    objects=[]
    for field,kind in [('deployments','Deployment'),('services','Service'),('pods','Pod')]:
        for x in o.get(field,[]):
            md=x.get('metadata',{})
            if md.get('name')!='web' and not (kind=='Pod' and md.get('labels',{}).get('service')=='web'):continue
            objects.append({'apiVersion':x['apiVersion'],'kind':kind,
                'metadata':{k:md[k] for k in ('name','namespace','labels') if k in md},
                'spec':x.get('spec',{}),'status':x.get('status',{})})
    t=s['fault_persistence'][0]['target']
    symptoms={k:t[k] for k in ('url','method','status','seconds','curl_rc','body') if k in t}
    return {'schema_version':'rq3b-permitted-input-pilot-v1','source_mode':'archived_development_observation',
        'context':'k3d-lab','namespace':'robot-shop',
        'repair_requirement':'Restore HTTP access through Service web without changing its ports or unrelated services.',
        'allowed_mutation_targets':[{'kind':'Service','name':'web','namespace':'robot-shop'}],
        'current_objects':objects,'observed_http_symptom':symptoms,
        'past_candidate_code_included':False,'future_repair_outcome_included':False}

def validate_output(data, events):
    # Every observed completed item must be the model's final text. Any tool
    # invocation makes this planning-only adapter inadmissible.
    for e in events:
        item=e.get('item',{})
        if e.get('type','').startswith('item.') and item.get('type') not in ('agent_message','reasoning'):
            raise ValueError('Model used an unpermitted tool or item: '+str(item.get('type')))
    if len(data)>MAX_FINAL_BYTES:raise ValueError('Final output exceeds the declared byte admission bound')
    x=json.loads(data)
    if set(x)!= {'status','plan_yaml','rationale'} or x['status'] not in ('candidate','unresolved'):
        raise ValueError('Unexpected planner schema')
    if not all(isinstance(x[k],str) for k in x):raise ValueError('All planner fields must be strings')
    if not x['rationale'].strip():raise ValueError('Missing planning rationale')
    if (x['status']=='candidate') != bool(x['plan_yaml'].strip()):raise ValueError('Candidate/unresolved plan mismatch')
    if not any(e.get('type')=='turn.completed' for e in events):raise ValueError('No completed model turn')
    return x

def run(output, envelope, *, timeout=180, cli=CLI):
    if isinstance(timeout,bool) or not isinstance(timeout,(float,int)) or not math.isfinite(timeout) or timeout<=0:
        raise ValueError('Positive finite planning deadline required')
    output.mkdir(parents=True,exist_ok=False);(output/'empty-workspace').mkdir()
    save(output/'permitted-input.json',envelope)
    prompt=('Prepare one repair plan from this permitted current observation. '
            'This is a planning integration test; do not execute any action.\n'+json.dumps(envelope,sort_keys=True))
    (output/'prompt.txt').write_text(prompt)
    argv=[str(cli),'exec','--ignore-user-config','--ignore-rules','--ephemeral',
        '--skip-git-repo-check','--sandbox','read-only','--cd',str((output/'empty-workspace').resolve()),
        '--model',MODEL,'--color','never','--json',
        '-c','model_reasoning_effort="high"','-c','web_search="disabled"',
        '-c','features.shell_tool=false','-c','features.multi_agent=false','-c','features.apps=false',
        '-c','project_doc_max_bytes=0',
        '-c','model_instructions_file='+json.dumps(str(SOURCE/'instructions.txt')),
        '--output-schema',str(SOURCE/'output-schema.json'),'-o',str((output/'output.json').resolve()),'-']
    receipt={'kind':'rq3b_saved_observation_model_integration','formal_trials':0,'live_recovery_trials':0,
        'model':MODEL,'model_alias_snapshot_guaranteed':False,'reasoning_effort':'high','backend':'Codex CLI with existing ChatGPT login',
        'cli_version':subprocess.check_output([str(cli),'--version'],text=True).strip(),
        'planner_timeout_seconds':timeout,'admission_max_final_bytes':MAX_FINAL_BYTES,
        'maximum_output_tokens':None,'token_cap_requirement_resolved':False,
        'tool_call_allowance':0,'outer_retry_limit':0,'cli_internal_retries_verified':False,
        'argv':argv,'prompt_sha256':sha(output/'prompt.txt'),
        'instruction_sha256':sha(SOURCE/'instructions.txt'),'output_schema_sha256':sha(SOURCE/'output-schema.json'),
        'source_sha256':sha(Path(__file__)),'accepted_planning_call':False,'generated_plan_executed':False}
    save(output/'receipt.json',receipt)
    start=time.monotonic();receipt['started_at']=time.time()
    with (output/'events.jsonl').open('w') as stdout,(output/'stderr.log').open('w') as stderr:
        p=subprocess.Popen(argv,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,text=True,start_new_session=True)
        try:
            p.communicate(input=prompt,timeout=timeout);receipt['returncode']=p.returncode
        except subprocess.TimeoutExpired:
            receipt['timeout_reached']=True
            os.killpg(p.pid,signal.SIGTERM)
            try:p.communicate(timeout=5)
            except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.communicate()
            receipt['returncode']=p.returncode
    receipt['elapsed_seconds']=time.monotonic()-start;receipt['completed_at']=time.time()
    try:
        if receipt.get('timeout_reached') or receipt['returncode']!=0:raise ValueError('Actual model call did not complete normally')
        events=[json.loads(line) for line in (output/'events.jsonl').read_text().splitlines() if line.strip()]
        result=validate_output((output/'output.json').read_bytes(),events)
        receipt['accepted_planning_call']=True;receipt['generated_status']=result['status']
        receipt['output_sha256']=sha(output/'output.json')
        receipt['usage']=[e.get('usage') for e in events if e.get('type')=='turn.completed']
        (output/'generated-plan.yaml').write_text(result['plan_yaml'])
    except Exception as error:receipt['rejection_reason']=str(error)
    receipt['evidence_sha256']={p.name:sha(p) for p in output.iterdir() if p.is_file() and p.name!='receipt.json'}
    save(output/'receipt.json',receipt);return receipt

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);args=parser.parse_args()
    source=ROOT/'results/reuse-flow-development-v8/07-revalidate-P5/primary/observations.json'
    setup=source.with_name('incident-setup.json')
    output=(ROOT/args.output).resolve()
    if not output.is_relative_to(ROOT/'results/RQ3b-planner-pilot-v1'):raise ValueError('Pilot output only')
    envelope=permitted_envelope(source,setup)
    receipt=run(output,envelope)
    save(output/'source-identities.json',{str(p.relative_to(ROOT)):sha(p) for p in (source,setup)})
    print(json.dumps({k:receipt.get(k) for k in ('accepted_planning_call','elapsed_seconds','generated_status','rejection_reason','formal_trials','token_cap_requirement_resolved')}))

if __name__=='__main__':main()
