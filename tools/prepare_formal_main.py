"""Register prospective 40/200 order only after supplementary qualification."""
import argparse
import collections
import copy
import json
import random
import time
from pathlib import Path
import yaml
import formal_collection_v2 as collection
import formal_methods as methods
import incident_observations as incidents
import comparison_policies as policies
import reuse_assessor as assessor
import audit_main_development as development_audit

ROOT=collection.ROOT

def calibration_plan(out, calibration):
    # This source selection is written before fitting or formal outcome access.
    p=collection.check(calibration)
    negative=ROOT/'results/reuse-flow-development-v8/03-failed-validation-P3/replica/outcome-record.json'
    case=negative.parent.parent/'acceptance.json'
    sources=[{'candidate':x['candidate'],'record':str((calibration/'trials'/('calibration-'+x['candidate'])/'record.json').relative_to(ROOT)),
              'audit':str((calibration/'trials'/('calibration-'+x['candidate'])/'audit.json').relative_to(ROOT))}
             for x in p['controls']]
    sources.append({'candidate':'P3','record':str(negative.relative_to(ROOT)),
                    'case_acceptance':str(case.relative_to(ROOT))})
    sources.append({'candidate':'P2',
        'record':'results/pre-main-development-v4/DEV-P2-minimum4-repair-attempt-1/record.json',
        'independent_development_audit':True,
        'profile_note':'Earlier qualification profile before the DNS correction; permitted incident features and frozen P2 action are unchanged. This is development training only.'})
    plan={'at':time.time(),'stage':'development fitting source selection; not formal outcomes',
          'sources':sources,'positive_and_negative_must_be_observed':True,
          'reference_protocol':'results/fault-matching-development-v2/protocol.json',
          'reference_protocol_sha256':collection.sha(ROOT/'results/fault-matching-development-v2/protocol.json'),
          'scope':'Five repair families have observed positive development outcomes; negative pilot coverage is P2 and P3 only. P2 negative predates the DNS correction and remains attributed to that profile. No population accuracy or held-out calibration claim.',
          'main_trial_labels_forbidden':True}
    collection.save(out/'calibration-selection.json',plan)
    return plan

def fit(out,calibration):
    accepted=calibration/'collection-acceptance.json'
    if not accepted.exists() or not json.loads(accepted.read_text())['accepted']:
        raise RuntimeError('Five supplementary reference trials must finish first')
    plan=calibration_plan(out,calibration);training=[];refs={};sources=[]
    cp=collection.check(calibration)
    for source in plan['sources']:
        path=ROOT/source['record'];r=json.loads(path.read_text())
        if source.get('audit'):
            audit=json.loads((ROOT/source['audit']).read_text())
            assert audit['accepted'] and collection.sha(path)==audit['raw_record_sha256']
        elif source.get('independent_development_audit'):
            assert development_audit.audit(path)['accepted']
        else:
            assert json.loads((ROOT/source['case_acceptance']).read_text())['passed']
            assert r['observation_complete'] and r['action_executed']
        assert r['repair_outcome'] in ('Successful repair','Confirmed failure')
        assert not r.get('main_trial') and not r.get('main_trials')
        extracted=incidents.before_action(r,r['construction']['target_deployment'])
        training.append({'candidate':r['candidate'],'incident':extracted['incident'],
                         'outcome':r['repair_outcome'],'label_source':'observed_development_replay','main_trial':False})
        sources.append({**source,'record_sha256':collection.sha(path),**extracted})
        if source.get('audit'):
            assert r['repair_outcome']=='Successful repair'
            row=next(x for x in cp['controls'] if x['candidate']==r['candidate'])
            refs[r['candidate']]={'snapshot':methods.scope_snapshot(r['pre_action_policy_observation']),
                'criteria':methods.criteria(row,cp),'source':source,'record_sha256':collection.sha(path),
                'history_policy':'Supplementary successful registration under the common DNS profile; original 25 qualification receipts remain immutable. This is an observed incident snapshot, not reconstructed S4 data.'}
    reference=json.loads((ROOT/plan['reference_protocol']).read_text())
    model=policies.fit_fault_matching(reference['registered_incidents'],training)
    collection.save(out/'fault-matching-model.json',{'at':time.time(),'model':model,
                    'sources':sources,'training':training,'source_selection_sha256':collection.sha(out/'calibration-selection.json'),
                    'main_labels_used':False,'scope_limit':plan['scope']})
    collection.save(out/'whole-environment-references.json',refs)
    return model

def make_order(scenarios,controls,seed):
    randomizer=random.Random(seed);order=[]
    for repeat in range(1,6):
        block=[]
        for row in scenarios:
            block.append({'scenario':row['id'],'repeat':repeat,'arm':'repair','category':'main'})
            if repeat==1:block.append({'scenario':row['id'],'repeat':repeat,'arm':'no-repair','category':'variant-no-repair-control'})
        for row in controls:
            category='unrelated-change-control' if row.get('unrelated_control') else 'original-conditions-control'
            block.append({'scenario':row['id'],'repeat':repeat,'arm':'repair','category':category})
            if repeat==1 and not row.get('unrelated_control'):
                block.append({'scenario':row['id'],'repeat':repeat,'arm':'no-repair','category':'original-no-repair-control'})
        randomizer.shuffle(block)
        for entry in block:
            entry['trial_id']=f"{len(order)+1:03d}-{entry['scenario']}-{entry['arm']}-r{repeat}"
            order.append(entry)
    return order

