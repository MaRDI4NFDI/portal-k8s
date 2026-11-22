{{- define "mariadb.secretName" -}}
{{- .Values.secrets.mariadb | default (printf "%s-secrets" .Release.Name) -}}
{{- end -}}
