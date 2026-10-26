"""Retain absent historical subjects without changing the current verifier.

Transport, permission, and probe errors still stop collection. Only a successful
API response proving that the old Deployment is absent becomes diagnostic data.
"""
import qualification as q
import reset_baseline as rb


def observe(key):
    path, pattern = q.PATHS[key]
    result = q.request('http://web:8080' + path, pattern)
    if key == 'P3':
        name = q.FAMILY[key]
        raw = rb.kubectl('get', 'deployment', name, '--ignore-not-found', '-o', 'json')
        if not raw.strip():
            result['rollout'] = {
                'workload': name, 'subject_present': False, 'complete': False,
                'reason': 'historical_target_absent',
            }
            result['historical_subject_absent'] = True
            result['healthy'] = False
        else:
            result['rollout'] = q.current_rollout(name)
            result['healthy'] = result['healthy'] and result['rollout']['complete']
    return result
