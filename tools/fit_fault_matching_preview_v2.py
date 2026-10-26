"""Fit a development preview only to audited, actually observed pilot outcomes."""
import hashlib
import json
import time
from pathlib import Path

import audit_main_development as audit
import comparison_policies as policies
import incident_observations as incidents

ROOT=Path(__file__).resolve().parent.parent


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    out=ROOT/'results/fault-matching-development-v2'
    protocol=out/'protocol.json';plan=json.loads(protocol.read_text())
    for name,h in plan['source_hashes'].items():
        if sha(ROOT/'tools'/name)!=h:raise RuntimeError('Prospective incident/model source changed: '+name)
    source_protocol=ROOT/plan['pilot_protocol']
    if sha(source_protocol)!=plan['pilot_protocol_sha256']:raise RuntimeError('Pilot plan changed')
    pilot_out=source_protocol.parent
    training=[];traces=[]
    for identifier in plan['planned_pilots']:
        path=pilot_out/(identifier+'-repair-attempt-1')/'record.json'
        accepted=audit.audit(path)
        if not accepted['accepted']:raise RuntimeError('Do not label an incomplete pilot: '+str(accepted['failures']))
        r=json.loads(path.read_text())
        if r['scenario']!=identifier or r['arm']!='repair' or r['main_trials']!=0:
            raise RuntimeError('Outside the prospectively declared development pilots')
        observed=incidents.before_action(r,r['construction']['target_deployment'])
        training.append({'candidate':r['candidate'],'incident':observed['incident'],
            'outcome':r['repair_outcome'],'label_source':'observed_development_replay','main_trial':False})
        traces.append({**accepted,**observed,'label':r['repair_outcome']})
    model=policies.fit_fault_matching(plan['registered_incidents'],training)
    record={'stage':'development model preview; main threshold not frozen','at':time.time(),'main_trials':0,
        'protocol_sha256':sha(protocol),'training':training,'audited_sources':traces,'model':model,
        'fitting_source_sha256':sha(Path(__file__)),
        'scope_limit':plan['scope_limit'],
        'decisions':[{'candidate':p['candidate'],'observed_label':p['outcome'],
                      **policies.fault_matching(model,p['candidate'],p['incident'])} for p in training]}
    with (out/'preview-model.json').open('x') as f:json.dump(record,f,indent=2);f.write('\n')
    print(json.dumps({'development_preview':True,'positive':model['pilot_positive_count'],
                      'negative':model['pilot_negative_count'],'balanced_accuracy':model['pilot_balanced_accuracy'],
                      'threshold':model['threshold'],'main_threshold_frozen':False}))


if __name__=='__main__':main()
