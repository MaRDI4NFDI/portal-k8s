{{- define "mariadb.resourceName" -}}
{{- if .root.Values.prefixResourceNames -}}
{{ .root.Release.Name }}-{{ .name }}
{{- else -}}
{{ .name }}
{{- end -}}
{{- end -}}

{{- define "mariadb.secretName" -}}
{{- .Values.secrets.mariadb | default (printf "%s-secrets" .Release.Name) -}}
{{- end -}}
