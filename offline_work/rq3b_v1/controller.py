"""RQ3b orchestration with injected adapters; offline development only.

No container, shell, network or model client is imported here. A future live
adapter must provide bounded calls and independently auditable physical traces.
Fixture outcomes are never formal experiments or estimates of recovery time.
"""
from dataclasses import dataclass
import copy
import hashlib
import json
import math

PROCESSES = ('proposed', 'replanning', 'full_revalidation')
OUTCOMES = ('Successful repair', 'Confirmed failure', 'Indeterminate')

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

@dataclass(frozen=True)
class Plan:
    name: str
    content: str
    origin: str

    @property
    def sha256(self):
        return hashlib.sha256(self.content.encode()).hexdigest()

@dataclass(frozen=True)
class Limits:
    # Numerical values are fixture/pilot parameters, not selected formal budgets.
    response_cap: float
    evidence_ttl: float
    refreshes: int
    replays: int
    generated_attempts: int
    assessments: int

    def __post_init__(self):
        for value in (self.response_cap, self.evidence_ttl):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError('A finite positive time budget is required')
        for value in (self.refreshes, self.replays, self.generated_attempts, self.assessments):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError('Count budgets must be nonnegative integers')
        if self.generated_attempts < 1 or self.assessments < 1:
            raise ValueError('A generated attempt and assessment must be available')

class CapReached(Exception):
    pass

class UnsafeContinuation(Exception):
    pass

