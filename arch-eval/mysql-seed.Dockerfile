# Carrier image for the MySQL seed snapshot.
#
# The seed is held in an image rather than a hostPath or ConfigMap because:
#   - it is immutable and read-only by construction, which is what the seed must be
#   - it lives in the image store, not in the Kubernetes namespace, so deleting the
#     namespace with --hard cannot touch it
#   - it is node independent, so the restore Job can run wherever the PVC binds,
#     which matters with local-path storage
#   - 488 MB is far beyond the ConfigMap size limit
#
# This image never runs a database. It only supplies the archive to the restore Job.
FROM alpine:3.20
COPY mysql-5.7-seed.tar /seed/mysql-5.7-seed.tar
COPY mysql-5.7-seed.tar.sha256 /seed/mysql-5.7-seed.tar.sha256
COPY seed.meta.yaml /seed/seed.meta.yaml
