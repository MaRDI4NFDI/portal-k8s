{{/*
Environment shared by every workload that runs the resync tool, so that
`resync Q42` works with no flags at all.
*/}}
{{- define "qlever.resyncEnv" -}}
- name: PATH
  value: "/opt/resync:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
- name: QLEVER_HOST
  value: {{ .Release.Name | quote }}
- name: QLEVER_PORT
  value: {{ .Values.qlever.port | quote }}
- name: QLEVER_ACCESS_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ .Release.Name }}-secrets
      key: TOKEN
- name: ENTITY_DATA_URL
  value: {{ .Values.resync.entityDataUrl | quote }}
- name: WIKIBASE_API_URL
  value: {{ .Values.resync.apiUrl | quote }}
- name: WIKIBASE_CONCEPT_URI
  value: {{ .Values.resync.conceptUri | default (printf "https://%s" .Values.global.baseDomain) | quote }}
- name: WIKIBASE_NAMESPACES
  value: {{ .Values.resync.namespaces | quote }}
{{- end -}}
