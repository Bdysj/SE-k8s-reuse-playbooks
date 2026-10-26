# Reuse the already recorded arm64 build (Locust 2.34.0), without resolving dependencies again.
# load-original is an archival tag of the original image ID recorded in sources.lock.yaml.
FROM ddn-eval/rs-load:arm64-original
COPY arch-eval/grouped-load.py /load/grouped-load.py
COPY arch-eval/load-entrypoint.sh /load/grouped-entrypoint.sh
CMD ["sh", "/load/grouped-entrypoint.sh"]
