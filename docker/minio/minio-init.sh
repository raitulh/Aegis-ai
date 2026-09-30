#!/bin/sh
# ==============================================================================
# One-shot MinIO bootstrap for the local stack (docker-compose `minio-init`).
# Idempotent — safe to run on every `docker compose up`.
#
#   1. create the lab bucket (private: no anonymous access)
#   2. create a least-privilege policy limited to that bucket
#   3. create the application user the API/workers use (OBJECT_STORAGE_ACCESS_KEY /
#      OBJECT_STORAGE_SECRET_KEY) and attach the policy — the app never holds the
#      MinIO root credentials.
#
# Env: MINIO_ENDPOINT, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, BUCKET, APP_ACCESS_KEY,
#      APP_SECRET_KEY, MC_CONFIG_DIR / TMPDIR (writable; the container root filesystem is read-only).
# Uses only POSIX sh builtins + cat + mc (the mc image has no grep/sed/awk).
# ==============================================================================
set -eu

: "${MINIO_ENDPOINT:=http://minio:9000}"
: "${BUCKET:=aegis-lab}"
: "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}"
: "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"
: "${APP_ACCESS_KEY:?APP_ACCESS_KEY is required}"
: "${APP_SECRET_KEY:?APP_SECRET_KEY is required}"
POLICY=aegis-lab-rw

if [ "$APP_ACCESS_KEY" = "$MINIO_ROOT_USER" ]; then
  echo "[minio-init] APP_ACCESS_KEY must differ from MINIO_ROOT_USER (the app must not use root credentials)" >&2
  exit 1
fi

echo "[minio-init] connecting to ${MINIO_ENDPOINT}"
mc alias set aegis "$MINIO_ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" > /dev/null

echo "[minio-init] bucket ${BUCKET} (private)"
mc mb --ignore-existing "aegis/${BUCKET}"
mc anonymous set none "aegis/${BUCKET}"

echo "[minio-init] policy ${POLICY} (bucket-scoped read/write)"
# Object read/write/delete + listing on this bucket only: no bucket creation or
# deletion, no policy/admin actions, no access to any other bucket.
POLICY_FILE="${TMPDIR:-/tmp}/${POLICY}.json"
cat > "$POLICY_FILE" <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["s3:GetBucketLocation", "s3:ListBucket", "s3:ListBucketMultipartUploads"],
      "Resource": ["arn:aws:s3:::${BUCKET}"]
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload",
                 "s3:ListMultipartUploadParts"],
      "Resource": ["arn:aws:s3:::${BUCKET}/*"]
    }
  ]
}
JSON
mc admin policy create aegis "$POLICY" "$POLICY_FILE"

echo "[minio-init] application user ${APP_ACCESS_KEY}"
mc admin user add aegis "$APP_ACCESS_KEY" "$APP_SECRET_KEY"
# The mc image ships no grep/sed: match with a shell `case` instead.
USER_INFO="$(mc admin user info aegis "$APP_ACCESS_KEY")"
case "$USER_INFO" in
  *"$POLICY"*) echo "[minio-init] policy already attached" ;;
  *) mc admin policy attach aegis "$POLICY" --user "$APP_ACCESS_KEY" ;;
esac

echo "[minio-init] done"
