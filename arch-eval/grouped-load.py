"""Keep upstream transactions/users/waits unchanged; bound stats cardinality.

Unique cart IDs/city UUIDs otherwise create a new Locust StatsEntry on every
transaction. name= changes only the statistical label, never the HTTP URL.
The original frozen workload remains in /load/robot-shop.py.
"""
import importlib.util
import json
import os
import re
import time
import gevent
from locust import events

spec = importlib.util.spec_from_file_location("upstream_robot_shop", "/load/robot-shop.py")
upstream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream)
UserBehavior = upstream.UserBehavior
original_start = UserBehavior.on_start


def grouped_start(self):
    original_request = self.client.request

    def request(method, url, *args, **kwargs):
        path = url.split("?", 1)[0]
        patterns = [
            (r"^/api/cart/add/[^/]+/[^/]+/[^/]+$", "/api/cart/add/{id}/{sku}/{qty}"),
            (r"^/api/cart/update/[^/]+/[^/]+/[^/]+$", "/api/cart/update/{id}/{sku}/{qty}"),
            (r"^/api/cart/cart/[^/]+$", "/api/cart/cart/{id}"),
            (r"^/api/shipping/calc/[^/]+$", "/api/shipping/calc/{city}"),
            (r"^/api/shipping/cities/[^/]+$", "/api/shipping/cities/{country}"),
            (r"^/api/shipping/confirm/[^/]+$", "/api/shipping/confirm/{id}"),
            (r"^/api/payment/pay/[^/]+$", "/api/payment/pay/{id}"),
            (r"^/api/catalogue/product/[^/]+$", "/api/catalogue/product/{sku}"),
            (r"^/api/ratings/api/fetch/[^/]+$", "/api/ratings/api/fetch/{sku}"),
            (r"^/api/ratings/api/rate/[^/]+/[^/]+$", "/api/ratings/api/rate/{sku}/{score}"),
        ]
        label = next((label for pat, label in patterns if re.match(pat, path)), path)
        if path == "/api/payment/pay/partner-57":
            label += " [upstream intentional error]"
        kwargs.setdefault("name", label)
        return original_request(method, url, *args, **kwargs)

    self.client.request = request
    return original_start(self)

UserBehavior.on_start = grouped_start


@events.test_start.add_listener
def monitor(environment, **kwargs):
    def sample():
        import psutil
        while environment.runner:
            stats = environment.stats
            record = {"time": time.time(), "users": environment.runner.user_count,
                      "requests": stats.total.num_requests, "failures": stats.total.num_failures,
                      "stats_entries": len(stats.entries),
                      "rss_bytes": psutil.Process().memory_info().rss,
                      "entries": [{"method": e.method, "name": e.name,
                                   "requests": e.num_requests, "failures": e.num_failures}
                                  for e in stats.entries.values()]}
            tmp = "/tmp/load-metrics.tmp"
            with open(tmp, "w") as f:
                json.dump(record, f)
            os.replace(tmp, "/tmp/load-metrics.json")
            gevent.sleep(15)
    gevent.spawn(sample)
