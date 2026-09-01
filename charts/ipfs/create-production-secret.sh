#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 /path/to/google-client.json" >&2
  exit 1
fi

for command_name in gpg jq openssl sops; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

client_json=$1
if [ ! -f "$client_json" ]; then
  echo "Google client JSON not found: $client_json" >&2
  exit 1
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
secret_path="$repository_root/apps/production/ipfs/secrets.yaml"
public_key="$repository_root/.sops.pub.asc"
key_fingerprint=BE155DFF5AD65EA97520D7E6B003A490455B332D
redirect_uri=https://ipfs-admin.portal.mardi4nfdi.de/oauth2/callback

if [ -e "$secret_path" ]; then
  echo "Refusing to overwrite existing file: $secret_path" >&2
  exit 1
fi

client_id=$(jq -er '.web.client_id' "$client_json")
client_secret=$(jq -er '.web.client_secret' "$client_json")

if ! jq -e --arg redirect_uri "$redirect_uri" \
  '.web.redirect_uris | index($redirect_uri)' \
  "$client_json" >/dev/null; then
  echo "The Google client does not contain the required redirect URI:" >&2
  echo "$redirect_uri" >&2
  exit 1
fi

printf 'Allowed Google email address: '
IFS= read -r allowed_email
if [ -z "$allowed_email" ]; then
  echo "The allowed email address must not be empty." >&2
  exit 1
fi

cookie_secret=$(openssl rand -base64 32 | tr -- '+/' '-_')
temporary_directory=$(mktemp -d)
chmod 700 "$temporary_directory"
trap 'rm -rf "$temporary_directory"' EXIT HUP INT TERM
plain_secret="$temporary_directory/secrets.yaml"
encrypted_secret="$temporary_directory/secrets.encrypted.yaml"
umask 077

{
  printf '%s\n' \
    'apiVersion: v1' \
    'kind: Secret' \
    'metadata:' \
    '  name: ipfs-oauth2-proxy' \
    'type: Opaque' \
    'stringData:'
  printf '  OAUTH2_PROXY_CLIENT_ID: %s\n' "$client_id"
  printf '  OAUTH2_PROXY_CLIENT_SECRET: %s\n' "$client_secret"
  printf '  OAUTH2_PROXY_COOKIE_SECRET: %s\n' "$cookie_secret"
  printf '  allowed-emails: |\n    %s\n' "$allowed_email"
} > "$plain_secret"

if ! gpg --list-keys "$key_fingerprint" >/dev/null 2>&1; then
  gpg --import "$public_key" >/dev/null 2>&1
fi

(
  cd "$repository_root"
  sops --encrypt \
    --filename-override apps/production/ipfs/secrets.yaml \
    "$plain_secret" > "$encrypted_secret"
)

mv "$encrypted_secret" "$secret_path"
echo "Created encrypted Secret: $secret_path"
