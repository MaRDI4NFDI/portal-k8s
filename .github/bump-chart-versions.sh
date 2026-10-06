#!/bin/bash
# Run by Renovate after updating images: a changed chart is only deployed
# under a new version, so bump the patch version of every chart touched and
# move the pins of the releases that use it from the old to the new version.
set -euo pipefail

replace() { sed -E "$1" "$2" > "$2.new" && mv "$2.new" "$2"; }

git diff --name-only HEAD -- 'charts/*/values.yaml' | while read -r values; do
  dir=$(dirname "$values")
  name=$(basename "$dir")
  old=$(awk '/^version:/ { print $2 }' "$dir/Chart.yaml")
  new=$(echo "$old" | awk -F. '{ print $1 "." $2 "." $3 + 1 }')
  replace "s/^version: .*/version: $new/" "$dir/Chart.yaml"

  grep -l "chart: $name\$" apps/base/*/release.yaml | while read -r release; do
    app=$(basename "$(dirname "$release")")
    for pin in apps/production/"$app"/*.yaml apps/staging/"$app"/*.yaml; do
      if [ -f "$pin" ] && grep -q "kind: HelmRelease" "$pin"; then
        replace "s/^( +version: \"?)${old//./\\.}(\"?)\$/\1$new\2/" "$pin"
      fi
    done
  done
done
