#!/usr/bin/env python3
"""Save S3 requirements/proposals; this does not attest feasibility or register cases."""
import time
from pathlib import Path
import yaml
ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'candidates/requirements.v1.yaml'
if OUT.exists():raise SystemExit('Refusing to replace previously recorded expectations')
registry=yaml.safe_load((ROOT/'candidates/registry.yaml').read_text())
conditions=['target_objects','referenced_fields','resource_relationships','verification_criteria']
requirements={
'P1':['Existing deployment-list/grep web rule resolves intended serving Deployment, without adding discovery.',
      'Restart template update and controller rollout are admitted; paused=true is unsupported by kubectl restart.',
      'Current frontend Service routes to the Pods of the Deployment restarted by the candidate.',
      'Historical homepage status/content/latency establishes only the original read criterion.'],
'P2':['Literal Deployment ratings in robot-shop owns the intended serving Pods.',
      'Numeric spec.replicas plus one satisfies the current required ready-replica minimum.',
      'Ratings Service selects the scaled Deployment and Pods can access existing MySQL Service/account.',
      'Historical fetch does not establish persistent vote write/read correctness.'],
'P3':['Literal Deployment web and actual named-container ownership remain valid.',
      'Fixed resources admit workload; additional affinity requires beta.kubernetes.io/os=linux on an eligible node. Legacy annotation alone does not establish actual QoS.',
      'Current serving desired template belongs to the workload whose resources the candidate changes.',
      'Recovery binds to current matching ReplicaSet/Pod; stronger transaction criteria require new evidence.'],
'P4':['Literal Deployment web is the frontend consuming the repaired variable.',
      'CATALOGUE_HOST is consumed by envsubst; catalogue resolves to intended backend; updates by name preserve other consumed variables.',
      'Patched frontend receives current traffic and restarted nginx consumes updated variable.',
      'Catalogue read success does not establish cart write/read integration.'],
'P5':['Literal Service web is the routing object to be repaired.',
      'Fixed selector.service=web matches current Pod labels and retained selector keys also match.',
      'Current client path traverses repaired Service and selected endpoints are actual frontend Pods.',
      'Historical homepage success does not establish current stateful cart write/read integration.']}
