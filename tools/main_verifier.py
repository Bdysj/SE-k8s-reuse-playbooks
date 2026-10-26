"""Scenario-bound observations and Section 3.6 labels, independent of policy.

New development implementation: historical S2/S3/S4 executors are untouched.
The scenario contract is evaluator input, never an expected outcome label.
"""
import copy
import time

import qualification as q
import scenario_feasibility as s
import reset_baseline as rb

KINDS = ('deployments', 'statefulsets', 'replicasets', 'pods', 'services', 'endpointslices')


def collect():
    started = time.time()
    data = {kind: rb.kjson('get', kind)['items'] for kind in KINDS}
    return {'started_at': started, 'completed_at': time.time(), **data}


def find(data, kind, name):
    return next((x for x in data[kind] if x['metadata']['name'] == name), None)


def controlled_by(obj, kind, uid):
    return any(x.get('controller') and x.get('kind') == kind and x.get('uid') == uid
               for x in obj['metadata'].get('ownerReferences', []))


def template(value):
    value = copy.deepcopy(value)
    value.get('metadata', {}).get('labels', {}).pop('pod-template-hash', None)
    return {'metadata': {'labels': value.get('metadata', {}).get('labels', {}),
                         'annotations': value.get('metadata', {}).get('annotations', {})},
            'spec': value['spec']}


def workload(data, name):
    d = find(data, 'deployments', name)
    stateful = d is None
    if stateful:
        d = find(data, 'statefulsets', name)
    if d is None:
        return {'name': name, 'healthy': False, 'reason': 'declared workload absent', 'owned_uids': [], 'current_uids': []}
    if stateful:
        owned = [p for p in data['pods'] if controlled_by(p, 'StatefulSet', d['metadata']['uid'])]
        current = [p for p in owned if p['metadata'].get('labels', {}).get('controller-revision-hash') == d.get('status', {}).get('updateRevision')]
    else:
        rs = [r for r in data['replicasets'] if controlled_by(r, 'Deployment', d['metadata']['uid'])]
        latest = {r['metadata']['uid'] for r in rs if template(r['spec']['template']) == template(d['spec']['template'])}
        owned = [p for p in data['pods'] if any(controlled_by(p, 'ReplicaSet', r['metadata']['uid']) for r in rs)]
        current = [p for p in owned if any(controlled_by(p, 'ReplicaSet', uid) for uid in latest)]
    current = [p for p in current if not p['metadata'].get('deletionTimestamp')]
    desired = d['spec'].get('replicas', 1)
    st = d.get('status', {})
    healthy = (desired > 0 and st.get('observedGeneration') == d['metadata'].get('generation')
               and st.get('updatedReplicas', 0) == desired and st.get('readyReplicas', 0) == desired
               and len(current) == desired and all(q.ready(p) for p in current))
    if not stateful:
        healthy = healthy and st.get('availableReplicas', 0) == desired and len(latest) == 1
    return {'name': name, 'uid': d['metadata']['uid'], 'generation': d['metadata'].get('generation'),
            'desired': desired, 'healthy': bool(healthy),
            'owned_uids': sorted(p['metadata']['uid'] for p in owned),
            'current_uids': sorted(p['metadata']['uid'] for p in current),
            'terminating_uids': sorted(p['metadata']['uid'] for p in owned if p['metadata'].get('deletionTimestamp'))}


def endpoint_binding(data, service, owner):
    w = workload(data, owner)
    eps = [e for sl in data['endpointslices']
           if sl['metadata'].get('labels', {}).get('kubernetes.io/service-name') == service
           for e in (sl.get('endpoints') or []) if (e.get('conditions') or {}).get('ready') is True]
    current = set(w['current_uids'])
    valid = (find(data, 'services', service) is not None and bool(eps)
             and all(e.get('targetRef', {}).get('kind') == 'Pod'
                     and e.get('targetRef', {}).get('uid') in current
                     and not (e.get('conditions') or {}).get('terminating', False) for e in eps))
    return {'service': service, 'intended_owner': owner, 'current_owner': w,
            'ready_endpoint_uids': [e.get('targetRef', {}).get('uid') for e in eps], 'healthy': bool(valid)}


def target(row):
    cfg = row['construction']
    base = 'http://' + cfg['frontend_service'] + ':8080'
    o = s.request(base + cfg['target_path'], cfg['target_pattern'], 1.0)
    data = collect()
    state = workload(data, cfg['target_deployment'])
    service = 'ratings' if row['candidate'] == 'P2' else cfg['frontend_service']
    binding = endpoint_binding(data, service, cfg['target_deployment'])
    o.update(workload=state, endpoint_binding=binding)
    o['healthy'] = bool(o['healthy'] and state['healthy'] and binding['healthy']
                        and state['desired'] >= cfg['required_replicas'])
    if row['candidate'] == 'P2':
        count = o.get('json', {}).get('rating_count')
        o['structured_count_valid'] = isinstance(count, int) and not isinstance(count, bool) and count >= 0
        o['healthy'] = o['healthy'] and o['structured_count_valid']
    if cfg.get('cart_check'):
        o['cart'] = s.cart(base)
        o['healthy'] = o['healthy'] and o['cart']['passed']
    if cfg.get('vote_check') and o['healthy']:
        o['vote'] = s.vote(base)
        o['healthy'] = o['healthy'] and o['vote']['passed']
    if cfg.get('preserve_cart_host'):
        deployment = find(data, 'deployments', cfg['target_deployment'])
        env = s.container(deployment).get('env', [])
        o['cart_host_preserved'] = any(e['name'] == 'CART_HOST' and e.get('value') == cfg['preserve_cart_host'] for e in env)
        o['healthy'] = o['healthy'] and o['cart_host_preserved']
    o['completed_at'] = time.time()
    return o


def non_target(row):
    """Stable named checks; retiring Pods are accepted only with a ready current rollout."""
    start = time.time()
    cfg = row['construction']
    checks = {}
    for name, url, pattern in q.DIRECT:
        if row['candidate'] == 'P2' and name == 'ratings':
            continue
        if name == 'catalogue':
            url = 'http://' + cfg['catalogue_service'] + ':8080/products'
        checks['http:' + name] = s.request(url, pattern)
    data = collect()
    # Use the declared manifest, not the surviving live set: deletion must fail.
    for name in row['non_target_workloads']:
        checks['workload:' + name] = workload(data, name)
    return {'started_at': start, 'completed_at': time.time(), 'checks': checks,
            'passed': all(c['healthy'] is True for c in checks.values())}


def regression(pre, posts, complete):
    """A failure already present before action cannot become a new regression."""
    if pre is None:
        return None, []
    confirmed = sorted({name for post in posts for name, check in post.get('checks', {}).items()
                        if pre.get('checks', {}).get(name, {}).get('healthy') is True
                        and check.get('healthy') is False})
    if confirmed:
        return True, confirmed
    names = set(pre.get('checks', {}))
    observable = (complete and bool(posts) and bool(names)
                  and all(c.get('healthy') is True for c in pre['checks'].values())
                  and all(set(p.get('checks', {})) == names
                          and all(c.get('healthy') is True for c in p['checks'].values()) for p in posts))
    return (False if observable else None), []


def outcome(execution_complete, target_recovery, non_target_regression, action_executed=True):
    if not action_executed:
        return 'Not executed'
    if execution_complete is False or target_recovery is False or non_target_regression is True:
        return 'Confirmed failure'
    if execution_complete is True and target_recovery is True and non_target_regression is False:
        return 'Successful repair'
    return 'Indeterminate'
