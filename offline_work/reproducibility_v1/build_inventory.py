"""Read-only, bounded reproduction index. Never copies evidence or images.

All experiment facts come from frozen registrations and accepted records.
YAML is retained as an identified source. The narrow scalar extractor below
only reads the explicitly pinned repository, platform and local-image fields.
It is not a general YAML loader and rejects missing or ambiguous pins.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parents[2]
METHODS = ('proposed', 'fault_matching', 'whole_environment')
FAMILIES = ('target_objects', 'referenced_fields', 'resource_relationships', 'verification_criteria')
COUNTS = {'main': 200, 'variant-no-repair-control': 40,
          'original-conditions-control': 25, 'unrelated-change-control': 25,
          'original-no-repair-control': 5}

def require(condition, message):
    if not condition:
        raise ValueError(message)

def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()

class Index:
    def __init__(self, root):
        self.root = root.resolve()
        self.files = {}

    def reference(self, name, expected=None):
        path = (self.root / name).resolve()
        require(path.is_relative_to(self.root), 'Outside-project reference')
        require(path.is_file(), 'Missing artifact: ' + str(name))
        key = str(path.relative_to(self.root))
        identity = digest(path)
        require(expected is None or identity == expected, 'Identity mismatch: ' + key)
        require(key not in self.files or self.files[key]['sha256'] == identity,
                'Artifact changed during index construction: ' + key)
        value = {'path': key, 'sha256': identity, 'bytes': path.stat().st_size}
        self.files[key] = value
        return value

    def read(self, name, expected=None):
        ref = self.reference(name, expected)
        path = self.root / ref['path']
        raw = path.read_bytes()
        require(hashlib.sha256(raw).hexdigest() == ref['sha256'], 'Read identity race')
        return json.loads(raw)

    def verify_stability(self):
        for name, value in self.files.items():
            require(digest(self.root / name) == value['sha256'], 'Indexed input changed: ' + name)

def validate_design(candidates, scenarios, rows):
    ids = [c['id'] for c in candidates]
    require(set(ids) == {f'P{i}' for i in range(1, 6)} and len(ids) == 5,
            'Exactly five distinct registered candidates required')
    require(all(c.get('registered') is True for c in candidates), 'Unregistered candidate')
    require(len(scenarios) == 40 and len({s['id'] for s in scenarios}) == 40,
            'Exactly40 distinct main scenarios required')
    matrix = Counter((s['candidate'], s['condition']) for s in scenarios)
    require(matrix == Counter({(c, f): 2 for c in ids for f in FAMILIES}),
            'Two constructions per playbook/condition family required')
    require(all(s.get('expectation_is_not_outcome_label') is True for s in scenarios),
            'Construction expectation must not supply the observed outcome')
    require(len(rows) == 295 and len({r['trial_id'] for r in rows}) == 295,
            '295 distinct accepted records required')
    require(Counter(r['category'] for r in rows) == Counter(COUNTS), 'Main/control counts mismatch')
    main = [r for r in rows if r['category'] == 'main']
    required = {(s['id'], i) for s in scenarios for i in range(1, 6)}
    require({(r['scenario'], r['repeat']) for r in main} == required and len(main) == 200,
            'Every main scenario needs five distinct registered repeats')
    lookup = {s['id']: s for s in scenarios}
    for row in main:
        definition = lookup[row['scenario']]
        require(row['candidate'] == definition['candidate'] and row['condition'] == definition['condition'],
                'Scenario identity mismatch')
        require(row['arm'] == 'repair', 'Main record must be in the repair arm')

def validate_response_queue(queue, scenarios):
    require(len(queue) == 240 and len({q['trial_id'] for q in queue}) == 240,
            '240 distinct registered response slots required')
    require(len({q['name'] for q in queue}) == 240, 'Duplicate response directory')
    require([q['position'] for q in queue] == list(range(1, 241)), 'Response queue position mismatch')
    expected = {(s['id'], r, m) for s in scenarios for r in (1, 2) for m in METHODS}
    require({(q['scenario'], q['repeat'], q['method']) for q in queue} == expected,
            'Response queue must cover each scenario, repeat and method once')
    lookup = {s['id']: s['candidate'] for s in scenarios}
    require(all(q['candidate'] == lookup[q['scenario']] for q in queue), 'Response candidate mismatch')

def validate_qualification(raw, registration_record, candidate):
    require(raw['candidate'] == candidate and raw['repeat'] == registration_record['repeat'],
            'Qualification physical identity mismatch')
    require(raw['protocol_sha256'] == registration_record['protocol_sha256'], 'Qualification protocol binding')
    require(raw['passed'] is True and raw['outcome_passed'] is True
            and raw['execution_complete'] is True and raw['target_recovery'] is True
            and raw['non_target_regression'] is False and raw['post_reset_confirmed'] is True,
            'Qualification requires complete successful physical execution and confirmed reset')

def section(text, name):
    found = list(re.finditer(r'^' + re.escape(name) + r':\s*\n', text, re.M))
    require(len(found) == 1, 'Missing or duplicate pinned section: ' + name)
    tail = text[found[0].end():]
    boundary = re.search(r'^[A-Za-z_][A-Za-z0-9_-]*:', tail, re.M)
    return tail[:boundary.start()] if boundary else tail

def scalar(block, field, spaces):
    values = re.findall(r'^' + ' ' * spaces + re.escape(field) + r': ([^\n]+)$', block, re.M)
    require(len(values) == 1, 'Missing or ambiguous pinned field: ' + field)
    value = values[0].strip()
    return value[1:-1] if len(value) > 1 and value[0] == value[-1] and value[0] in ('"', "'") else value

def named_blocks(text, spaces):
    starts = list(re.finditer(r'^' + ' ' * spaces + r'([a-zA-Z0-9_-]+):\s*\n', text, re.M))
    return {m[1]: text[m.end():starts[i+1].start() if i+1 < len(starts) else len(text)]
            for i, m in enumerate(starts)}

def pinned_metadata(text):
    repos = named_blocks(section(text, 'repositories'), 2)
    require(set(repos) == {'robot-shop', 'kubeplaybook', 'microremed'}, 'Repository pin set mismatch')
    repository_rows = []
    for name, block in repos.items():
        commit = scalar(block, 'commit', 4)
        require(re.fullmatch('[0-9a-f]{40}', commit), 'Invalid repository commit')
        repository_rows.append({'name': name, 'url': scalar(block, 'url', 4),
                                'commit': commit, 'license_recorded_upstream': scalar(block, 'license', 4)})
    platform = section(text, 'platform')
    versions = {name: scalar(platform, name, 2) for name in
                ('kubectl', 'k3d', 'k3s', 'helm', 'node_arch', 'workload_arch', 'chaos_mesh', 'python', 'ansible_core')}
    local = section(text, 'local_images')
    start = re.search(r'^  builds:\s*\n', local, re.M)
    stop = re.search(r'^  identity_reconciliation:', local, re.M)
    require(start is not None and stop is not None and stop.start() > start.end(), 'Local image pin boundaries')
    images = []
    for name, block in named_blocks(local[start.end():stop.start()], 4).items():
        image_id = scalar(block, 'image_id', 6)
        require(re.fullmatch(r'sha256:[0-9a-f]{64}', image_id), 'Invalid Docker configuration image ID')
        images.append({'name': name, 'ref': scalar(block, 'ref', 6), 'docker_config_image_id': image_id,
                       'architecture': scalar(block, 'architecture', 6),
                       'dockerfile_path': scalar(block, 'built_from', 6)})
    require(len(images) == 11, 'Eleven local application images required')
    require(len({i['ref'] for i in images}) == 11, 'Duplicate image reference')
    return repository_rows, versions, images

def build(root, preview):
    idx = Index(root)
    registration = idx.read('candidates/registration.v1.json')
    protocol = idx.read('results/formal-main-v1/protocol.json')
    freeze = idx.read('results/formal-main-analysis-v1/analysis-freeze.json')
    for name, identity in freeze['files'].items():
        idx.reference('results/formal-main-analysis-v1/' + name, identity)
    identities = idx.read('results/formal-main-analysis-v1/input-identities.json')
    rows = idx.read('results/formal-main-analysis-v1/trial-level-data.json')
    idx.reference('results/formal-main-v1/protocol.json', identities['protocol_sha256'])
    acceptance = idx.read('results/formal-main-v1/collection-acceptance.json', identities['collection_acceptance_sha256'])
    require(acceptance['accepted'] is True and len(acceptance['records']) == 295, 'Accepted main collection required')
    validate_design(registration['candidates'], protocol['scenarios'], rows)
    idx.reference('candidates/registration.v1.json', protocol['registration_sha256'])
    idx.reference(protocol['manifest'], protocol['manifest_sha256'])
    idx.reference(protocol['requirements_source'], protocol['requirements_sha256'])
    idx.reference('results/formal-main-v1/invalid-attempts.json', identities['invalid_attempts_sha256'])
    recovery = idx.read('results/formal-main-v1/runtime/current-operational-recovery.json')
    require(recovery['sha256'] == identities['operational_recovery_sha256'], 'Operational recovery pointer mismatch')
    idx.reference(recovery['path'], recovery['sha256'])
    for name, identity in protocol['tool_hashes'].items():
        idx.reference(protocol['source_archive'] + '/' + name, identity)

    candidate_rows = []
    for c in registration['candidates']:
        pin = c['frozen_playbook']; idx.reference(pin['path'], pin['sha256'])
        require(protocol['candidate_hashes'][c['id']] == pin['sha256'], 'Candidate protocol pin mismatch')
        requirements = c['requirements_source']; idx.reference(requirements['path'], requirements['sha256'])
        historical = c['historical_execution_evidence']
        require(historical['successful_trials'] == 5 and len(historical['records']) == 5,
                'Five qualifying successes per candidate required')
        require({r['repeat'] for r in historical['records']} == set(range(1,6)),
                'Five distinct qualifying repeat identities required')
        refs = []
        for record in historical['records']:
            require(record['candidate'] == c['id'], 'Qualification candidate mismatch')
            raw = idx.read(record['record'], record['record_sha256'])
            validate_qualification(raw, record, c['id'])
            idx.reference(record['protocol'], record['protocol_sha256'])
            refs.append({'record': record['record'], 'sha256': record['record_sha256'], 'repeat': record['repeat']})
        idx.reference(historical['qualification_acceptance'], historical['acceptance_sha256'])
        upstream = pin.get('upstream_source')
        if upstream: idx.reference(upstream['path'], upstream['sha256'])
        candidate_rows.append({'id': c['id'], 'family': c['family'], 'origin': c['origin'],
                               'playbook': idx.files[pin['path']], 'upstream_source': upstream,
                               'adaptations': pin.get('adaptations', []),
                               'qualification_records': refs, 'registered_fault': c['registered_fault']})

    raw_refs = []; row_by_id = {r['trial_id']: r for r in rows}
    require(len(identities['accepted_records']) == 295, 'Main raw identity count mismatch')
    require([x['entry'] for x in acceptance['records']] == protocol['order'], 'Main queue acceptance mismatch')
    require([x['trial_id'] for x in identities['accepted_records']] == [x['trial_id'] for x in protocol['order']],
            'Main raw identities must follow the registered queue')
    for identity, accepted, entry in zip(identities['accepted_records'], acceptance['records'], protocol['order']):
        directory = 'results/formal-main-v1/trials/' + identity['trial_id']
        raw = idx.read(directory + '/record.json', identity['record_sha256'])
        audit = idx.read(directory + '/audit.json', identity['audit_sha256'])
        require(audit == accepted['audit'] and audit['accepted'] is True, 'Main saved audit mismatch')
        require(audit['raw_record_sha256'] == identity['record_sha256'] and raw['trial_identity'] == entry,
                'Main raw record binding mismatch')
        row = row_by_id[entry['trial_id']]
        require(raw['repair_outcome'] == row['outcome'] and raw['action_executed'] == row['action_executed'],
                'Analysis outcome differs from raw evidence')
        raw_refs.append({'trial_id': identity['trial_id'], 'scenario': row['scenario'],
                         'category': row['category'], 'repeat': row['repeat'],
                         'record': idx.files[directory + '/record.json'], 'audit': idx.files[directory + '/audit.json']})
    scenario_rows = []
    for s in protocol['scenarios']:
        manifest = idx.reference(s['healthy_manifest'], s['manifest_sha256'])
        feasible = idx.reference(s['source_feasibility_record'], s['source_feasibility_sha256'])
        scenario_rows.append({'id': s['id'], 'candidate': s['candidate'], 'condition': s['condition'],
                              'requirement': s['requirement'], 'change': s['change'],
                              'construction_expectation_is_not_outcome_label': True,
                              'construction': s['construction'], 'manifest': manifest,
                              'feasibility_source': feasible, 'original_control': s['control_id'],
                              'main_replays': [x for x in raw_refs if x['scenario'] == s['id'] and x['category'] == 'main'],
                              'no_repair_controls': [x for x in raw_refs if x['scenario'] == s['id'] and x['category'] == 'variant-no-repair-control']})
    controls = [{**{k:c[k] for k in ('id','candidate','condition')},
                 'manifest':idx.reference(c['healthy_manifest'],c['manifest_sha256'])} for c in protocol['controls']]

    response = idx.read('results/formal-rq3a-responses-v5/protocol.json')
    response_freeze = idx.read('results/formal-rq3a-responses-v5/freeze.json')
    idx.reference('results/formal-rq3a-responses-v5/protocol.json', response_freeze['protocol_sha256'])
    validate_response_queue(response['queue'], protocol['scenarios'])
    require(response['parent_protocol_sha256'] == identities['protocol_sha256'], 'Response parent mismatch')
    preview_path = preview.resolve().relative_to(root.resolve())
    snapshot = idx.read(str(preview_path / 'input-snapshot.json'))
    require(snapshot['planned_responses'] == 240 and snapshot['formal_complete'] is False,
            'Development snapshot scope mismatch')
    for name, identity in snapshot['input_sha256'].items(): idx.reference(name, identity)
    response_slots = [{**{k:q[k] for k in ('position','scenario','candidate','repeat','method','matched_block','trial_id','name')},
                       'protocol_sha256':q['protocol_sha256'],
                       'accepted_in_snapshot':q['position'] <= snapshot['completed_at_snapshot']} for q in response['queue']]

    lock = idx.reference(response['source_lock']['path'], response['source_lock']['sha256'])
    require(lock['sha256'] == protocol['source_lock_sha256'], 'Shared source-lock identity mismatch')
    text = (root / lock['path']).read_text(); repos, versions, images = pinned_metadata(text)
    manifest_text = (root / protocol['manifest']).read_text()
    image_refs = re.findall(r'^\s+image:\s+([^\s]+)\s*$', manifest_text, re.M)
    require(len(image_refs) == 13 and len(set(image_refs)) == 13, 'Thirteen baseline image references required')
    local_refs = {i['ref'] for i in images}
    require(local_refs.issubset(image_refs), 'Local image not used by frozen baseline')
    upstream_refs = [r for r in image_refs if r not in local_refs]
    require(all(re.fullmatch(r'(redis|rabbitmq)@sha256:[0-9a-f]{64}', r) for r in upstream_refs),
            'External baseline images require registry digests')
    for image in images: image['current_dockerfile_identity'] = idx.reference(image['dockerfile_path'])
    reconciliation = idx.read('results/reset-acceptance/image-identity-reconciliation.json')
    by_name = {i['name']:i for i in images}
    for item in reconciliation:
        require(by_name[item['name']]['docker_config_image_id'] == item['observed_id'], 'Reconciled image identity mismatch')
        require(by_name[item['name']]['current_dockerfile_identity']['sha256'] == item['dockerfile_sha256'],
                'Reconciled Dockerfile identity mismatch')
    checksum_ref = idx.reference('seed/mysql-5.7-seed.tar.sha256')
    checksum = (root / checksum_ref['path']).read_text().split()[0]
    require(re.fullmatch('[0-9a-f]{64}', checksum), 'Invalid seed identity')
    require(scalar(section(text, 'baseline_overrides'), 'archive_sha256', 4) == checksum,
            'Seed lock identity mismatch')
    seed = idx.reference('seed/mysql-5.7-seed.tar', checksum)
    idx.reference('seed/seed.meta.yaml')
    doc_verification = idx.read('results/rq1-rq2-document-v2/verification.json')
    idx.reference('D-DN-3.1_EN.docx', doc_verification['docx_sha256'])
    require(doc_verification['chapters123_body_unchanged'] is True, 'Document method identity assurance required')
    idx.reference('results/offline-parallel-v1/rq3b/preparation.json')
    idx.verify_stability()

    inventory = {'at':time.time(), 'status':'VERIFIED_LOCAL_INDEX_NOT_RELEASE_ARCHIVE',
                 'audience':'Author reproduction inventory; includes outcome-linked source locations. Do not add to the sealed reviewer kit.',
                 'copied_raw_evidence_files':0, 'copied_image_or_seed_bytes':0,
                 'registered_candidates':candidate_rows, 'source_repositories':repos,
                 'recorded_platform_versions':versions, 'local_image_identities':images,
                 'external_registry_digest_refs':upstream_refs, 'source_lock':lock,
                 'seed_archive':seed, 'main_accepted_records':raw_refs, 'main_counts':COUNTS,
                 'RQ3a':{'planned_responses':240, 'completed_at_snapshot':snapshot['completed_at_snapshot'],
                         'snapshot_reference':str(preview_path),'planned_queue':response_slots,
                         'extra_attempts_preserved':snapshot['extra_rows'],
                         'duplicate_physical_attempt_exclusions':snapshot['deduplicated_rejections'],
                         'final_collection_complete':False},
                 'RQ3b':{'formal_started':False,'actual_trials':0,'offline_controller_prepared':True,
                         'remaining':'Backend, live adapters, pilot budgets and sequence design require prospective registration.'},
                 'chronology_notes':['Candidate registration describes an earlier qualification stage. Its zero main count is historical.',
                                     'The frozen main protocol contains older response-planning counts. The current approved response protocol registers240.',
                                     'The current document contains later results edits. Its method body is unchanged according to document verification.',
                                     'Historical construction expectations do not provide outcome labels. Actual main records provide outcomes.'],
                 'release_limitations':['Docker configuration image IDs are distinct from registry manifest digests.',
                                        'Exact locally built images must be retained or exported before artifact release.',
                                        'Byte-identical rebuilds have not been demonstrated. Some dependencies float.',
                                        'Complete build-to-image provenance must be confirmed from archived build records where needed.',
                                        'The MySQL seed archive is indexed in place. PVCs and Docker images were not accessed or changed.']}
    appendix = {'status':'PREPARED_APPENDIX_MATERIALS_NOT_NEW_PAPER_RESULTS',
                'simple_english_paragraphs':['The study uses five fixed playbooks. Three are adapted from a repository. Two are written manually.',
                                            'Each playbook has eight main scenarios. The design varies four condition families. Each family has two constructions.',
                                            'Each main scenario has five repair replays. The accepted main collection contains200 repair replays and95 control runs.',
                                            'The response experiment registers240 responses. It combines40 scenarios, three methods and two repeats.',
                                            'The local index records file paths and SHA256 values. It does not copy the raw traces or container images.',
                                            'The response collection is incomplete at this snapshot. Sequence experiments have not started.'],
                'candidate_table':[{k:c[k] for k in ('id','family','origin')} for c in candidate_rows],
                'scenario_table':[{k:s[k] for k in ('id','candidate','condition','change')} for s in scenario_rows],
                'version_table':versions,
                'artifact_counts':{'playbooks':5,'main_scenarios':40,'qualification_successes':25,
                                   'accepted_main_repairs':200,'accepted_controls':95,'planned_responses':240},
                'scope_note':'Use these facts for a reproduction appendix. Do not treat offline controller tests or incomplete previews as empirical sequence results.'}
    return inventory, {'scenarios':scenario_rows,'controls':controls}, appendix, idx.files

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', type=Path, default=ROOT/'results/offline-parallel-v1/rq3a/preview-002')
    parser.add_argument('--output', type=Path, default=ROOT/'results/offline-parallel-v1/reproducibility')
    args = parser.parse_args()
    output = args.output.resolve()
    require(output.is_relative_to(ROOT/'results/offline-parallel-v1'), 'Output outside new offline workspace')
    require(not output.exists(), 'Preserve earlier index outputs')
    inventory, scenarios, appendix, files = build(ROOT, args.preview)
    output.mkdir(parents=True)
    for name, value in [('inventory.json',inventory),('scenario-index.json',scenarios),
                        ('appendix-materials.json',appendix),('file-identities.json',files)]:
        (output/name).write_text(json.dumps(value,indent=2)+'\n')
    receipt = {'at':time.time(),'accepted':True,'indexed_inputs':len(files),'playbooks':5,'main_scenarios':40,
               'qualification_records':25,'main_accepted_records':295,'planned_response_slots':240,
               'completed_responses_at_snapshot':inventory['RQ3a']['completed_at_snapshot'],
               'input_identities_verified':True,'input_stability_verified':True,'no_live_cluster_commands':True,
               'raw_evidence_copied':False,'image_or_volume_exported_or_deleted':False,
               'outputs_sha256':{p.name:digest(p) for p in output.iterdir() if p.is_file()}}
    (output/'inventory-acceptance.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps({k:receipt[k] for k in ('accepted','indexed_inputs','playbooks','main_scenarios','main_accepted_records','planned_response_slots')}))

if __name__ == '__main__':
    main()
