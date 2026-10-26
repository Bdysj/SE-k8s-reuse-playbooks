"""Conservative supplemental mappings for the admitted RQ2 prototype.

v2 admits exact integer Ki node-memory units observed in the live API.
Historical assessor sources remain frozen. This development layer downgrades
unproved requirements rather than treating a Linux label or role annotation as
complete evidence. Only explicit, fresh matching validation can resolve Unknown.
"""
import copy
import resource_quantities
import json
import time

import yaml
import main_verifier as v
import reset_baseline as rb
import reuse_assessor as a
import reuse_evidence as e


def registered_container_unchanged(snapshot, deployment, container):
    documents = [x for x in yaml.safe_load_all(rb.MANIFEST.read_text()) if x]
    old = next((x for x in documents if x['kind']=='Deployment' and x['metadata']['name']==deployment), None)
    live = v.find(snapshot, 'deployments', deployment)
    if old is None or live is None:
        return None
    def find(d):
        return next((c for c in d['spec']['template']['spec']['containers'] if c['name']==container), None)
    before, after = find(old), find(live)
    if before is None or after is None:
        return None
    return all(before.get(k)==after.get(k) for k in ('image','command','args','volumeMounts','workingDir'))


def scheduler_mapping(snapshot, deployment):
    """Sufficient resource/placement check for one replacement web Pod only.

    Hard affinity between Pods, topology spreading, taints, nondefault scheduler
    or opaque quantity units are deliberately outside this automatic mapping.
    """
    if deployment is None or 'scheduler_nodes' not in snapshot or 'scheduled_pods' not in snapshot:
        return None, 'Scheduler capacity observation unavailable'
    spec = deployment['spec']['template']['spec']
    affinity = spec.get('affinity', {})
    if (affinity.get('podAffinity') or affinity.get('podAntiAffinity')
            or spec.get('topologySpreadConstraints') or spec.get('schedulerName','default-scheduler')!='default-scheduler'
            or len(spec.get('containers',[]))!=1 or spec.get('initContainers') or spec.get('overhead')):
        return None, 'Retained scheduling or multi-container semantics require isolated validation'
    selector = spec.get('nodeSelector', {})
    eligible = [n for n in snapshot['scheduler_nodes'] if not n.get('unschedulable')
                and n.get('ready') is True and not n.get('taints')
                and n.get('labels',{}).get('beta.kubernetes.io/os')=='linux'
                and all(n.get('labels',{}).get(k)==val for k,val in selector.items())]
    if not eligible:
        possible = [n for n in snapshot['scheduler_nodes'] if not n.get('unschedulable')
                    and n.get('labels',{}).get('beta.kubernetes.io/os')=='linux'
                    and all(n.get('labels',{}).get(k)==val for k,val in selector.items())]
        if any(n.get('ready') is None or n.get('taints') for n in possible):
            return None, 'Node readiness or taint/toleration semantics need further evidence'
        return False, 'No ready untainted node satisfies retained nodeSelector and fixed Linux affinity'
    for node in eligible:
        alloc = node['allocatable']
        cpu, memory = rb.norm_cpu(alloc.get('cpu')), resource_quantities.memory_bytes(alloc.get('memory'))
        if not isinstance(cpu,int) or not isinstance(memory,int):
            return None, 'Unsupported node allocation quantity'
        used_cpu = used_memory = 0
        for pod in snapshot['scheduled_pods']:
            if pod.get('nodeName')!=node['name'] or pod.get('phase') in ('Succeeded','Failed'):
                continue
            reqs = []
            for c in pod.get('containers',[]):
                values = c.get('resources',{}).get('requests',{})
                pair = (rb.norm_cpu(values.get('cpu','0')), resource_quantities.memory_bytes(values.get('memory','0')))
                if not all(isinstance(x,int) for x in pair):
                    return None, 'Unsupported scheduled Pod resource quantity'
                reqs.append(pair)
            init_cpu = init_mem = 0
            for c in pod.get('initContainers',[]):
                # Restartable init containers have different accounting semantics.
                if c.get('restartPolicy') == 'Always':
                    return None, 'Restartable init container accounting requires validation'
                values = c.get('resources',{}).get('requests',{})
                pair = (rb.norm_cpu(values.get('cpu','0')), resource_quantities.memory_bytes(values.get('memory','0')))
                if not all(isinstance(x,int) for x in pair):
                    return None, 'Unsupported init container resource quantity'
                init_cpu, init_mem = max(init_cpu,pair[0]), max(init_mem,pair[1])
            overhead = pod.get('overhead',{})
            overhead_cpu = rb.norm_cpu(overhead.get('cpu','0'))
            overhead_mem = resource_quantities.memory_bytes(overhead.get('memory','0'))
            if not isinstance(overhead_cpu,int) or not isinstance(overhead_mem,int):
                return None, 'Unsupported Pod overhead quantity'
            used_cpu += max(sum(x[0] for x in reqs),init_cpu)+overhead_cpu
            used_memory += max(sum(x[1] for x in reqs),init_mem)+overhead_mem
        # Account conservatively for one extra Pod while the old one is retained.
        # Frozen P3 assigns 100m/256Mi to the one admitted application container.
        if cpu-used_cpu>=100 and memory-used_memory>=256*2**20 and node.get('pressure') is False:
            return True, 'One replacement Pod fits observed requests and the supported retained placement'
    return None, 'Resource headroom or node-pressure proof is insufficient; do not predict repair failure'


