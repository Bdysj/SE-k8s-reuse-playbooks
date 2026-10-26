"""Restore declared incident construction before the unchanged baseline reset.

This version accepts frozen logical identities when the current target is renamed.
This is post-decision cleanup, never a repair candidate or a decision input.
The physical replay executor already removes these declared deployments before
reset; declined incidents need the same operation for immutable selectors.
"""
import time

import registration_trials as reg
import reset_baseline as rb
import reuse_flow_executor as ex


def validate_scope(row, protocol, declared):
    if row not in protocol['scenarios']:
        raise RuntimeError('Cleanup requires the exact frozen incident construction')
    cfg = row['construction']; names = cfg.get('delete_deployments_before', [])
    # A renamed current target still restores its original baseline identity.
    # The frozen target container identifies that logical serving workload;
    # unrelated database/workload containers cannot enter this cleanup scope.
    if any(name not in declared or declared[name]['kind'] != 'Deployment'
           or cfg['target_container'] not in declared[name].get('containers', {})
           for name in names):
        raise RuntimeError('Incident cleanup extends beyond its declared logical target Deployment')
    return names


def reset_incident(row, protocol, log):
    if rb.CONTEXT != protocol['context'] or rb.NS != protocol['namespace']:
        raise RuntimeError('Incident cleanup context/namespace does not match the frozen protocol')
    declared = rb.declared_fingerprint()['workloads']
    names = validate_scope(row, protocol, declared)
    receipt = {'at': time.time(), 'context': rb.CONTEXT, 'namespace': rb.NS,
        'scenario': row['id'], 'phase': 'post-decision incident cleanup and fixed baseline reset',
        'candidate_action': False, 'declared_deployments': list(names),
        'removed_deployments': [], 'baseline_confirmed': False,
        'manifest_sha256': ex.sha(ex.ROOT/protocol['manifest']),
        'reset_source_sha256': ex.sha(ex.ROOT/'tools/reset_baseline.py')}
    path = log.with_suffix('.cleanup.json')
    ex.save(path, receipt)
    try:
        rb.kubectl('delete', 'podchaos', 'pilot-pod-fail', '--ignore-not-found', '--timeout=60s')
        for name in names:
            rb.kubectl('delete', 'deployment', name, '--ignore-not-found', '--wait=true', '--timeout=120s')
            receipt['removed_deployments'].append(name)
            ex.save(path, receipt)
        reg.reset_s4(log, protocol)
        receipt['baseline_confirmed'] = True
    except Exception as error:
        receipt['error'] = str(error)
        raise
    finally:
        receipt['completed_at'] = time.time()
        ex.save(path, receipt)
