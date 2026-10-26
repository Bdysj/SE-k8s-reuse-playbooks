"""RQ2 development prototype: explicit supported mappings and three-state rules.

The current role/criterion contract is an attributed manual annotation. It is
not inferred from a scenario ID or from any expected/observed repair outcome.
No mutating task is used to discover a target. New observations never overwrite
the registered history. This module alone is not an accepted RQ3 evaluation.
"""
import hashlib
import json
import time

CONDITIONS = ('target_objects', 'referenced_fields', 'resource_relationships', 'verification_criteria')
SAT, CONTRA, UNKNOWN = 'Satisfied', 'Contradicted', 'Unknown'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',',':')).encode()).hexdigest()


def decision(requirements, remaining_checks=1, omitted=None):
    active = [r for r in requirements if r['condition'] != omitted]
    if not active:
        return {'decision':'Stop reuse', 'reason':'No applicable supported requirements'}
    contradicted = [r['condition'] for r in active if r['state'] == CONTRA]
    if contradicted:
        return {'decision':'Stop reuse', 'reason':'Contradicted prerequisite', 'conditions':contradicted}
    unknown = [r['condition'] for r in active if r['state'] == UNKNOWN]
    if unknown:
        return {'decision':'Revalidate' if remaining_checks > 0 else 'Stop reuse',
                'reason':'Unresolved evidence' if remaining_checks > 0 else 'Validation budget exhausted',
                'conditions':unknown}
    if any(r['state'] != SAT for r in active):
        raise ValueError('Unsupported evidence state')
    return {'decision':'Direct reuse', 'reason':'All applicable requirements supported'}


def contract(row, source, source_sha256):
    cfg = row['construction']
    return {'source':source, 'source_sha256':source_sha256,
        'annotation_origin':'manual declared current business role and verifier; validated separately',
        'namespace':'robot-shop', 'serving_deployment':cfg['target_deployment'],
        'frontend_service':cfg['frontend_service'], 'catalogue_service':cfg['catalogue_service'],
        'required_replicas':cfg['required_replicas'], 'target_container':cfg['target_container'],
        'target_path':cfg['target_path'], 'cart_check':cfg['cart_check'], 'vote_check':cfg['vote_check'],
        'preserve_cart_host':cfg.get('preserve_cart_host'),
        'equivalent_endpoints':{'P1':['/','/index.html'], 'P2':['/api/ratings/api/fetch/Watson'],
          'P3':['/api/catalogue/products'], 'P4':['/api/catalogue/products','/api/catalogue/product/Watson'],
          'P5':['/','/index.html']}}


