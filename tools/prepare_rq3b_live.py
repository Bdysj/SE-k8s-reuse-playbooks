"""Freeze bounded pilot/formal inputs prospectively. Does not execute incidents."""
import hashlib
import json
from pathlib import Path
import sys
import time
import shutil
ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'results/formal-rq3b-short-v1'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path,x):path.write_text(json.dumps(x,indent=2)+'\n')

def main():
    OUT.mkdir(exist_ok=False)
    order=[]
    for template,processes in [
        ('unchanged',['proposed','replanning','full_revalidation']),
        ('preserving',['replanning','full_revalidation','proposed']),
        ('invalidating',['full_revalidation','proposed','replanning'])]:
        for process in processes:
            order.append({'position':len(order)+1,'name':f'{len(order)+1:02d}-{template}-{process}',
                          'template':template,'process':process})
    protocol={'version':OUT.name,'written_at':time.time(),'scope_frozen':True,
        'formal_collection_requires_live_pilot_acceptance':True,
        'candidate':'P5','candidate_sha256':sha(ROOT/'candidates/P5.yaml'),
        'scope':'Bounded Service-routing case study. No coverage/generalisation claim for all five playbooks or long recurrence.',
        'registered_event_slots':45,'sequences':9,'incidents_per_sequence':5,'order':order,
        'selection':'Unchanged P5 control, preserving P5-2-1 track label, invalidating P5-2-2 label mapping. Chosen for the three required case classes, not recovery-time outcomes.',
        'change_schedule':'Incident1 unchanged baseline. Incident2 applies the template change once. Incidents3-5 retain it and all prior repairs. Same schedule for all processes.',
        'evidence_age_independent_factor':False,'current_observation_ttl_seconds':30,
        'independent_start_snapshot':'Fixed seed and same manifest; business cart rq3b-preserved-cart has Watson quantity2. Other seeded workload transaction histories are not bitwise identical.',
        'within_sequence_reset_allowed':False,'between_sequence_reset_required':True,
        'primary_baseline':'results/reuse-flow-development-v8/07-revalidate-P5/primary/baseline.yaml',
        'replica_baseline':'results/reuse-flow-development-v8/07-revalidate-P5/replica/baseline.yaml',
        'replica_fidelity_scope':'Exact application specifications, current frontend Deployment/Service relationship, declared workload and same selector fault; independent fixed MySQL/MongoDB seeds and copied retained cart. Unrelated transaction histories are not replicated and are a stated limitation.',
        'replica_cleanup':'Confirm no active runner and retain isolated lease. Each subsequent revalidation begins with a charged full replica restore. Cleanup of primary only between sequences.',
        'model':{'alias':'gpt-6-sol','immutable_snapshot_verified':False,'identity_independently_attested':False,
            'reasoning_effort':'high','requested_max_output_tokens':2048,'maximum_final_bytes':32768,
            'token_budget_owner':'user backend control','client_token_boundary_gate_waived_by_user':True,
            'backend_token_value_independently_verified':False,'call_wall_deadline_seconds':180,
            'tool_calls':0,'client_retries':0,'gateway_internal_retries_verified':False,
            'input':'Fresh current objects and observed HTTP symptom. No historical candidate, expected outcome, other process history or injected-fault label.',
            'instructions':'offline_work/rq3b_planner_v1/instructions.txt',
            'schema':'offline_work/rq3b_planner_v1/output-schema.json'},
        'response_cap_seconds':1200,'generated_attempts':1,'isolated_replays':1,
        'safety_gate':'Identical namespace/context allowlist and selector-only Ansible command argv scope for historical and generated plans; syntax, fresh matching revision, current named non-target checks and fixed deadlines.',
        'verification':{'fault_persistence_seconds':60,'post_action_window_seconds':210,
            'sustained_recovery_seconds':30,'repair_budget_seconds':120,'sample_interval_seconds':5,
            'target':'HTTP status/content, current workload rollout and owner-bound EndpointSlice.',
            'non_target':'Existing named checks on all declared other workloads and Service paths.',
            'business_state':'Retained marker cart remains identical after successful repair and before the next incident.'},
        'unresolved_policy':'Keep observed incident as censored at1200s. Stop its sequence. Archive remaining incident slots as unpresented; do not invent response times. Subsequent matched sequences require a confirmed baseline reset.',
        'timing':'Fault presentation to final verifier confirmation, includes collection, planning, assessment, replica restoration, synchronisation, action and complete verification. Excludes predefined healthy change intervals and between-sequence primary resets.',
        'pilot_plan':'Five physical incidents: proposed unchanged then invalidating; replanning unchanged; full revalidation unchanged then invalidating. Separate pilot outputs, never pooled into formal data.',
        'analysis':'Report matched sequence totals, per-incident routes, determinate invalid actions, isolated failures and unresolved/unpresented counts. One sequence per template/process is descriptive; no population CI, statistical superiority or rare-failure equivalence claim.'}
    save(OUT/'protocol.json',protocol)
    shutil.copyfile(ROOT/'D-DN-3.1_EN.docx',OUT/'before.docx')
    protected=json.loads((ROOT/'results/RQ3b-code-refactor-v1/protected-inputs.json').read_text())['sha256']
    save(OUT/'protected-before.json',protected)
    save(OUT/'progress.json',{'state':'preparing','at':time.time(),'position':0,'target':45,'responses_completed':0,'formal_sequences_completed':0,'pilot_sequences_completed':0})
    print(str(OUT))


def freeze():
    sources={}
    for p in (ROOT/'tools').glob('*.py'):sources[str(p.relative_to(ROOT))]=sha(p)
    for folder in ['offline_work/rq3b_live_v1','offline_work/rq3b_api_v1','offline_work/rq3b_planner_v1']:
        for p in (ROOT/folder).iterdir():
            if p.is_file():sources[str(p.relative_to(ROOT))]=sha(p)
    for p in [ROOT/'candidates/P5.yaml',ROOT/'tools/ansible-inventory.ini',ROOT/'candidates/requirements.S3-feasible.v1.yaml']:
        sources[str(p.relative_to(ROOT))]=sha(p)
    for role in ['primary','replica']:
        for p in (ROOT/f'results/reuse-flow-development-v8/07-revalidate-P5/{role}').glob('baseline*'):
            sources[str(p.relative_to(ROOT))]=sha(p)
    save(OUT/'freeze.json',{'at':time.time(),'protocol_sha256':sha(OUT/'protocol.json'),
        'source_document_sha256':sha(ROOT/'D-DN-3.1_EN.docx'),'sources':sources,
        'python':'3.12','ansible_core':'2.19.3','ansible_runner':'2.4.2','pyyaml':'6.0.3'})
    snapshot=OUT/'source-snapshot';snapshot.mkdir()
    for name in sources:
        path=snapshot/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,path)
    print('Frozen',len(sources),'source identities')

if __name__=='__main__':
    if '--output' in sys.argv:
        OUT=(ROOT/sys.argv[sys.argv.index('--output')+1]).resolve()
        if not OUT.is_relative_to(ROOT/'results'):raise ValueError('Outputs must stay under results')
    if '--freeze' in sys.argv:freeze()
    else:main()