class Engine:
    """One process per sequence. State and permitted history never cross processes.

    Adapter contract:
      restore(start_identity) -> {'passed', 'start_identity', 'fingerprint'}
      present(incident) -> {'presented_at'}; predefined changes/faults, no cleanup
      collect(remaining) -> {'at', 'revision', 'contract', 'evidence_id'}
      assess(plan, observation, history, remaining) -> decision
      make_replica(plan, observation, remaining) -> fidelity/source/primary tokens
      isolated_replay(plan, replica, remaining) -> outcome, actual outcome_fixed_at,
          executed, evidence_id and cleanup_confirmed (cleanup costs included)
      audit_certificate(certificate, remaining) -> bool
      plan(observation, remaining) -> a newly generated Plan
      static_check(plan, remaining), safety_gate(plan, observation, remaining)
      execute(plan, permission, remaining) -> {'executed': bool, 'complete': bool, ...}
      verify(plan, execution, remaining) -> outcome plus immutable evidence_id
      reconcile(plan, execution, remaining) -> confirmed safe fallback boundary
      snapshot() -> state for archiving, never reset within a sequence

    All adapters see the same incident deadline. Executor exceptions/overruns
    after launch are retained as uncertain executions. An unconfirmed reconcile
    blocks further actions. A live adapter needs cancel/late-evidence handling.
    """
    def __init__(self, port, clock, limits, historical, initial_history=(), *, mode='offline_fixture'):
        if mode != 'offline_fixture':
            raise ValueError('Live execution is not enabled: prospective registration and adapters are pending')
        if historical.origin != 'historical':
            raise ValueError('The unchanged registered historical candidate is required')
        self.port, self.clock, self.limits, self.historical = port, clock, limits, historical
        self.initial_history = copy.deepcopy(tuple(initial_history))

    def run_sequence(self, process, sequence_id, start_identity, incidents):
        if process not in PROCESSES:
            raise ValueError('Unknown response process')
        reset = self.port.restore(start_identity)
        if reset.get('passed') is not True or reset.get('start_identity') != start_identity:
            raise ValueError('Equivalent starting snapshot not confirmed')
        self.history = copy.deepcopy(list(self.initial_history))
        records = []
        for incident in incidents:
            # Healthy intervals are excluded from recovery time but age evidence.
            self.port.healthy_interval(incident.get('healthy_interval_seconds', 0))
            presented = self.port.present(copy.deepcopy(incident))
            start = presented['presented_at']
            if start > self.clock() or not math.isfinite(start):
                raise ValueError('Invalid presentation timestamp')
            self.start, self.deadline = start, start + self.limits.response_cap
            self.record = {'incident_id': incident['id'], 'process': process, 'presented_at': start,
                           'events': [], 'live_attempts': [], 'isolated_attempts': [],
                           'recovered': False, 'status': 'Unresolved', 'censored': True,
                           'refreshes': 0, 'replays': 0, 'generated_attempts': 0}
            try:
                if process == 'replanning':
                    self.generated_fallback()
                elif process == 'full_revalidation':
                    observation = self.observe()
                    certificate = self.revalidate(observation)
                    if certificate and self.launch(self.historical, observation, certificate, 'after revalidation'):
                        pass
                    else:
                        self.generated_fallback()
                else:
                    self.proposed()
            except CapReached:
                self.record['stop_reason'] = 'Common response cap reached'
            except UnsafeContinuation as error:
                self.record['stop_reason'] = str(error)
            except Exception as error:
                self.record['stop_reason'] = 'Adapter unavailable: ' + str(error)
            if self.record['recovered']:
                self.record['capped_recovery_seconds'] = self.record['confirmed_at'] - start
            else:
                # No zero-time decline, free human repair or invented recovery.
                self.record['capped_recovery_seconds'] = self.limits.response_cap
            self.record['observed_handling_seconds'] = max(0, self.clock() - start)
            self.record['state_after_sha256'] = digest(self.port.snapshot())
            self.record['history_after_sha256'] = digest(self.history)
            self.record['incidents_with_invalid_live_execution'] = int(any(
                x['outcome'] == 'Confirmed failure' for x in self.record['live_attempts']))
            records.append(copy.deepcopy(self.record))
            if not self.record['recovered']:
                # Never overwrite an unresolved fault with a later incident.
                # A different continuation policy needs prospective registration.
                break
        return {'type': 'rq3b_offline_development_sequence', 'formal_trials': 0, 'process': process,
                'sequence_id': sequence_id, 'start_identity': start_identity,
                'starting_fingerprint': reset['fingerprint'], 'records': records,
                'history': copy.deepcopy(self.history),
                'capped_cumulative_seconds': sum(x['capped_recovery_seconds'] for x in records),
                'recovered_incidents': sum(x['recovered'] for x in records),
                'unresolved_incidents': sum(not x['recovered'] for x in records),
                'planned_incidents': len(incidents), 'unpresented_incidents': len(incidents) - len(records),
                'continuation_after_unresolved': 'blocked pending registered policy'}

    def remaining(self):
        return max(0, self.deadline - self.clock())

    def call(self, name, function, *args):
        if self.remaining() <= 0:
            raise CapReached()
        started = self.clock()
        event = {'stage': name, 'started_at': started, 'remaining_budget': self.remaining()}
        self.record['events'].append(event)
        try:
            result = function(*args, self.remaining())
        except Exception as error:
            event.update({'completed_at': self.clock(), 'error': str(error)})
            raise
        event['completed_at'] = self.clock()
        if self.clock() > self.deadline:
            event['late_result_not_admitted'] = True
            raise CapReached()
        return result

    def observe(self):
        observation = self.call('collect', self.port.collect)
        if not isinstance(observation, dict):
            raise ValueError('A saved permitted observation is required')
        return observation

    def fresh(self, observation):
        at = observation.get('at')
        return (isinstance(at, (int, float)) and not isinstance(at, bool) and math.isfinite(at)
                and 0 <= self.clock() - at <= self.limits.evidence_ttl
                and all(observation.get(k) for k in ('revision', 'contract', 'evidence_id')))

    def proposed(self):
        observation = self.observe()
        for _ in range(self.limits.assessments):
            if not self.fresh(observation):
                if self.record['refreshes'] >= self.limits.refreshes:
                    break
                self.record['refreshes'] += 1
                observation = self.observe()
                continue
            decision = self.call('assess', self.port.assess, self.historical, copy.deepcopy(observation),
                                 copy.deepcopy(tuple(self.history)))
            if decision == 'Direct reuse':
                if self.launch(self.historical, observation, None, 'direct reuse'):
                    return
                break
            if decision == 'Stop reuse':
                break
            if decision != 'Revalidate':
                raise ValueError('An explicit three-way decision is required')
            certificate = self.revalidate(observation)
            if certificate and self.launch(self.historical, observation, certificate, 'after revalidation'):
                return
            break
        self.generated_fallback()

    def revalidate(self, observation):
        if not self.fresh(observation):
            return None
        while self.record['replays'] < self.limits.replays:
            self.record['replays'] += 1
            replica = self.call('replica creation synchronization and fault setup', self.port.make_replica,
                                self.historical, copy.deepcopy(observation))
            if replica.get('primary_before') != replica.get('primary_after'):
                raise UnsafeContinuation('Isolated replica changed the incident environment')
            if not (replica.get('fidelity_passed') is True and replica.get('isolated') is True
                    and replica.get('source_revision') == observation['revision']
                    and replica.get('source_contract') == observation['contract']
                    and replica.get('primary_before') == replica.get('primary_after')):
                return None
            attempt = {'candidate_sha256': self.historical.sha256, 'outcome': 'Indeterminate', 'executed': None}
            self.record['isolated_attempts'].append(attempt)
            try:
                checked = self.call('isolated replay and current verification', self.port.isolated_replay,
                                    self.historical, replica)
            except Exception:
                # Isolated action completion is not inferred from a missing result.
                attempt['execution_status'] = 'adapter result unavailable'
                raise
            if checked.get('outcome') not in OUTCOMES or not checked.get('evidence_id'):
                raise ValueError('Saved isolated outcome evidence required')
            if not isinstance(checked.get('executed'), bool):
                raise ValueError('Actual isolated execution must be explicit')
            attempt.update(checked)
            if checked.get('cleanup_confirmed') is not True:
                raise UnsafeContinuation('Isolated action has no confirmed cleanup boundary')
            fixed_at = checked.get('outcome_fixed_at')
            if (not isinstance(fixed_at, (int, float)) or isinstance(fixed_at, bool)
                    or not math.isfinite(fixed_at) or not self.start <= fixed_at <= self.clock()):
                raise ValueError('Actual isolated outcome time is required before cleanup')
            if checked['outcome'] == 'Confirmed failure':
                return None
            if checked['outcome'] == 'Indeterminate':
                continue
            certificate = {**checked, 'candidate_sha256': self.historical.sha256,
                           'revision': observation['revision'], 'contract': observation['contract'],
                           'completed_at': fixed_at, 'fidelity_passed': True}
            if self.call('certificate provenance audit', self.port.audit_certificate, certificate) is True:
                return certificate
            return None
        return None

    def launch(self, plan, observation, certificate, route):
        if plan.origin == 'historical' and plan.sha256 != self.historical.sha256:
            raise ValueError('An edited historical plan is replanning')
        if self.call('static checks', self.port.static_check, plan) is not True:
            return False
        current = self.observe()
        if not self.fresh(current):
            return False
        if (not self.fresh(observation)
                or current['revision'] != observation['revision'] or current['contract'] != observation['contract']):
            return False
        if self.call('common safety gate', self.port.safety_gate, plan, current) is not True:
            return False
        launch_observation = self.observe()
        if (launch_observation.get('revision') != current['revision']
                or launch_observation.get('contract') != current['contract']):
            return False
        current = launch_observation
        # Gate latency may consume the certificate's lifetime or incident budget.
        if not self.fresh(current):
            return False
        if certificate:
            if (certificate.get('candidate_sha256') != plan.sha256
                    or certificate.get('revision') != current['revision']
                    or certificate.get('contract') != current['contract']
                    or not 0 <= self.clock() - certificate['completed_at'] <= self.limits.evidence_ttl):
                return False
        attempt = {'candidate_sha256': plan.sha256, 'plan_origin': plan.origin, 'route': route,
                   'outcome': 'Indeterminate', 'executed': None}
        self.record['live_attempts'].append(attempt)
        permission = {'candidate_sha256': plan.sha256, 'revision': current['revision'],
                      'contract': current['contract'],
                      'expires_at': min(self.deadline, current['at'] + self.limits.evidence_ttl,
                                        certificate['completed_at'] + self.limits.evidence_ttl if certificate else self.deadline)}
        try:
            execution = self.call('live execution', self.port.execute, plan, permission)
            if not isinstance(execution.get('executed'), bool):
                raise ValueError('Actual execution must be explicit')
            attempt['executed'] = execution['executed']
            if not execution['executed']:
                attempt['outcome'] = 'Not executed'
                return False
            verified = self.call('current recovery and non-target verification', self.port.verify, plan, execution)
            if verified.get('outcome') not in OUTCOMES or not verified.get('evidence_id'):
                raise ValueError('Actual current verification evidence required')
            if verified['outcome'] == 'Successful repair' and not (
                execution.get('complete') is True and verified.get('target_recovered') is True
                and verified.get('non_target_passed') is True and verified.get('window_complete') is True):
                raise ValueError('A success label cannot bypass complete recovery evidence')
            attempt.update(verified)
        except CapReached:
            attempt['outcome'] = 'Indeterminate'
            raise
        except Exception as error:
            attempt.update({'outcome': 'Indeterminate', 'error': str(error)})
            execution = {'executed': attempt.get('executed'), 'complete': False}
        if attempt['outcome'] == 'Successful repair':
            self.record.update({'recovered': True, 'status': 'Recovered', 'censored': False,
                                'confirmed_at': self.clock()})
            self.history.append({'candidate_sha256': plan.sha256, 'plan_origin': plan.origin,
                                 'evidence_id': attempt['evidence_id'], 'confirmed_at': self.clock(),
                                 'revision': current['revision'], 'contract': current['contract']})
            return True
        if self.call('rollback and safe fallback confirmation', self.port.reconcile, plan, execution) is not True:
            raise UnsafeContinuation('Failed or uncertain action has no confirmed safe fallback boundary')
        return False

    def generated_fallback(self):
        for _ in range(self.limits.generated_attempts):
            observation = self.observe()
            if not self.fresh(observation):
                continue
            self.record['generated_attempts'] += 1
            plan = self.call('fixed replanning procedure', self.port.plan, copy.deepcopy(observation))
            if not isinstance(plan, Plan) or plan.origin != 'generated':
                raise ValueError('Generated or edited plans must count as replanning')
            if self.launch(plan, observation, None, 'generated repair'):
                return