def compare(candidate, candidate_hash, snapshot, current, now=None, ttl=30, validation=None):
    now = time.time() if now is None else now
    requirements = []
    def add(condition, state, reason, observation=None):
        requirements.append({'condition':condition, 'state':state, 'reason':reason,
            'observation':observation, 'observation_at':snapshot.get('completed_at'),
            'annotation_source':current.get('source'), 'annotation_sha256':current.get('source_sha256')})
    required = ('deployments','services','replicasets','pods','endpointslices')
    stamp = snapshot.get('started_at', snapshot.get('completed_at'))
    if (stamp is None or now-stamp > ttl or now < stamp or any(k not in snapshot for k in required)
            or not current.get('source_sha256')):
        for c in CONDITIONS:
            add(c, UNKNOWN, 'Missing, stale, future-dated, or unattributed observation')
        return requirements
    deployments = {d['metadata']['name']:d for d in snapshot['deployments']}
    services = {d['metadata']['name']:d for d in snapshot['services']}
    # P1's actual read-only discovery output must be supplied by the collector.
    if candidate == 'P1':
        resolved = snapshot.get('p1_discovery_names')
        if resolved is None:
            action_name = None
            add('target_objects', UNKNOWN, 'The admitted read-only discovery result is missing')
        elif not resolved:
            action_name = None
            add('target_objects', CONTRA, 'Existing grep-web discovery resolves no target', resolved)
        else:
            action_name = resolved[0]
            add('target_objects', SAT if action_name == current['serving_deployment'] else CONTRA,
                'First result of the unchanged discovery must have the declared serving role', resolved)
    else:
        action_name = 'ratings' if candidate == 'P2' else 'web'
        found = services.get(action_name) if candidate == 'P5' else deployments.get(action_name)
        add('target_objects', SAT if found else CONTRA, 'Frozen literal resource must exist', action_name)
    dep = deployments.get(action_name)
    serving = deployments.get(current['serving_deployment'])
    relation_name = current['frontend_service'] if candidate == 'P5' else current['serving_deployment']
    if action_name is None:
        add('resource_relationships', UNKNOWN, 'No resolved action target')
    else:
        state = SAT if action_name == relation_name else CONTRA
        observed_frontend = snapshot.get('client_frontend_service')
        binding = None
        if state == SAT:
            if observed_frontend is None:
                state = UNKNOWN
            elif observed_frontend != current['frontend_service']:
                state = CONTRA
            elif candidate != 'P5':
                service_name = 'ratings' if candidate == 'P2' else current['frontend_service']
                service = services.get(service_name)
                selector = service.get('spec',{}).get('selector') if service else None
                labels = serving.get('spec',{}).get('template',{}).get('metadata',{}).get('labels') if serving else None
                if selector is None or labels is None:
                    state = UNKNOWN
                else:
                    binding = bool(selector) and all(labels.get(k)==value for k,value in selector.items())
                    state = SAT if binding else CONTRA
        add('resource_relationships', state,
            'Declared serving role must agree with current client route and Service/template binding',
            {'action_object':action_name, 'serving_object':relation_name,
             'observed_frontend_service':observed_frontend,'selector_binding':binding})
    if candidate == 'P5':
        svc = services.get('web')
        if svc is None or serving is None:
            add('referenced_fields', UNKNOWN, 'Selector effect unavailable without Service and serving template')
        else:
            predicted = dict(svc['spec'].get('selector', {})); predicted['service'] = 'web'
            labels = serving['spec']['template']['metadata'].get('labels', {})
            add('referenced_fields', SAT if all(labels.get(k) == v for k,v in predicted.items()) else CONTRA,
                'Fixed merge changes only service; retained selector keys must match serving Pod labels',
                {'predicted_selector':predicted,'serving_labels':labels})
    elif dep is None:
        add('referenced_fields', UNKNOWN, 'Action Deployment field observation unavailable')
    elif candidate == 'P1':
        add('referenced_fields', CONTRA if dep['spec'].get('paused',False) else SAT,
            'Admitted rollout restart requires an unpaused Deployment', {'paused':dep['spec'].get('paused',False)})
    elif candidate == 'P2':
        n = dep['spec'].get('replicas')
        valid = isinstance(n,int) and not isinstance(n,bool)
        add('referenced_fields', UNKNOWN if not valid else (SAT if n+1 >= current['required_replicas'] else CONTRA),
            'Registered arithmetic mapping is current replicas plus exactly one',
            {'current':n, 'required_minimum':current['required_replicas']})
    elif candidate == 'P3':
        spec = dep['spec']['template']['spec']
        anti = spec.get('affinity',{}).get('podAntiAffinity',{}).get('requiredDuringSchedulingIgnoredDuringExecution',[])
        node_facts = snapshot.get('p3_fixed_affinity_supported')
        # Hard anti-affinity is deliberately unresolved until its actual rollout is validated.
        state = UNKNOWN if anti or node_facts is None else (SAT if node_facts is True else CONTRA)
        add('referenced_fields', state, 'Fixed resources/affinity mapping; retained scheduling constraints require evidence',
            {'hard_anti_affinity':anti, 'fixed_linux_affinity_supported':node_facts,
             'qos_annotation_is_not_actual_qos':True})
    elif candidate == 'P4':
        # Consumption comes from registered image/entrypoint mapping, not an env-name guess.
        consumption = snapshot.get('p4_registered_consumption_unchanged')
        target_exists = 'catalogue' in services
        state = CONTRA if not target_exists or current['catalogue_service'] != 'catalogue' else (SAT if consumption is True else UNKNOWN)
        add('referenced_fields', state, 'Fixed catalogue literal needs the registered startup-consumption path and backend',
            {'catalogue_service_exists':target_exists,'registered_consumption_unchanged':consumption})
    else:
        raise ValueError('Candidate outside admitted five-family subset')
    stricter = current['cart_check'] or current['vote_check'] or current['required_replicas'] > 1
    mapped = current['target_path'] in current['equivalent_endpoints'].get(candidate, [])
    criteria_digest = digest(current)
    validation_matches = (validation and validation.get('candidate_sha256') == candidate_hash
        and validation.get('contract_sha256') == criteria_digest and validation.get('fidelity_passed') is True
        and isinstance(validation.get('completed_at'),(int,float))
        and 0 <= now-validation['completed_at'] <= ttl
        and snapshot.get('relevant_revision') is not None
        and validation.get('relevant_revision') == snapshot.get('relevant_revision'))
    validated = validation_matches and validation.get('outcome') == 'Successful repair'
    failed = validation_matches and validation.get('outcome') == 'Confirmed failure'
    add('verification_criteria', CONTRA if failed else (SAT if (mapped and not stricter) or validated else UNKNOWN),
        'Equivalent check mapping or fresh fidelity-checked replay under the current criteria is required',
        {'contract_sha256':criteria_digest,'stricter':bool(stricter),'mapped_endpoint':mapped,'validated':bool(validated)})
    if validated:
        for r in requirements:
            if r['state'] == UNKNOWN and r['condition'] in validation.get('verified_requirements', []):
                r.update(state=SAT, reason='Uncertainty resolved by fresh matching isolated replay evidence',
                         validation_record=validation.get('record'), validation_record_sha256=validation.get('record_sha256'))
    return requirements


def assess(candidate, candidate_hash, snapshot, current, *, remaining_checks=1, omitted=None, now=None, validation=None):
    states = compare(candidate, candidate_hash, snapshot, current, now=now, validation=validation)
    result = decision(states, remaining_checks, omitted)
    return {'prototype':'rq2-development-v1', 'candidate':candidate, 'candidate_sha256':candidate_hash,
        'observation_sha256':digest(snapshot), 'contract_sha256':digest(current), 'states':states,
        'omitted_condition':omitted, 'remaining_checks':remaining_checks,
        'initial_decision':result['decision'], **result,
        'relevant_revision':snapshot.get('relevant_revision'),
        'next_action':{'Direct reuse':'recheck relevant state and common safety gate',
                       'Revalidate':'refresh unresolved observations or fidelity-checked isolated replay',
                       'Stop reuse':'end reuse attempt and use declared fallback'}[result['decision']]}


def before_execution(record, current_revision):
    if record.get('relevant_revision') is None or record['relevant_revision'] != current_revision:
        return {'permitted':False,'next_action':'reassess','reason':'Missing or changed relevant revision'}
    return {'permitted':record['decision']=='Direct reuse','next_action':'common safety gate'}
