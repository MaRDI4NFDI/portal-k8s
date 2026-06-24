{{/*
Build a Traefik Host() rule from a list of host names.
*/}}
{{- define "wikibase.hostRule" -}}
{{- $first := true -}}
{{- range $host := . -}}
{{- if $first -}}
{{- $first = false -}}
{{- else }} || {{ end -}}
Host(`{{ $host }}`)
{{- end -}}
{{- end -}}

{{/*
Common environment variables shared between web and jobrunner MediaWiki containers.
*/}}
{{- define "mediawiki.env" -}}
- name: WIKIBASE_SCHEME
  value: "{{ .Values.mediawiki.scheme }}"
- name: WIKIBASE_HOST
  value: "{{ .Values.global.baseDomain }}"
- name: WIKIBASE_PORT
  value: "{{ .Values.apache.port }}"
- name: DB_SERVER
  value: "{{ .Values.mediawiki.db_server }}"
- name: DB_NAME
  value: "{{ .Values.mediawiki.db_name }}"
- name: DB_USER
  value: "{{ .Values.mediawiki.db_user }}"
- name: DB_PRIMARY_IP
  value: "{{ .Values.mediawiki.db_primary }}"
- name: DB_SECONDARY_IP
  value: "{{ .Values.mediawiki.db_secondary }}"
- name: DB_SECONDARY_IP
  value: "{{ .Values.mediawiki.db_secondary }}"
- name: DEPLOYMENT_ENV
  value: "{{ .Values.mediawiki.deployment_env }}"
- name: MW_ELASTIC_HOST
  value: "{{ .Values.mediawiki.elasticsearch_host }}"
- name: MW_ELASTIC_PORT
  value: "{{ .Values.mediawiki.elasticsearch_port }}"
- name: MW_REDIS_HOST
  value: "{{ .Values.mediawiki.redis_host }}"
- name: MW_REDIS_PORT
  value: "{{ .Values.mediawiki.redis_port }}"
- name: MW_MEMCACHED_HOST
  value: "{{ .Values.mediawiki.memcached_host }}"
- name: MW_MEMCACHED_PORT
  value: "{{ .Values.mediawiki.memcached_port }}"
- name: MW_LATEXML_HOST
  value: "{{ .Values.mediawiki.latexml_host }}"
- name: MW_LATEXML_PORT
  value: "{{ .Values.mediawiki.latexml_port }}"
- name: MW_FORMULASEARCH_HOST
  value: "{{ .Values.mediawiki.formulasearch_host }}"
- name: MW_FORMULASEARCH_PORT
  value: "{{ .Values.mediawiki.formulasearch_port }}"
- name: IMPORTER_ENDPOINT
  value: "{{ .Values.mediawiki.importerEndpoint }}"
- name: USE_CDN
  value: "{{ .Values.varnish.enabled }}"
- name: CDN_BACKEND_HOST
  value: "{{ .Values.varnish.backendHost }}"
- name: CDN_SERVER
  value: "{{ .Values.varnish.fullnameOverride }}"
- name: DB_PASS
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.mariadb }}
      key: MARIADB_PASSWORD
{{- end -}}

{{/*
PHP-FPM environment variables.
*/}}
{{- define "mediawiki.phpfpmEnv" -}}
- name: PHP_FPM_PM
  value: "{{ .pm | default "dynamic" }}"
- name: PHP_FPM_MAX_CHILDREN
  value: "{{ .maxChildren | default 75 }}"
- name: PHP_FPM_START_SERVERS
  value: "{{ .startServers | toString | default "25" }}"
- name: PHP_FPM_MIN_SPARE_SERVERS
  value: "{{ .minSpareServers | toString | default "10" }}"
- name: PHP_FPM_MAX_SPARE_SERVERS
  value: "{{ .maxSpareServers | toString | default "40" }}"
- name: PHP_FPM_MAX_REQUESTS
  value: "{{ .maxRequests | default 1000 }}"
- name: PHP_FPM_REQUEST_TIMEOUT
  value: "{{ .requestTimeout | default "60s" }}"
{{- end -}}

{{/*
PHP OPcache environment variables. Consumed by docker-wikibase entrypoint
which renders /usr/local/etc/php/conf.d/zz-opcache.ini from these.
*/}}
{{- define "mediawiki.opcacheEnv" -}}
- name: OPCACHE_MEMORY_CONSUMPTION
  value: "{{ .memoryConsumption }}"
- name: OPCACHE_MAX_ACCELERATED_FILES
  value: "{{ .maxAcceleratedFiles }}"
- name: OPCACHE_INTERNED_STRINGS_BUFFER
  value: "{{ .internedStringsBuffer }}"
- name: OPCACHE_VALIDATE_TIMESTAMPS
  value: "{{ .validateTimestamps }}"
- name: OPCACHE_REVALIDATE_FREQ
  value: "{{ .revalidateFreq }}"
- name: OPCACHE_JIT_BUFFER_SIZE
  value: "{{ .jitBufferSize }}"
{{- end -}}

{{/*
Common envFrom shared between web and jobrunner MediaWiki containers.
*/}}
{{- define "mediawiki.envFrom" -}}
- configMapRef:
    name: wikibase-config
- secretRef:
    name: {{ .Release.Name }}-env-secrets
{{- end -}}

{{/*
Common volume definitions for MediaWiki containers (oauth secrets).
*/}}
{{- define "mediawiki.volumes" -}}
- name: oauth-secrets
  secret:
    secretName: {{ .Release.Name }}-oauth-secrets
    items:
    - key: oauth.key
      path: oauth.key
      mode: 0644
    - key: oauth.cert
      path: oauth.cert
      mode: 0660
{{- end -}}

{{/*
Common volume mounts for MediaWiki containers (oauth secrets).
*/}}
{{- define "mediawiki.volumeMounts" -}}
- name: oauth-secrets
  mountPath: /var/oauth
  readOnly: true
{{- end -}}
