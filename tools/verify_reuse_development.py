"""Archive bounded synthetic acceptance, scope and traceability for RQ2.

These records verify implementation rules; they do not establish live replica
fidelity, method superiority, or any main-trial outcome.
"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path, required=True)
    out = ap.parse_args().output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    req_path = ROOT/'candidates/requirements.S3-feasible.v1.yaml'
    req = yaml.safe_load(req_path.read_text())
    observations = {
        'target_objects': 'Deployment/Service existence; admitted P1 deployment-list discovery; manually attributed current role',
        'referenced_fields': 'replicas; paused; resources and retained scheduling fields; registered web startup-consumption mapping; predicted selector',
        'resource_relationships': 'declared load HOST; client-facing Service/template labels; current owner graph; dependency revision',
        'verification_criteria': 'manual current criterion contract; explicitly supported endpoint equivalence; exact fresh isolated replay when available'}
    tests = {
        'target_objects': ['test_empty_locator_is_contradiction_missing_observation_is_unknown',
                           'test_logical_replacement_allowed_idle_target_rejected'],
        'referenced_fields': ['test_contradiction_precedes_unknown', 'test_budget_exhaustion_stops'],
        'resource_relationships': ['test_role_annotation_alone_does_not_prove_current_binding',
                                  'test_relevant_revisions_exclude_unrelated_objects_but_detect_replacement'],
        'verification_criteria': ['test_stricter_verifier_alone_needs_revalidation_not_failure',
                                  'test_revalidation_needs_fidelity_freshness_and_exact_contract']}
    mappings = []
    for candidate, conditions in req['requirements'].items():
        for condition, requirement in conditions.items():
            mappings.append({'id':candidate+':'+condition, 'candidate':candidate,
                'condition':condition, 'registered_requirement':requirement,
                'requirement_source':str(req_path.relative_to(ROOT)),
                'requirement_source_sha256':sha(req_path), 'candidate_sha256':req['candidate_hashes'][candidate],
                'collector':observations[condition], 'rule':'supported_reuse.compare with reuse_assessor.decision',
                'design_verification_tests':tests[condition],
                'scope':'admitted five frozen candidates; automatic observations plus attributed manual semantic mappings'})
    commands = []
    successful = True
    for label, pattern in [('owner-verifier', 'test_main_verifier.py'),
                           ('assessor-and-workflow', 'test_reuse*.py'),
                           ('supplemental-semantics', 'test_supported_reuse.py'),
                           ('comparison-policies', 'test_comparison_policies.py')]:
        argv = [sys.executable, '-m', 'unittest', 'discover', '-s', 'tools', '-p', pattern, '-v']
        start = time.time()
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=120)
        log = out/(label+'.log')
        log.write_text(result.stdout+result.stderr)
        commands.append({'label':label,'argv':argv,'started_at':start,'completed_at':time.time(),
            'exit_code':result.returncode,'log':str(log.relative_to(ROOT)),'log_sha256':sha(log)})
        successful = successful and result.returncode == 0
    sources = ['reuse_assessor.py','reuse_evidence.py','reuse_workflow.py','comparison_policies.py',
               'main_verifier.py','supported_reuse.py','test_supported_reuse.py',
               'verify_reuse_development.py','test_main_verifier.py',
               'test_reuse_assessor.py','test_reuse_evidence.py','test_reuse_workflow.py','test_comparison_policies.py']
    archive = out/'tool-snapshot'; archive.mkdir()
    for name in sources:
        (archive/name).write_bytes((ROOT/'tools'/name).read_bytes())
    record = {'stage':'RQ2 implementation development', 'written_at':time.time(), 'main_trials':0,
        'synthetic_checks_passed':successful,'commands':commands,'traceability_rows':mappings,
        'source_hashes':{n:sha(ROOT/'tools'/n) for n in sources},
        'manual_annotation_scope':'Current business role, criterion meaning, endpoint equivalence and '
            'registered web ENV/entrypoint consumption are explicit human mappings. They are not '
            'automatically inferred from playbooks or from scenario IDs/outcomes.',
        'automatic_observation_scope':'Named namespaced Deployment, StatefulSet, ReplicaSet, Pod, Service, '
            'EndpointSlice, ConfigMap, HPA; admitted P1 get-deployments discovery; P3 node '
            'labels/allocatable/Ready/pressure and all-namespace Pod scheduling fields only; '
            'P2 SELECT through the MySQL Service using the existing server Pod and application '
            'account. No helper creation or mutating discovery is permitted.',
        'prospective_development_budgets':{'observation_ttl':30,'refresh_budget':1,'replay_budget':1,
                                         'total_budget_seconds':1800,'max_assessments':6},
        'same_common_gate':'Candidate SHA, isolated contexts/namespace, declared action allowlist, '
            'P2 bounded scale, 120s action timeout, confirmed rollback; execution permission and '
            'policy decision remain distinct.',
        'not_yet_verified':['live readonly assessor/prototype acceptance', 'real fidelity-checked isolated replay adapter',
            'live P2 backend/account consumption and P3 conservative placement/request-capacity mapping',
            'cases outside the explicitly supported single-container scheduler/consumption subset',
            'labelled fault-matching development positives and negatives',
            'comparison/RQ3b settings and replanning backend freeze'],
        'limits':'Passing synthetic cases is implementation verification only. The current collector '
            'does not prove every registered requirement in every changed configuration. Unmapped '
            'requirements must remain Unknown before method freeze; no main trial may start.'}
    path = out/'acceptance.json'
    path.write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({'synthetic_checks_passed':successful,'traceability_rows':len(mappings),
                      'live_method_accepted':False,'main_trials':0}))
    if not successful:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