objects={
'P1':('Replace web Pod, preserving Deployment web, discovery rule and Service.','Rename serving web Deployment to storefront without web substring; preserve labels/Service; fault storefront.'),
'P2':('Replace ratings Pod, preserving Deployment and Service.','Rename ratings Deployment to feedback; preserve labels/Service; scale feedback to zero.'),
'P3':('Replace web Pod, preserving Deployment/current ownership.','Rename web Deployment to storefront; preserve labels/Service; underallocate storefront container.'),
'P4':('Replace web Pod, preserving Deployment and variable consumption.','Rename web Deployment to storefront; preserve labels/Service; inject bad host on storefront.'),
'P5':('Recreate Service web with new UID and unchanged routing contract; confirm healthy before fault.','Rename client-facing Service to storefront; update load/probe frontend URL; remove web Service; fault storefront selector.')}
fields={
'P1':('Set admitted RollingUpdate maxSurge=2, maxUnavailable=0, unpaused.','Set paused=true before fixed-victim fault; confirm healthy before injection; frozen restart is unsupported.'),
'P2':('Healthy desired/minimum replicas=2; underprovision to 1; frozen +1 can restore minimum.','Healthy desired/minimum replicas=3; underprovision to 1; frozen +1 is insufficient. Explicit overlap with verification/multiplicity.'),
'P3':('Calibrated underallocation memory 1Mi -> 2Mi, below observed working set; current-template readiness still required.','Remove beta.kubernetes.io/os on all nodes, retain hostname/resources. Baseline lacks this affinity and must remain healthy; fixed affinity then lacks an eligible node. Restore exact labels.'),
'P4':('Add consumed CART_HOST as equivalent FQDN before CATALOGUE_HOST in env list; inject bad catalogue host; repair by name must preserve CART_HOST.','Move backend to catalogue-v2 Service; update web/cart CATALOGUE_HOST and ratings CATALOGUE_URL; remove old catalogue. Full healthy workload required before bad web-host fault. Fixed catalogue literal becomes unsuitable.'),
'P5':('Add track=stable to frontend Pods/Service selector; fault changes service key only; fixed merge retains track.','Recreate same-named web Deployment with selector app=web, labels app=web/service=frontend; initial Service selects frontend. After wrong-selector fault, fixed web value is unsuitable. Delete immutable-selector variant during cleanup.')}
relationships={
'P1':('Recreate frontend Service, preserving selector/ports and logical backend.','Keep web Deployment; route current Service to same-image frontend-live Deployment; fault that Pod. Frozen locator still selects idle web.'),
'P2':('Replace MySQL Pod with same PVC/Service/accounts; wait actual SQL and ratings health; no in-trial data restore.','Keep ratings; route ratings Service to same-image feedback-live Deployment; establish healthy, scale feedback-live to zero. Candidate scales idle ratings.'),
'P3':('Recreate frontend Service, preserving connection to current web Pods.','Keep web; route Service to same-image frontend-live Deployment; underallocate there. Verify current owner/template there, not idle web.'),
'P4':('Replace catalogue Pod, preserving Service and consumed host connection.','Keep web; route Service to same-image frontend-live; inject wrong consumed host there. Frozen candidate patches idle web.'),
'P5':('Replace backend web Pod; retain Service/selector; EndpointSlice targetRef matches current owned Pod.','Keep web Service; move load/probe frontend to public-web Service with same initial backend; fault public-web selector. Candidate repairs idle web.')}
verification={
'P1':('Use /index.html instead of / through same Service; confirm equivalent content/latency.','Require fresh-cart add/read with Watson SKU and quantity, plus historical checks.'),
'P2':('Structured JSON rating_count nonnegative integer on same fetch endpoint; retain HTTP/latency/rollout criteria.','Require accepted vote and persistent fetch count increase; predeclare concurrency/race policy and validate healthy feasibility.'),
'P3':('EndpointSlice current-Pod ownership as equivalent evidence, retaining current template/resources/QoS/readiness and business criteria.','Require cart add/read state assertions plus current-template rollout and catalogue read.'),
'P4':('Use /api/catalogue/product/Watson with expected SKU; document equivalent catalogue-host fault dependence.','Require cart add/read integration plus catalogue read and consumed configuration.'),
'P5':('Use /index.html instead of / through repaired Service; confirm equivalent content.','Require cart add/read through current client-facing Service, plus homepage; no direct-Pod shortcut.')}
rows=[]
for c in registry['candidates']:
    key=c['id']
    for i,kind in enumerate(conditions):
        for j,change in enumerate([objects,fields,relationships,verification][i][key]):
            rows.append({'id':f'{key}-{i+1}-{j+1}','candidate':key,'condition':kind,'requirement':requirements[key][i],
                         'change':change,'expected_effect':'potentially_preserves_reuse' if j==0 else
                         'old_success_evidence_insufficient' if kind=='verification_criteria' else 'potentially_invalidates_reuse',
                         'expectation_is_not_outcome_label':True,'feasibility_verified':False,
                         'overlap_policy':'Record every additional affected condition; no independent single-condition causal-effect claim.',
                         'verification_note':'Stricter criteria may require new evidence even when unchanged repair succeeds; never force failure labels.'})
assert len(rows)==40 and len({r['id'] for r in rows})==40
plan={'version':'requirements-draft-v1','written_at_epoch':time.time(),'stage':'S3 draft; changed-condition feasibility not yet tested',
      'qualification_setup':'results/qualification-v3/protocol.json','candidate_hashes':{c['id']:c['candidate_sha256'] for c in registry['candidates']},
      'constants':'Images, workload demand, physical cluster capacity and security policies fixed; no candidate edits or input rescues.',
      'feasibility_gate':'Legal mutation, full healthy state before fault, observable intended symptom, explicit owner/Service binding and controlled reset including added objects and node labels. Infeasible proposals may change before main freeze.',
      'status_counts':{'proposals':40,'feasibility_verified':0,'registered_main_scenarios':0,'main_replay_trials':0},
      'requirements':{k:dict(zip(conditions,v)) for k,v in requirements.items()},'proposals':rows,
      'open_design_points':['P2 replica minimum changes also alter verification/multiplicity; do not claim a field-type violation.',
                            'P4 relocation must update all actual catalogue consumers so healthy controls pass.',
                            'P5 changed immutable selector requires explicit deletion/recreation cleanup.',
                            'Vote-count assertions under concurrent load need an explicit race policy before testing.'],
      'references':['D-DN-3.1_EN.docx 3.5.2-3.5.3','https://raw.githubusercontent.com/kubernetes/kubectl/v0.33.5/pkg/polymorphichelpers/objectrestarter.go']}
OUT.write_text(yaml.safe_dump(plan,sort_keys=False,width=110))
print('Saved 20 requirements and 40 proposals; verified-feasibility/main counts remain zero.')
