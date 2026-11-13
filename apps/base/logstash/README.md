# Logstash

MediaWiki logs for staging and production, stored the way Wikimedia
stores theirs: [Logstash](https://www.elastic.co/docs/reference/logstash)
receives the records, [OpenSearch](https://docs.opensearch.org/latest/)
stores them, and [OpenSearch Dashboards](https://docs.opensearch.org/latest/dashboards/)
lets you search them.

```text
MediaWiki (staging and production)
  │  one JSON record per UDP datagram, port 11514
  ▼
logstash ──► logging-opensearch ◄── logstash-dashboards
             index logstash-mediawiki-YYYY.MM.DD
```

One stack in the `production` namespace serves both environments. Each
record says where it came from: `host` (pod name; staging pods start
with `staging-`), `wiki` (database name), `channel` and `level`.

| Release | Chart | What it runs |
|---|---|---|
| `logstash` | `stateless-service` | Logstash with the pipeline built in |
| `logstash-dashboards` | `stateless-service` | OpenSearch Dashboards |
| `logging-opensearch` | `opensearch` | OpenSearch 2.19 on a 10 GiB `csi-rbd-sc` volume |

Only `logging-opensearch` keeps state. It is separate from
`opensearch`, the CirrusSearch cluster; logs never touch the search
index.

## Searching logs

Dashboards has no public hostname. Start the SSH tunnel to the cluster,
then forward the port:

```bash
kubectl --context mardi -n production port-forward svc/logstash-dashboards 5601
```

Open <http://localhost:5601>. The first time, create an index pattern
under *Dashboards Management → Index patterns*: pattern `logstash-*`,
time field `@timestamp`. Then use *Discover*. Useful filters:

- `channel:exception` – PHP exceptions, with `exception.trace`
- `channel:OpenIDConnect or channel:PluggableAuth` – the login flow
- `host:staging-*` – staging only; `not host:staging-*` – production only
- `level:ERROR` – errors of any channel

To query OpenSearch directly, forward its port instead:

```bash
kubectl --context mardi -n production port-forward svc/logging-opensearch 9200
```

```bash
curl -s 'localhost:9200/_cat/indices/logstash-*?v&s=index'
```

## What MediaWiki sends

MediaWiki sends a record when the wikibase release sets
`mediawiki.logstash_host` (both do). All channels send warnings and
above; the channels in `$wmgDebugChannels` in
[`LocalSettings.d/000_logging.php`](https://github.com/MaRDI4NFDI/docker-wikibase/blob/main/LocalSettings.d/000_logging.php)
of docker-wikibase send everything from debug on.

The same records also go to the pod's stderr, so `kubectl logs` shows
them even when Logstash is down. UDP never makes a request wait, so a
missing or slow Logstash does not affect the wiki; records sent then are
lost.

Some channels contain personal data. OpenIDConnect at debug level logs
users' email addresses and OIDC subjects; lower it to `info` when it is
not needed.

## Pipeline and image

The pipeline, Wikimedia's filters and the index template are built
into `ghcr.io/mardi4nfdi/logstash`, in
[docker-logstash](https://github.com/MaRDI4NFDI/docker-logstash),
whose README describes them. Each release there (`vX.Y.Z`, our own
numbering) opens a pull request in this repository that sets the new
image in `apps/production/logstash/` as `X.Y.Z@<digest>`; Renovate
leaves that image alone.

## Retention

When Logstash starts, it creates or updates the ISM policy
`logstash-retention`, which deletes an index `RETENTION_DAYS` (set in
`release.yaml`, 90) days after its creation. A changed value applies
to indices created afterwards.

## Disk space

Check the usage:

```bash
curl -s 'localhost:9200/_cat/allocation?v'
```

When the disk is 95 % full, OpenSearch makes the indices read-only and
new records are lost. It lifts that by itself once usage drops below
90 %, after the retention policy deletes old indices or the volume grows.

To grow the volume, patch the claim; Ceph RBD supports online expansion:

```bash
kubectl --context mardi -n production patch pvc opensearch-data-logging-opensearch-0 -p '{"spec":{"resources":{"requests":{"storage":"20Gi"}}}}'
```

Leave `storage.size` in the values as it is. A StatefulSet's claim
template cannot change, so a new value would make the Helm upgrade fail.

## Troubleshooting

- No records at all: check that Logstash runs and reached OpenSearch
  (`kubectl -n production logs deploy/logstash`). It logs "Installing
  OpenSearch template" and "Pipelines running" on start.
- Debug or info records missing for some channel: add it to
  `$wmgDebugChannels`.
- `mapper_parsing_exception` in the Logstash log: two records used the
  same field with different types, and the later one was rejected.
  Numbers, booleans and strings never clash, because the template
  stores them all as text. An object and a string still do; the first
  type seen in a day's index wins. `exception` is protected: the
  template maps it as an object, and Logstash moves a string
  `exception` to `exception_message`. Protect other fields the same
  way in docker-logstash when they clash.
- Records of the `api` channel never arrive: Wikimedia's filter drops
  them on purpose.
