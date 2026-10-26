"""Exact nonnegative integer memory quantities for the admitted scheduler scope.

Binary suffixes are case-sensitive (Ki, Mi, Gi...). Unknown/fractional/scientific
forms remain unsupported rather than being silently rounded or guessed. This
does not replace historical reset normalisation or expand Kubernetes semantics.
Reference: https://kubernetes.io/docs/concepts/configuration/manage-resources-containers/
"""
import re

FACTORS={'':1,'k':10**3,'M':10**6,'G':10**9,'T':10**12,'P':10**15,'E':10**18,
         'Ki':2**10,'Mi':2**20,'Gi':2**30,'Ti':2**40,'Pi':2**50,'Ei':2**60}


def memory_bytes(value):
    if isinstance(value,bool) or value is None:return None
    match=re.fullmatch(r'(\d+)(Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E|)',str(value))
    return int(match[1])*FACTORS[match[2]] if match else None