def prepare(out,calibration):
    p=collection.base_protocol(out);model=fit(out,calibration)
    controls=p['controls']
    for row in list(controls):
        row=copy.deepcopy(row);row['id']=row['candidate']+'-unrelated-change'
        row['condition']='unrelated-change-control';row['unrelated_control']='ddn-unrelated-control'
        docs=list(yaml.safe_load_all((ROOT/row['healthy_manifest']).read_text()))
        assert not any('ddn-unrelated-control' in json.dumps(d) for d in docs)
        docs.append({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':row['unrelated_control']},
                     'data':{'UNUSED_NOTE':'unreferenced-control-v1'}})
        path=out/(row['id']+'.healthy.yaml');path.write_text(yaml.safe_dump_all(docs,sort_keys=False,width=4096))
        row.update(healthy_manifest=str(path.relative_to(ROOT)),manifest_sha256=collection.sha(path))
        controls.append(row)
    order=make_order(p['scenarios'],controls,20261004)
    proposals=yaml.safe_load((ROOT/p['requirements_source']).read_text())['proposals']
    hypotheses={x['id']:{'expected_effect':x['expected_effect'],
                         'requirement':x['requirement'],'not_an_outcome_label':True}
                for x in proposals}
    hypotheses['P2-2-1']['expected_effect']='potentially preserves; stronger minimum requires evidence'
    hypotheses['P2-2-2']['expected_effect']='potentially invalidates: unchanged +1 does not meet minimum3'
    collection.save(out/'design-hypotheses.json',{'at':time.time(),'hypotheses':hypotheses,
                    'policy_and_verifier_must_not_load_this_file':True})
    counts=dict(collections.Counter(x['category'] for x in order))
    assert counts=={'main':200,'variant-no-repair-control':40,'original-conditions-control':25,
                    'unrelated-change-control':25,'original-no-repair-control':5}
    p.update(version='formal-main-v1',stage='frozen RQ1 main protocol and initial RQ3a decision recording',
             written_at=time.time(),record_type='formal_control_replay',main_protocol_ready=True,
             registered_main_scenarios=40,main_trials_planned=200,main_trials=0,
             order=order,order_random_seed=20261004,counts=counts,
             comparison_model=str((out/'fault-matching-model.json').relative_to(ROOT)),
             comparison_model_sha256=collection.sha(out/'fault-matching-model.json'),
             source_lock_sha256=collection.sha(ROOT/'sources/sources.lock.yaml'),
             design_hypotheses_sha256=collection.sha(out/'design-hypotheses.json'),
             whole_environment_references=str((out/'whole-environment-references.json').relative_to(ROOT)),
             whole_environment_references_sha256=collection.sha(out/'whole-environment-references.json'),
             additional_runs={'current_queue_controls':95,'supplementary_registration_development':5,
                 'initial_decision_ablation_computations':800,'evidence_only_saved_observation_computations':50,
                 'future_actual_RQ3a_response_trials_planned':600,'future_actual_RQ3a_isolated_replays_maximum':400,
                 'RQ3b_sequences':'not registered in this protocol; planning backend and sequence freeze pending'},
             counterfactual_policy='Every main trial attempts unchanged RQ1 lab replay regardless of initial policy; initial RQ3a decisions are saved first. No actual revalidation-route, reliability-superiority or RQ3b claim follows from these counterfactual outcomes.',
             shared_control_policy='Five original-condition repair repeats per family shared by that family\'s eight variants; report this shared dependence, not 200 independent controls. Each variant has one complete no-repair persistence control; five original no-repair controls and 25 unused-ConfigMap controls are separate.',
             failure_policy='Actual confirmed repair failures continue after confirmed reset. Incomplete collection, pre-action instability or reset failure halts at a preserved record; no silent repeat, dropped main case or outcome-guided adjustment.',
             chronology='full seed reset -> variant -> DNS/image/current-owner healthy acceptance -> fault confirmation -> 60s fault persistence -> saved initial decisions -> current revision/reassessment and shared safety/immediate non-target checks -> unchanged action -> full 210s window -> independent outcome -> explicit cleanup and full confirmed reset -> next trial',
             historical_check_policy='The original qualification business predicate is sampled separately in each target iteration. It is retained beside current criteria and never substitutes for the current outcome or enters policy text. Same checks run in both repair/no-repair arms.',
             method_threshold=model['threshold'],calibration_scope='Five positive families, negative P2/P3 pilots; limited coverage and older P2 profile are reported.',
             prerequisites=['accepted baseline and current serving verifier','same-operator controls',
                 'five-family supplementary history and observed pilot threshold',
                 'frozen current collector, comparison interfaces, action and reset procedure'],
             deferred_requirements={'RQ3a_actual_response_routes':'Separate 600 matched response trials; initial saved decisions do not replace these routes.',
                 'RQ3b':'Fix authorised provider/model and sequence protocol before any main sequence; not a prerequisite for independent RQ1 data collection.'},
             prerequisites_completed=['v4 executor live acceptance','v8 full reuse-flow acceptance','five supplementary original-condition snapshot registrations','observed development calibration','frozen main order/verifier/matched controls'])
    # Eight per family and ten per primary condition; overlap annotations retained.
    assert len(p['scenarios'])==40
    assert set(collections.Counter(r['candidate'] for r in p['scenarios']).values())=={8}
    assert set(collections.Counter(r['condition'] for r in p['scenarios']).values())=={10}
    collection.freeze(out,p)
    print(json.dumps({'main_scenarios':40,'main_replays':200,'separate_controls':95,
                      'threshold':model['threshold'],'protocol_frozen':True}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--calibration',type=Path,required=True);args=parser.parse_args()
    prepare(args.output.resolve(),args.calibration.resolve())