def collect(candidate, current):
    snapshot = e.collect(candidate, current)
    if candidate == 'P2':
        d = v.find(snapshot,'deployments','ratings')
        c = next((c for c in d['spec']['template']['spec']['containers'] if c['name']=='ratings'),None) if d else None
        env = {x['name']:x for x in c.get('env',[])} if c else {}
        pdo = env.get('PDO_URL',{}).get('value')
        mapping = registered_container_unchanged(snapshot,'ratings','ratings')
        dsn_matches = (None if 'valueFrom' in env.get('PDO_URL',{}) else
            ('PDO_URL' not in env or pdo == 'mysql:host=mysql;dbname=ratings;charset=utf8mb4'))
        binding = v.endpoint_binding(snapshot,'mysql','mysql')
        backend = {'registered_consumption_unchanged':mapping,'dsn_matches_registered_mysql':dsn_matches,
            'service_binding':binding,'query_available':None,
            'mapping_source':'sources/robot-shop/ratings/html/src/Kernel.php',
            'mapping_source_sha256':e.sha(rb.ROOT/'sources/robot-shop/ratings/html/src/Kernel.php')}
        if mapping is True and dsn_matches and binding['healthy']:
            try:
                # SELECT only, through the Service and real application account.
                # Existing server Pod is used; no discovery/helper object is created.
                count = rb.kubectl('exec','deployment/mysql','--','env','MYSQL_PWD=iloveit',
                    'mysql','--connect-timeout=5','--protocol=TCP','-h','mysql','-P','3306',
                    '-uratings','--batch','--skip-column-names','-e','SELECT COUNT(*) FROM ratings.ratings').strip()
                backend.update(query_available=count.isdigit(),query_result_is_nonnegative_count=count.isdigit())
            except Exception as error:
                backend.update(query_available=None,collection_error=type(error).__name__)
        snapshot['p2_backend'] = backend
    if candidate == 'P3':
        nodes = json.loads(rb.kubectl('get','nodes','-o','json',namespaced=False))['items']
        snapshot['scheduler_nodes'] = [{'name':n['metadata']['name'],
            'uid':n['metadata']['uid'],'labels':n['metadata'].get('labels',{}),
            'allocatable':n.get('status',{}).get('allocatable',{}),
            'unschedulable':n['spec'].get('unschedulable',False),'taints':n['spec'].get('taints',[]),
            'ready':next((True if c['status']=='True' else False if c['status']=='False' else None
                          for c in n['status'].get('conditions',[]) if c['type']=='Ready'),None),
            'pressure': (any(c['status']!='False' for c in n['status'].get('conditions',[])
                            if c['type'] in ('MemoryPressure','DiskPressure','PIDPressure'))
                         if {'MemoryPressure','DiskPressure','PIDPressure'} <=
                            {c['type'] for c in n['status'].get('conditions',[])} else None)} for n in nodes]
        pods = json.loads(rb.kubectl('get','pods','-A','-o','json',namespaced=False))['items']
        # Only scheduling fields are retained; unrelated ENV/secrets are excluded.
        snapshot['scheduled_pods'] = [{'nodeName':p['spec'].get('nodeName'),
            'phase':p.get('status',{}).get('phase'),
            'containers':[{'resources':c.get('resources',{})} for c in p['spec'].get('containers',[])],
            'initContainers':[{'resources':c.get('resources',{}),'restartPolicy':c.get('restartPolicy')}
                              for c in p['spec'].get('initContainers',[])],
            'overhead':p['spec'].get('overhead',{})} for p in pods]
    snapshot['completed_at'] = time.time()
    snapshot['relevant_revision'] = a.digest({'dependency_revision':snapshot['relevant_revision'],
        'backend':snapshot.get('p2_backend'),'scheduler_nodes':snapshot.get('scheduler_nodes'),
        'scheduled_pods':snapshot.get('scheduled_pods')})
    return snapshot


