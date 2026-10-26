"""Repeatable API acceptance check. Archives new evidence; never executes repairs."""
import argparse
import hashlib
import json
from pathlib import Path
from .client import BASE_URL, MODEL, request, completed_json, checked_candidate, boundary_passed, save
from offline_work.rq3b_planner_v1.planner import permitted_envelope

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'offline_work/rq3b_planner_v1'
SCHEMA = json.loads((SOURCE / 'output-schema.json').read_text())
CAP_PROMPT = 'Output all consecutive integers 1 through 500 as a JSON array. No ellipsis, no code and no abbreviation.'


def responses_payload(model, text, cap, *, effort='none', schema=None):
    payload = {'model': model, 'store': False, 'tools': [], 'tool_choice': 'none',
               'input': text, 'max_output_tokens': cap, 'reasoning': {'effort': effort}}
    if schema is not None:
        payload['text'] = {'format': {'type': 'json_schema', 'name': 'experiment_output',
                                     'strict': True, 'schema': schema}}
    return payload


def run(output, *, model=MODEL, base_url=BASE_URL):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    summary = {'kind': 'local_api_planning_integration', 'model': model, 'base_url': base_url,
        'interface': 'Responses API', 'structured_smoke_passed': False,
        'token_boundary_passed': False, 'chat_token_boundary_passed': False,
        'saved_observation_planning_passed': False, 'transport_budget_checks_passed': False,
        'formal_protocol_accepted': False, 'formal_trials': 0, 'live_recovery_trials': 0,
        'source_mode': 'archived development observations',
        'gateway_internal_retries_verified': False, 'immutable_model_snapshot_verified': False,
        'total_wall_deadline_enforced': False,
        'prospective_planner_settings': {'reasoning_effort': 'high', 'requested_max_output_tokens': 2048,
                                        'socket_timeout_seconds': 180, 'client_retries': 0, 'tool_call_allowance': 0}}
    def call(name, payload, *, endpoint='responses', timeout=90):
        return request(output / name, payload, endpoint=endpoint, timeout=timeout, base_url=base_url)
    def stage(name, passed):
        print(json.dumps({'stage': name, 'passed': passed}), flush=True)
    try:
        models, receipt = call('01-models', {}, endpoint='models')
        entries = models.get('data') if isinstance(models, dict) else None
        ids = [x.get('id') for x in entries if isinstance(x, dict)] if isinstance(entries, list) else []
        summary['discovered_model_ids'] = ids
        if not receipt['http_completed'] or model not in ids:
            summary['stop_reason'] = 'Requested model unavailable at the selected endpoint'
            return summary
        schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                  'required': ['ok'], 'additionalProperties': False}
        smoke, _ = call('02-structured-smoke', responses_payload(model, 'Return the JSON object with ok true.', 256, schema=schema))
        try:
            value = completed_json(smoke, model, 256)
            summary['structured_smoke_passed'] = isinstance(value, dict) and set(value) == {'ok'} and value['ok'] is True
        except (ValueError, TypeError):
            pass
        stage('structured_smoke', summary['structured_smoke_passed'])
        for name, endpoint in [('03-responses-boundary', 'responses'), ('04-chat-boundary', 'chat/completions')]:
            payload = responses_payload(model, CAP_PROMPT, 16) if endpoint == 'responses' else {
                'model': model, 'messages': [{'role': 'user', 'content': CAP_PROMPT}],
                'reasoning_effort': 'none', 'max_completion_tokens': 16, 'stream': False}
            response, _ = call(name, payload, endpoint=endpoint)
            key = 'token_boundary_passed' if endpoint == 'responses' else 'chat_token_boundary_passed'
            summary[key] = boundary_passed(response, model, 16, endpoint=endpoint)
            stage(endpoint + '_boundary', summary[key])
        summary['transport_budget_checks_passed'] = summary['structured_smoke_passed'] and summary['token_boundary_passed']
        if not summary['transport_budget_checks_passed']:
            summary['stop_reason'] = 'Responses output controls failed; no generated plan requested'
            return summary
        source = ROOT / 'results/reuse-flow-development-v8/07-revalidate-P5/primary/observations.json'
        setup = source.with_name('incident-setup.json')
        envelope = permitted_envelope(source, setup)
        payload = responses_payload(model, json.dumps(envelope, sort_keys=True), 2048, effort='high', schema=SCHEMA)
        payload['instructions'] = (SOURCE / 'instructions.txt').read_text()
        response, _ = call('05-planning', payload, timeout=180)
        try:
            result = checked_candidate(response, model, 2048)
            save(output / '05-planning' / 'checked-output.json', result)
            (output / '05-planning' / 'generated-plan.yaml').write_text(result['plan_yaml'])
            summary['saved_observation_planning_passed'] = True
        except (ValueError, TypeError) as error:
            save(output / '05-planning' / 'rejection.json', {'error_type': type(error).__name__, 'reason': str(error)})
        save(output / 'source-identities.json', {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (source, setup, SOURCE / 'instructions.txt', SOURCE / 'output-schema.json', Path(__file__), Path(__file__).with_name('client.py'))})
        stage('saved_observation_planning', summary['saved_observation_planning_passed'])
        return summary
    except Exception as error:
        summary['stop_reason'] = type(error).__name__
        return summary
    finally:
        save(output / 'acceptance.json', summary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='New directory under Project/results; existing evidence is never overwritten')
    parser.add_argument('--model', default=MODEL)
    parser.add_argument('--base-url', default=BASE_URL)
    args = parser.parse_args()
    output = (ROOT / args.output).resolve()
    if not output.is_relative_to(ROOT / 'results') or output == ROOT / 'results':
        parser.error('--output must name a new directory under Project/results')
    if output.exists():
        parser.error('Output already exists; choose a fresh directory')
    summary = run(output, model=args.model, base_url=args.base_url)
    print(json.dumps(summary), flush=True)
    if not (summary['transport_budget_checks_passed'] and summary['saved_observation_planning_passed']):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
