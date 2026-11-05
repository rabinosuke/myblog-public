#!/usr/bin/env bash
# CIで検証したコミットを手動で反映。DBの追加は新Webを公開する前に済ませる。
set -Eeuo pipefail
revision=${1:?commit required}
migrate_flag=${2:-no}
[[ "$revision" =~ ^[0-9a-f]{40}$ ]]
[[ "$migrate_flag" == yes || "$migrate_flag" == no ]]
exec 9>.git/production-deploy.lock
flock -n 9 || { echo 'Another deployment is running'; exit 1; }
git diff --quiet
git diff --cached --quiet
old_revision=$(git rev-parse HEAD)
backup="$HOME/backups/coco-release-$(date -u +%Y%m%dT%H%M%SZ)"
umask 077
mkdir -p "$backup"
compose=(docker compose -f docker-compose.prod.yml)
old_image=$(docker inspect "$("${compose[@]}" ps -q web)" --format '{{.Image}}')
rollback_image="myblog-web:rollback-$(date -u +%Y%m%dT%H%M%SZ)"
docker tag "$old_image" "$rollback_image"
printf '%s\n' "$old_revision" > "$backup/previous-commit"
printf '%s\n' "$rollback_image" > "$backup/previous-image"
cp .env "$backup/env"
git archive "$old_revision" | gzip > "$backup/source.tar.gz"
"${compose[@]}" exec -T db sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot --single-transaction --routines --triggers --no-tablespaces "$MYSQL_DATABASE"' | gzip > "$backup/database.sql.gz"
gzip -t "$backup/database.sql.gz"
tar -czf "$backup/media.tar.gz" media
printf 'Backup ready: %s\n' "$backup"

rollback() {
    code=$?
    trap - ERR
    echo 'Deployment failed; restoring previous application (new DB tables are retained).'
    git switch --detach "$old_revision"
    cp "$backup/env" .env
    docker tag "$rollback_image" myblog-web:latest
    "${compose[@]}" up -d --no-deps --no-build --force-recreate web nginx
    exit "$code"
}
trap rollback ERR

git fetch origin main
# mainが先に進んでいても、CIで確認したコミットだけをデプロイする。
git merge-base --is-ancestor "$revision" origin/main
git merge --ff-only "$revision"
test "$(git rev-parse HEAD)" = "$revision"
"${compose[@]}" config --quiet
"${compose[@]}" build --pull web
"${compose[@]}" run --rm --no-deps web python manage.py check --deploy --fail-level WARNING
if [[ "$migrate_flag" == yes ]]; then
    "${compose[@]}" run --rm --no-deps web python manage.py migrate --noinput
fi
"${compose[@]}" run --rm --no-deps web python manage.py migrate --check
"${compose[@]}" run --rm --no-deps web python manage.py collectstatic --noinput
"${compose[@]}" run --rm --no-deps nginx nginx -t
"${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 120 web
"${compose[@]}" up -d --no-deps --force-recreate nginx
"${compose[@]}" exec -T nginx nginx -t
curl --fail --silent --show-error --retry 5 --retry-delay 2 -o /dev/null https://example.com/health/
curl --fail --silent --show-error --retry 3 --retry-delay 2 -o /dev/null https://example.com/
trap - ERR
printf 'Deployed: %s\n' "$revision"
