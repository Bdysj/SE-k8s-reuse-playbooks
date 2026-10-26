"""Executable RQ3a baselines; fitting requires actual labelled pilot outcomes.

Model fitting, vocabulary and threshold are separate from policy assessment.
The assessment interface cannot access an injected fault label or replay result.
"""
import collections
import math
import re


def incident_text(observation):
    permitted = {k:observation.get(k, []) for k in ('workload','symptoms','error_codes')}
    values = []
    for value in permitted.values():
        values.extend(value if isinstance(value,list) else [value])
    text = ' '.join(str(x) for x in values).lower()
    text = re.sub(r'\b\d{4}-\d\d-\d\d[t ][0-9:.]+(?:z|[+-]\d\d:\d\d)?', ' ', text)
    text = re.sub(r'\b[0-9a-f]{8}-[0-9a-f-]{27,}\b', ' ', text)
    text = re.sub(r'-[a-z0-9]{8,10}-[a-z0-9]{5}\b', '', text)
    return re.findall(r'[a-z0-9]+',text)


def vector(tokens, idf):
    tf = collections.Counter(tokens)
    value = {token:count*idf[token] for token,count in tf.items() if token in idf}
    norm = math.sqrt(sum(x*x for x in value.values()))
    return {k:v/norm for k,v in value.items()} if norm else {}


def similarity(incident, references, idf):
    current = vector(incident_text(incident), idf)
    return max((min(1.0, sum(value*vector(incident_text(old),idf).get(k,0)
                              for k,value in current.items())) for old in references), default=0.0)


def fit_fault_matching(registered, pilots):
    labelled = [p for p in pilots if p['outcome'] in ('Successful repair','Confirmed failure')]
    positives = [p for p in labelled if p['outcome']=='Successful repair']
    negatives = [p for p in labelled if p['outcome']=='Confirmed failure']
    if not positives or not negatives:
        raise ValueError('Both actually labelled positive and negative pilot outcomes are required')
    if any(p.get('main_trial') or p.get('label_source') != 'observed_development_replay' for p in labelled):
        raise ValueError('Do not fit to main outcomes or scenario expectations')
    documents = [incident_text(x) for xs in registered.values() for x in xs] + [incident_text(p['incident']) for p in pilots]
    df = collections.Counter(t for d in documents for t in set(d))
    idf = {t:math.log((1+len(documents))/(1+n))+1 for t,n in sorted(df.items())}
    scores = [(similarity(p['incident'],registered[p['candidate']],idf),p['outcome']=='Successful repair') for p in labelled]
    # All distinct empirical decision boundaries, plus reject-all. Higher wins ties.
    thresholds = sorted({0.0,1.0,math.nextafter(1.0,math.inf)} | {s for s,_ in scores} |
                        {math.nextafter(s,math.inf) for s,_ in scores})
    scored=[]
    for threshold in thresholds:
        sensitivity=sum(s>=threshold for s,y in scores if y)/len(positives)
        specificity=sum(s<threshold for s,y in scores if not y)/len(negatives)
        scored.append(((sensitivity+specificity)/2,threshold))
    accuracy,threshold=max(scored)
    return {'version':'tfidf-pilot-v1','registered':registered,'idf':idf,'threshold':threshold,
            'pilot_balanced_accuracy':accuracy,'pilot_positive_count':len(positives),
            'pilot_negative_count':len(negatives),'indeterminate_pilots':len(pilots)-len(labelled),
            'normalisation':'permitted workload/symptoms/error codes only; lowercase tokens; timestamps/UUIDs/Pod suffixes removed',
            'tie_policy':'higher threshold; threshold above 1 is the explicit reject-all boundary'}


def fault_matching(model, candidate, incident):
    score=similarity(incident,model['registered'][candidate],model['idf'])
    return {'decision':'Direct reuse' if score>=model['threshold'] else 'Stop reuse',
            'score':score,'threshold':model['threshold']}


EXCLUDED = {'resourceVersion','uid','managedFields','creationTimestamp','deletionTimestamp','deletionGracePeriodSeconds','status'}


def canonical(value):
    """Keep declared names/spec/labels and realised addresses; remove volatile metadata."""
    if isinstance(value,dict):
        return {k:canonical(v) for k,v in sorted(value.items()) if k not in EXCLUDED
                and k not in ('kubectl.kubernetes.io/restartedAt',)}
    if isinstance(value,list):
        # Named Kubernetes resources and set-like links sort; command/arg and all
        # other semantic arrays retain order rather than normalising differences away.
        items=[canonical(x) for x in value]
        if all(isinstance(x,dict) and 'metadata' in x and 'name' in x['metadata'] for x in items):
            return sorted(items,key=lambda x:(x.get('kind',''),x['metadata'].get('namespace',''),x['metadata']['name']))
        return items
    return value


def whole_environment(historical, current, historical_criteria, current_criteria):
    equal = canonical(historical)==canonical(current) and historical_criteria==current_criteria
    return {'decision':'Direct reuse' if equal else 'Revalidate',
            'reason':'canonical namespace and verification specification equal' if equal else 'full revalidation required'}
