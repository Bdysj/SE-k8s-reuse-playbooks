"""One prospective replica-degradation operator shared by variants and controls."""

DECREMENT = 2


def desired_after_fault(before, decrement=DECREMENT):
    if (not isinstance(before,int) or isinstance(before,bool) or before<1
            or decrement!=DECREMENT or isinstance(decrement,bool)):
        raise ValueError('Fault needs a healthy positive replica count and the fixed decrement two')
    return max(0,before-decrement)