def compare(candidate, candidate_hash, snapshot, current, now=None, ttl=30, validation=None):
    now = time.time() if now is None else now
    rows = a.compare(candidate,candidate_hash,snapshot,current,now,ttl,validation)
    by_condition = {r['condition']:r for r in rows}
    def supplement(condition, proof, reason, observation=None):
        state = a.SAT if proof is True else (a.CONTRA if proof is False else a.UNKNOWN)
        row = by_condition[condition]
        if row['state'] != a.CONTRA and state != a.SAT:
            row.update(state=state,reason=reason,supplemental_observation=observation)
    if all(r['state']==a.UNKNOWN and 'unattributed' in r['reason'] for r in rows):
        return rows
    action = ('ratings' if candidate=='P2' else 'web')
    dep = v.find(snapshot,'deployments',action) if action else None
    if candidate in ('P2','P3','P4') and dep:
        containers = dep['spec']['template']['spec'].get('containers',[])
        required_name = 'ratings' if candidate=='P2' else 'web'
        supplement('target_objects',any(c.get('name')==required_name for c in containers),
                   'Admitted target-container mapping is absent')
        if len(containers)!=1:
            supplement('referenced_fields',None,'Multi-container action/consumption mapping is unsupported')
    if candidate == 'P2':
        backend = snapshot.get('p2_backend')
        proof = None
        if backend:
            if backend.get('dsn_matches_registered_mysql') is False:
                proof = False
            elif (backend.get('registered_consumption_unchanged') is True
                    and backend.get('service_binding',{}).get('healthy') is True
                    and backend.get('query_available') is True):
                proof = True
        supplement('resource_relationships',proof,'Real current MySQL Service/account and registered ratings consumption require evidence',backend)
    if candidate == 'P3':
        proof, reason = scheduler_mapping(snapshot,dep)
        supplement('referenced_fields',proof,reason)
    # Supplemental Unknowns resolve only through explicit verification of that
    # condition, not merely because a validation record exists.
    matches = (validation and validation.get('outcome')=='Successful repair'
        and validation.get('candidate_sha256')==candidate_hash
        and validation.get('contract_sha256')==a.digest(current)
        and validation.get('fidelity_passed') is True
        and snapshot.get('relevant_revision') is not None
        and validation.get('relevant_revision')==snapshot.get('relevant_revision')
        and isinstance(validation.get('completed_at'),(int,float))
        and 0<=now-validation['completed_at']<=ttl)
    if matches:
        for row in rows:
            if row['state']==a.UNKNOWN and row['condition'] in validation.get('verified_requirements',[]):
                row.update(state=a.SAT,reason='Supplemental uncertainty resolved by explicit fresh matching replay evidence')
    return rows
