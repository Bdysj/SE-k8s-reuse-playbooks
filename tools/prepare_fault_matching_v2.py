"""Predeclare permitted incident extraction before observing new pilot repairs."""
import hashlib
import json
import time
from pathlib import Path

import incident_observations as incidents

ROOT=Path(__file__).resolve().parent.parent
WORKLOADS={'P1':'web','P2':'ratings','P3':'web','P4':'web','P5':'web'}


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare():
    out=ROOT/'results/fault-matching-development-v2'
    out.mkdir(exist_ok=False)
    acceptance=ROOT/'results/S4-registration-v3/acceptance.json'
    accepted=json.loads(acceptance.read_text())
    assert accepted['passed'] and accepted['successful_qualification_trials']==25
    references={key:[] for key in WORKLOADS}
    provenance=[]
    for ref in accepted['records']:
        path=ROOT/ref['record']
        assert sha(path)==ref['record_sha256']
        r=json.loads(path.read_text());key=ref['candidate']
        assert r['candidate']==key and r['execution_complete'] and r['target_recovery']
        value=incidents.before_action(r,WORKLOADS[key])
        references[key].append(value['incident'])
        provenance.append({'candidate':key,'record':ref['record'],'record_sha256':sha(path),**value})
    sources=['incident_observations.py','comparison_policies.py','prepare_fault_matching_v2.py',
             'test_incident_observations.py','test_comparison_policies.py']
    snapshot=out/'tool-snapshot';snapshot.mkdir()
    for name in sources:(snapshot/name).write_bytes((ROOT/'tools'/name).read_bytes())
    record={'stage':'fault-matching development; not final model or method freeze','at':time.time(),
        'S4_acceptance':str(acceptance.relative_to(ROOT)),'S4_acceptance_sha256':sha(acceptance),
        'registered_incidents':references,'source_records':provenance,
        'pilot_protocol':'results/pre-main-development-v4/protocol.json',
        'pilot_protocol_sha256':sha(ROOT/'results/pre-main-development-v4/protocol.json'),
        'planned_pilots':['DEV-P2-minimum1','DEV-P2-minimum4'],
        'planned_arms':['repair','repair'],'main_trial_labels_forbidden':True,'main_trials':0,
        'source_hashes':{name:sha(ROOT/'tools'/name) for name in sources},
        'feature_selection':'Fixed first actual target observation strictly before action. Only '
            'ordinary workload names, HTTP/rollout symptoms and error codes; no construction, '
            'injection, expected effect, verifier minimum or future outcome in the policy text.',
        'label_policy':'Only complete observed development repair records produce positive/negative '
            'labels. Preserve Indeterminate and harness rejections. S4 is reference history, '
            'not negative-labelled training data.',
        'threshold_policy':'TF-IDF vocabulary fitted to registered ordinary incidents and development '
            'pilot ordinary incidents only. Choose observed balanced accuracy maximum, higher '
            'threshold on ties, with explicit reject-all boundary above one.',
        'scope_limit':'Two P2 pilots verify the fitting pipeline. They do not provide comprehensive '
            'five-family development coverage or establish a final threshold/comparative conclusion.'}
    (out/'protocol.json').write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'registered_reference_incidents':len(provenance),'planned_pilots':record['planned_pilots'],
                      'model_fitted':False,'main_trials':0}))


if __name__=='__main__':prepare()
