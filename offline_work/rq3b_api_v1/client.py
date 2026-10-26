"""Bounded planning-only Responses adapter. No automatic HTTP retries or execution."""
import hashlib
import json
import math
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

BASE_URL = 'http://127.0.0.1:8317/v1'
MODEL = 'gpt-6-sol'
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_FINAL_BYTES = 32768

def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')

def output_text(response):
    if not isinstance(response, dict) or not isinstance(response.get('output'), list):
        raise ValueError('Malformed Responses output')
    parts = []
    for item in response.get('output', []):
        if not isinstance(item, dict):
            raise ValueError('Malformed output item')
        if item.get('type') not in ('message', 'reasoning'):
            raise ValueError('Unexpected tool or output item')
        if item.get('type') == 'message':
            if item.get('role') != 'assistant':
                raise ValueError('Unexpected output role')
            content_parts = item.get('content')
            if not isinstance(content_parts, list):
                raise ValueError('Malformed message content')
            for content in content_parts:
                if not isinstance(content, dict) or content.get('type') != 'output_text':
                    raise ValueError('Refusal or unsupported output content')
                if not isinstance(content.get('text'), str):
                    raise ValueError('Missing output text')
                parts.append(content['text'])
    text = ''.join(parts)
    if not text or len(text.encode()) > MAX_FINAL_BYTES:
        raise ValueError('Empty or oversized final text')
    return text

def completed_json(response, expected_model, cap):
    """Shared completion, identity and token checks for smoke tests and plans."""
    if not isinstance(response, dict):
        raise ValueError('Response must be a JSON object')
    if response.get('status') != 'completed':
        raise ValueError('Incomplete or unsuccessful model output')
    if response.get('model') != expected_model:
        raise ValueError('Response model differs from the requested model')
    accounting = response.get('usage')
    usage = accounting.get('output_tokens') if isinstance(accounting, dict) else None
    if isinstance(usage, bool) or not isinstance(usage, int) or usage < 0 or (cap is not None and usage > cap):
        raise ValueError('Output token accounting missing or outside the budget')
    return json.loads(output_text(response))

def checked_candidate(response, expected_model, cap):
    value = completed_json(response, expected_model, cap)
    if not isinstance(value, dict):
        raise ValueError('Plan must be a JSON object')
    if set(value) != {'status', 'plan_yaml', 'rationale'}:
        raise ValueError('Unexpected planning schema')
    if value['status'] not in ('candidate', 'unresolved'):
        raise ValueError('Unexpected planning status')
    if not all(isinstance(value[k], str) for k in value):
        raise ValueError('All planning fields must be strings')
    if not value['rationale'].strip():
        raise ValueError('Missing planning rationale')
    if (value['status'] == 'candidate') != bool(value['plan_yaml'].strip()):
        raise ValueError('Plan/status mismatch')
    return value

def boundary_passed(response, expected_model, cap, *, endpoint='responses'):
    """A short completed response does not prove a generation cap is enforced."""
    if not isinstance(response, dict) or response.get('model') != expected_model:
        return False
    accounting = response.get('usage')
    if not isinstance(accounting, dict):
        return False
    if endpoint == 'responses':
        usage = accounting.get('output_tokens')
        details = response.get('incomplete_details')
        truncated = response.get('status') == 'incomplete' and isinstance(details, dict) and details.get('reason') == 'max_output_tokens'
    elif endpoint == 'chat/completions':
        usage = accounting.get('completion_tokens')
        choices = response.get('choices')
        truncated = isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) and choices[0].get('finish_reason') == 'length'
    else:
        return False
    return bool(truncated and isinstance(usage, int) and not isinstance(usage, bool) and 0 <= usage <= cap)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None

def request(directory, payload, *, timeout=90, endpoint='responses', base_url=BASE_URL):
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Finite positive request timeout required')
    if endpoint not in ('responses', 'models', 'chat/completions'):
        raise ValueError('Only planning and model discovery are permitted')
    key = os.environ.get('DDN_LLM_API_KEY') or os.environ.get('OPENAI_API_KEY')
    if not key:
        raise ValueError('Configure the API key locally')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    save(directory / 'request.json', payload)
    receipt = {'started_at': time.time(), 'base_url': base_url, 'endpoint': endpoint,
               'socket_timeout_seconds': timeout, 'total_wall_deadline_enforced': False,
               'client_retries': 0, 'http_completed': False,
               'formal_trials': 0, 'plan_executed': False}
    start = time.monotonic()
    encoded = None if endpoint == 'models' else json.dumps(payload).encode()
    req = urllib.request.Request(base_url.rstrip('/') + '/' + endpoint, data=encoded,
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    # Prohibit unexpected redirects, which could disclose the bearer credential.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    response = None
    try:
        with opener.open(req, timeout=timeout) as handle:
            raw = handle.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError('Response exceeds the transport admission bound')
            receipt['http_status'] = handle.status
            receipt['request_id'] = handle.headers.get('x-request-id')
        (directory / 'response.json').write_bytes(raw)
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError('Transport response must be a JSON object')
        response = decoded
        receipt['http_completed'] = True
        receipt['reported_model'] = response.get('model')
        receipt['response_status'] = response.get('status')
        receipt['reported_max_output_tokens'] = response.get('max_output_tokens')
        receipt['incomplete_details'] = response.get('incomplete_details')
        receipt['usage'] = response.get('usage')
    except urllib.error.HTTPError as error:
        receipt['http_status'] = error.code
        # The error body may contain request or credential echoes; keep only its hash.
        raw = error.read(MAX_RESPONSE_BYTES + 1)
        receipt['error_body_sha256'] = hashlib.sha256(raw).hexdigest()
        receipt['error_type'] = 'HTTPError'
    except Exception as error:
        receipt['error_type'] = type(error).__name__
    receipt['elapsed_seconds'] = time.monotonic() - start
    receipt['completed_at'] = time.time()
    receipt['evidence_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in directory.iterdir() if p.is_file()}
    save(directory / 'receipt.json', receipt)
    return response, receipt
