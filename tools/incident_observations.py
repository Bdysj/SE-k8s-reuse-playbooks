"""Ordinary pre-action incident inputs, without construction or outcome labels."""
import copy


def observed_incident(sample, workload, snapshot=None):
    symptoms, codes = [], []
    status = sample.get('status')
    if isinstance(status,int) and not 200 <= status < 300:
        symptoms.append('business HTTP request failed')
        codes.append('HTTP '+str(status))
    if sample.get('curl_rc') not in (None,0):
        symptoms.append('business HTTP transport error')
        codes.append('curl '+str(sample['curl_rc']))
    rollout = sample.get('workload',sample.get('rollout',{}))
    complete = rollout.get('healthy',rollout.get('complete'))
    if complete is False:
        symptoms.append('current workload rollout incomplete')
    if rollout.get('desired') == 0:
        symptoms.append('workload has no desired replicas')
    if sample.get('endpoint_binding',{}).get('healthy') is False:
        symptoms.append('current Service has no valid serving endpoints')
    if sample.get('content_match') is False and isinstance(status,int) and 200 <= status < 300:
        symptoms.append('business HTTP response content mismatch')
    if snapshot:
        # Current query responses only. No injection, mutation or repair metadata.
        for pod in snapshot.get('pods',[]):
            name = pod['metadata']['name']
            if not (name==workload or name.startswith(workload+'-')):
                continue
            for container in pod.get('status',{}).get('containerStatuses',[]):
                for section in ('state','lastState'):
                    for value in container.get(section,{}).values():
                        if value.get('reason'):
                            codes.append(value['reason'])
    return {'workload':[workload], 'symptoms':sorted(set(symptoms)),
            'error_codes':sorted(set(codes))}


def before_action(record, workload):
    start = record.get('action_start_seconds',record.get('repair_start_seconds'))
    if not isinstance(start,(int,float)):
        raise ValueError('No observed action boundary: do not fabricate a labelled incident')
    available = [x for x in record.get('samples',[]) if isinstance(x.get('window_seconds'),(int,float))
                 and x['window_seconds'] < start]
    if not available:
        raise ValueError('No actual pre-action target sample')
    # Fixed first pre-action sample, independent of outcome or error prevalence.
    sample = copy.deepcopy(available[0])
    return {'incident':observed_incident(sample,workload),
            'source_sample_window_seconds':sample['window_seconds'],
            'source_boundary_seconds':start,
            'selection':'first recorded target sample strictly before action',
            'input_scope':'observed workload, HTTP/rollout symptoms and ordinary error codes only'}
