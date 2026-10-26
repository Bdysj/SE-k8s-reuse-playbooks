"""Runner cancellation through its supported callback, with a termination gate."""
import threading
import ansible_runner

class ControlledRunner:
    def __init__(self,runner,thread,stop):
        self._runner,self._thread,self._stop=runner,thread,stop
    def __getattr__(self,name):return getattr(self._runner,name)
    def cancel(self):
        self._stop.set()
        self._thread.join(timeout=30)
        if self._thread.is_alive():
            raise RuntimeError('Ansible has not stopped; refuse reset concurrent with candidate execution')

def run_async(**kwargs):
    stop=threading.Event()
    thread,runner=ansible_runner.run_async(cancel_callback=stop.is_set,**kwargs)
    return thread,ControlledRunner(runner,thread,stop)
