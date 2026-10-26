"""Prospective RQ3a setup fix: only a saved complete60s sample may end persistence.

The original implementation and all its archived attempts remain immutable.
Evidence serialization time cannot make an earlier sub-threshold sample complete.
"""
from run_reuse_flow_development import (
    time, reg, rb, ex, s, v, supported, confirm_environment,
)


def setup(row,p,path,current):
    reg.reset_s4(path/'initial-reset.log',p);rb.ensure_probe()
    r={'initial_reset_log':str(path/'initial-reset.log'),'setup_checks':[], 'healthy_window':[],
       'setup_phase':'apply healthy variant'}
    ex.save(path/'incident-setup.json',r)
    r['replacement']=ex.apply_variant(row,ex.ROOT/row['healthy_manifest'])
    r['environment_check']=confirm_environment(p)
    ex.save(path/'incident-setup.json',r)
    if not r['environment_check']['passed']:raise RuntimeError('Declared resolver environment not applied')
    r['setup_phase']='healthy construction checks';ex.save(path/'incident-setup.json',r)
    end=time.monotonic()+90
    while True:
        health=s.healthy(row,ex.ROOT/row['healthy_manifest']);r['setup_checks'].append(health)
        ex.save(path/'incident-setup.json',r)
        if health['passed']:break
        if time.monotonic()>=end:raise RuntimeError('Healthy variant setup rejected')
        time.sleep(5)
    start=time.monotonic()
    r['setup_phase']='healthy observation window';ex.save(path/'incident-setup.json',r)
    while True:
        target=v.target(row)
        r['latest_target_before_non_target']=target;ex.save(path/'incident-setup.json',r)
        nt=v.non_target(row)
        r['healthy_window'].append({'target':target,'non_target':nt})
        ex.save(path/'incident-setup.json',r)
        if not target['healthy'] or not nt['passed']:raise RuntimeError('Healthy gate rejected')
        if time.monotonic()-start>=p['timing']['healthy_window']:break
        time.sleep(5)
    r['setup_phase']='read-only incident evidence';ex.save(path/'incident-setup.json',r)
    r['healthy_snapshot']=v.collect()
    r['cached_read_only_observation']=supported.collect(row['candidate'],current)
    r['fault_activation_at']=time.time();r['injection']=ex.inject_current(row)
    r['setup_phase']='fault confirmation';ex.save(path/'incident-setup.json',r)
    end=time.monotonic()+p['timing']['fault_confirmation'];r['fault_confirmation']=[]
    while True:
        target=v.target(row);r['fault_confirmation'].append(target)
        ex.save(path/'incident-setup.json',r)
        if not target['healthy']:break
        if time.monotonic()>=end:raise RuntimeError('Intended fault not observed')
        time.sleep(3)
    start=time.monotonic();r['fault_persistence']=[];r['setup_phase']='fault persistence window'
    while True:
        target=v.target(row);nt=v.non_target(row)
        r['fault_persistence'].append({'at':time.time(),'elapsed_seconds':time.monotonic()-start,
                                     'target':target,'non_target':nt})
        ex.save(path/'incident-setup.json',r)
        if target['healthy'] or not nt['passed']:raise RuntimeError('Fault self-resolved or pre-existing non-target instability')
        if r['fault_persistence'][-1]['elapsed_seconds']>=60:break
        time.sleep(5)
    return r

