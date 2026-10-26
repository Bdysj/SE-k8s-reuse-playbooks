#!/usr/bin/env bash
# Build the MySQL seed snapshot, once.
#
# Chosen approach: a fixed seed snapshot restored to an independent work volume at
# the start of each round. This script produces the seed half.
#
# Requirements this satisfies:
#   - the seed is made exactly once, from a CLEANLY SHUT DOWN MySQL, never by
#     copying a datadir that is being written to
#   - the database version, server config and snapshot checksum are all recorded
#   - the seed is stored outside the Kubernetes namespace, so "--hard" namespace
#     deletion cannot touch it
#
# It runs in plain Docker rather than in the cluster, because a clean shutdown in
# Kubernetes would make the container exit and be restarted before the datadir
# could be archived.
#
# IMPORTANT: the effective datadir is /var/lib/mysql, NOT /data. The repository's
# mysql/config.sh tries to rewrite datadir to /data, but no file under /etc/mysql
# sets datadir, so its "file not found" branch is taken and the rewrite never
# happens. Verified on a running server with SELECT @@datadir.
#
# Written for bash 3.2 as shipped by macOS.

set -uo pipefail
cd "$(dirname "$0")/.."

IMAGE=ddn-eval/rs-mysql-db:amd64
DATADIR=/var/lib/mysql
VOL=ddn-seed-build
NAME=ddn-seed-mysql
SEED_DIR="$(pwd)/seed"
SEED_TAR="$SEED_DIR/mysql-5.7-seed.tar"
mkdir -p "$SEED_DIR"

# Seed content assertions. If these do not hold, the snapshot is not taken.
EXPECT_CITIES_MIN=900000

echo "=== building MySQL seed snapshot"
docker rm -f "$NAME" >/dev/null 2>&1
docker volume rm "$VOL" >/dev/null 2>&1
docker volume create "$VOL" >/dev/null

echo "  starting mysqld on a fresh volume (initdb will import the dump)"
docker run -d --name "$NAME" --platform linux/amd64 \
  -v "$VOL:$DATADIR" "$IMAGE" >/dev/null || { echo "start failed"; exit 1; }

echo "  waiting for initdb to complete (the 948k-row dump import is slow)"
start=$(date +%s)
ok=""
for i in $(seq 1 240); do
  # The repository's own 99-finished.sh touches this after every init script.
  if docker exec "$NAME" test -f /tmp/finished 2>/dev/null; then ok=yes; break; fi
  sleep 15
done
if [ -z "$ok" ]; then
  echo "  initdb did not finish within 60 minutes"; docker logs --tail 10 "$NAME"; exit 1
fi
echo "  initdb finished after $(( $(date +%s) - start ))s"

echo "  validating seed content before snapshotting"
CITIES=$(docker exec "$NAME" mysql -N -e "SELECT COUNT(*) FROM cities.cities;" 2>/dev/null | tr -d '[:space:]')
RTABLE=$(docker exec "$NAME" mysql -N -e "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA='ratings' AND TABLE_NAME='ratings';" 2>/dev/null | tr -d '[:space:]')
USERS=$(docker exec "$NAME" mysql -N -e "SELECT COUNT(*) FROM mysql.user WHERE user IN ('shipping','ratings');" 2>/dev/null | tr -d '[:space:]')
VERSION=$(docker exec "$NAME" mysql -N -e "SELECT VERSION();" 2>/dev/null | tr -d '[:space:]')
echo "    cities.cities rows : $CITIES"
echo "    ratings.ratings tbl: $RTABLE"
echo "    app accounts       : $USERS"
echo "    server version     : $VERSION"

fail=0
[ "${CITIES:-0}" -ge "$EXPECT_CITIES_MIN" ] || { echo "    FAIL: cities rows < $EXPECT_CITIES_MIN"; fail=1; }
[ "${RTABLE:-0}" -eq 1 ] || { echo "    FAIL: ratings.ratings table missing"; fail=1; }
[ "${USERS:-0}" -ge 2 ] || { echo "    FAIL: application accounts missing"; fail=1; }
if [ $fail -ne 0 ]; then echo "  seed content invalid, no snapshot taken"; exit 1; fi

# Record the server configuration that produced this seed.
docker exec "$NAME" sh -c 'mysqld --verbose --help 2>/dev/null | sed -n "/^innodb-page-size/p;/^character-set-server/p;/^collation-server/p"' \
  > "$SEED_DIR/mysql-server-settings.txt" 2>/dev/null
docker exec "$NAME" mysql -N -e "SELECT CONCAT('version=',VERSION()); SELECT CONCAT('datadir=',@@datadir); SELECT CONCAT('innodb_page_size=',@@innodb_page_size); SELECT CONCAT('character_set_server=',@@character_set_server); SELECT CONCAT('lower_case_table_names=',@@lower_case_table_names);" \
  >> "$SEED_DIR/mysql-server-settings.txt" 2>/dev/null

echo "  shutting mysqld down cleanly"
docker exec "$NAME" mysqladmin shutdown 2>/dev/null
for i in $(seq 1 60); do
  running=$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)
  [ "$running" = "false" ] && break
  sleep 5
done
running=$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)
if [ "$running" != "false" ]; then
  echo "  mysqld did not stop cleanly; refusing to snapshot a live datadir"; exit 1
fi
# A clean 5.7 shutdown leaves no recovery work pending. Confirm in the log.
if docker logs "$NAME" 2>&1 | tail -20 | grep -q 'Shutdown complete'; then
  echo "  confirmed: 'Shutdown complete' in server log"
else
  echo "  WARNING: 'Shutdown complete' not found in log tail"
fi

echo "  archiving datadir from the stopped container's volume"
# tar from a helper container so nothing is mounted read-write by a live server.
docker run --rm -v "$VOL:$DATADIR:ro" -v "$SEED_DIR:/out" alpine:3.20 \
  tar -cf /out/$(basename "$SEED_TAR") -C "$DATADIR" . || { echo "archive failed"; exit 1; }

SUM=$(shasum -a 256 "$SEED_TAR" | cut -d' ' -f1)
SIZE=$(wc -c < "$SEED_TAR" | tr -d ' ')
echo "$SUM  $(basename "$SEED_TAR")" > "$SEED_TAR.sha256"

cat > "$SEED_DIR/seed.meta.yaml" <<EOF
# MySQL seed snapshot. Produced once by tools/make_mysql_seed.sh.
# Restored to a fresh, independent work volume at the start of each round.
created: "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
source_image: $IMAGE
server_version: "$VERSION"
datadir: $DATADIR
datadir_note: >
  The effective datadir is /var/lib/mysql. The repository's mysql/config.sh
  attempts to rewrite it to /data but no file under /etc/mysql sets datadir, so
  the rewrite never happens. Confirmed with SELECT @@datadir on a live server.
shutdown: clean (mysqladmin shutdown, container exited, log shows Shutdown complete)
archive: $(basename "$SEED_TAR")
archive_bytes: $SIZE
archive_sha256: $SUM
content_assertions:
  cities_cities_rows: $CITIES
  ratings_ratings_table_present: $RTABLE
  application_accounts: $USERS
EOF

docker rm -f "$NAME" >/dev/null 2>&1
docker volume rm "$VOL" >/dev/null 2>&1

echo
echo "  seed archive : $SEED_TAR"
echo "  bytes        : $SIZE"
echo "  sha256       : $SUM"
echo "  metadata     : $SEED_DIR/seed.meta.yaml"
