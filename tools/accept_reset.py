#!/usr/bin/env python3
"""Three real contamination/reset cycles; pilot evidence, never main-trial data."""
import json, subprocess, sys, time
from pathlib import Path
import reset_baseline as rb

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "reset-acceptance"
OUT.mkdir(parents=True, exist_ok=True)
records = []


def exec_in(target, *args):
    return rb.kubectl("exec", target, "--", *args).strip()


def check_markers(marker):
    rating = rb.run_db_client(f"SELECT COUNT(*) FROM ratings.ratings WHERE sku='{marker}'", "ratings")
    mongo = exec_in("deploy/mongodb", "mongo", "--quiet", "--eval",
                    f"print(db.getSiblingDB('users').users.count({{name:'{marker}'}}));")
    redis = exec_in("redis-0", "redis-cli", "EXISTS", marker)
    queues = exec_in("deploy/rabbitmq", "rabbitmqctl", "list_queues", "name", "messages", "--quiet")
    price = exec_in("deploy/mongodb", "mongo", "--quiet", "--eval",
                   "print(db.getSiblingDB('catalogue').products.findOne({sku:'Watson'}).price);")
    return {"mysql_marker_rows": int(rating), "mongodb_marker_rows": int(mongo.splitlines()[-1]),
            "redis_marker_exists": int(redis), "rabbitmq_marker_exists": marker in queues,
            "watson_price": float(price.splitlines()[-1])}

try:
    for i in range(1, 4):
        marker = f"RESET_ACCEPT_{i}"
        rb.run_db_client(f"INSERT INTO ratings.ratings(sku,avg_rating,rating_count) VALUES('{marker}',4.00,1)", "ratings")
        exec_in("deploy/mongodb", "mongo", "--quiet", "--eval",
                f"db.getSiblingDB('users').users.insertOne({{name:'{marker}',password:'test'}});"
                "db.getSiblingDB('catalogue').products.updateOne({sku:'Watson'},{$set:{price:9999}});")
        exec_in("redis-0", "redis-cli", "SET", marker, "dirty")
        exec_in("deploy/payment", "python", "-c",
                f"import pika;c=pika.BlockingConnection(pika.ConnectionParameters('rabbitmq'));"
                f"ch=c.channel();ch.queue_declare(queue='{marker}',durable=True);"
                f"ch.basic_publish(exchange='',routing_key='{marker}',body=b'dirty');c.close()")
        before = check_markers(marker)
        assert before == {"mysql_marker_rows":1,"mongodb_marker_rows":1,"redis_marker_exists":1,
                          "rabbitmq_marker_exists":True,"watson_price":9999.0}, before
        rb.kubectl("delete", "pod", "db-client", "--grace-period=1", "--ignore-not-found")
        log = OUT / f"round-{i}.log"
        start = time.monotonic()
        with log.open("w") as f:
            run = subprocess.run([sys.executable, "-u", str(ROOT / "tools" / "reset_baseline.py"),
                                  "--tier-timeout", "600"], stdout=f, stderr=subprocess.STDOUT)
        if run.returncode:
            raise RuntimeError(f"round {i} reset failed: {log}")
        after = check_markers(marker)
        assert after == {"mysql_marker_rows":0,"mongodb_marker_rows":0,"redis_marker_exists":0,
                         "rabbitmq_marker_exists":False,"watson_price":2001.0}, after
        rb.kubectl("delete", "pod", "db-client", "--grace-period=1", "--ignore-not-found")
        records.append({"round":i,"marker":marker,"before":before,"after":after,
                        "seconds":round(time.monotonic()-start,2),"reset_exit_code":run.returncode,
                        "log":str(log.relative_to(ROOT)),"passed":True})
        (OUT / "summary.json").write_text(json.dumps({"type":"pilot_reset_acceptance", "passed":len(records)==3,
                                                    "rounds":records},indent=2))
        print(f"round {i}/3 PASS: all four stores restored; {records[-1]['seconds']} seconds", flush=True)
except Exception as e:
    (OUT / "summary.json").write_text(json.dumps({"type":"pilot_reset_acceptance","passed":False,
                                                "rounds":records,"error":str(e)},indent=2))
    print(str(e),file=sys.stderr)
    sys.exit(1)
