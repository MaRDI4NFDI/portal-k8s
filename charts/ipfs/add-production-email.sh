#!/bin/sh
set -eu

if [ "$#" -ne 0 ]; then
  echo "Usage: $0" >&2
  exit 1
fi

for command_name in awk git gpg openssl sops; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 1
  fi
done

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_dir/../.." && pwd)
values_path="$repository_root/apps/production/ipfs/values-production.yaml"
kustomization_path="$repository_root/apps/production/ipfs/kustomization.yaml"
public_key="$repository_root/.sops.pub.asc"
sops_config="$repository_root/.sops.yaml"

if [ ! -f "$values_path" ] || [ ! -f "$kustomization_path" ] ||
  [ ! -f "$public_key" ] || [ ! -f "$sops_config" ]; then
  echo "Run this script from a checkout containing the production IPFS files." >&2
  exit 1
fi

key_fingerprint=$(awk '/^[[:space:]]+pgp:/ { print $2; exit }' "$sops_config")
if [ -z "$key_fingerprint" ]; then
  echo "No PGP fingerprint found in .sops.yaml." >&2
  exit 1
fi

if ! git -C "$repository_root" diff --quiet -- "$values_path" "$kustomization_path" ||
  ! git -C "$repository_root" diff --cached --quiet -- "$values_path" "$kustomization_path"; then
  echo "Refusing to overwrite uncommitted production IPFS changes." >&2
  exit 1
fi

printf 'Google email address to add: '
IFS= read -r new_email
case "$new_email" in
  ?*@?*.?*) ;;
  *)
    echo "Enter a valid email address." >&2
    exit 1
    ;;
esac

identifier=$(openssl rand -hex 8)
secret_name="ipfs-oauth2-email-$identifier"
relative_secret_path="admins/$identifier.yaml"
secret_path="$repository_root/apps/production/ipfs/$relative_secret_path"

if [ -e "$secret_path" ]; then
  echo "Generated administrator file already exists; run the script again." >&2
  exit 1
fi

temporary_directory=$(mktemp -d)
chmod 700 "$temporary_directory"
trap 'rm -rf "$temporary_directory"' EXIT HUP INT TERM
umask 077
plain_secret="$temporary_directory/email.yaml"
encrypted_secret="$temporary_directory/email.encrypted.yaml"
updated_values="$temporary_directory/values-production.yaml"
updated_kustomization="$temporary_directory/kustomization.yaml"

{
  printf '%s\n' \
    'apiVersion: v1' \
    'kind: Secret' \
    'metadata:'
  printf '  name: %s\n' "$secret_name"
  printf '%s\n' \
    'type: Opaque' \
    'stringData:' \
    '  email: |'
  printf '    %s\n' "$new_email"
} > "$plain_secret"

if ! gpg --list-keys "$key_fingerprint" >/dev/null 2>&1; then
  gpg --import "$public_key" >/dev/null 2>&1
fi

(
  cd "$repository_root"
  sops --encrypt \
    --filename-override "apps/production/ipfs/$relative_secret_path" \
    "$plain_secret" > "$encrypted_secret"
)

revision=$(date -u +%Y%m%d%H%M%S)
awk -v secret_name="$secret_name" -v revision="$revision" '
  /^      allowedEmailSecrets: \[\]$/ {
    print "      allowedEmailSecrets:"
    print "        - " secret_name
    secrets_updated = 1
    next
  }
  /^      allowedEmailSecrets:$/ {
    print
    print "        - " secret_name
    secrets_updated = 1
    next
  }
  /^      secretRevision:/ {
    print "      secretRevision: \"" revision "\""
    revision_updated = 1
    next
  }
  { print }
  END {
    if (!secrets_updated || !revision_updated) {
      exit 1
    }
  }
' "$values_path" > "$updated_values"

awk -v resource="$relative_secret_path" '
  /^  - secrets.yaml$/ {
    print
    print "  - " resource
    updated = 1
    next
  }
  { print }
  END {
    if (!updated) {
      exit 1
    }
  }
' "$kustomization_path" > "$updated_kustomization"

mkdir -p "$(dirname -- "$secret_path")"
mv "$encrypted_secret" "$secret_path"
mv "$updated_values" "$values_path"
mv "$updated_kustomization" "$kustomization_path"

echo "Added the encrypted administrator entry."
echo "Updated admin.secretRevision to $revision so Flux rolls the IPFS pod."
echo "Review and commit these three files:"
echo "  apps/production/ipfs/$relative_secret_path"
echo "  apps/production/ipfs/values-production.yaml"
echo "  apps/production/ipfs/kustomization.yaml"
