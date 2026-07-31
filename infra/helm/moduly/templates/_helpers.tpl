{{/*
Expand the name of the chart.
*/}}
{{- define "moduly.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Reject an ingress-backed production gateway that cannot recover the client
network. Direct gateway deployments may intentionally keep the list empty.
*/}}
{{- define "moduly.validateLoginTrustedProxy" -}}
{{- $nodeEnv := lower (.Values.gateway.env.NODE_ENV | default "production") -}}
{{- if and .Values.gateway.enabled .Values.ingress.enabled (eq $nodeEnv "production") (empty .Values.gateway.env.AUTH_LOGIN_TRUSTED_PROXY_CIDRS) -}}
{{- fail "gateway.env.AUTH_LOGIN_TRUSTED_PROXY_CIDRS is required for an ingress-backed production gateway" -}}
{{- end -}}
{{- end -}}

{{/*
Normalize and validate the chart-wide document storage contract. CLOUD must
never render without both coordinates; provider credentials remain external.
*/}}
{{- define "moduly.storageType" -}}
{{- upper (trim (default "LOCAL" .Values.storage.type)) -}}
{{- end -}}

{{- define "moduly.validateStorage" -}}
{{- $legacyGatewayStorage := or (hasKey .Values.gateway.env "STORAGE_TYPE") (hasKey .Values.gateway.env "S3_BUCKET_NAME") (hasKey .Values.gateway.env "AWS_REGION") -}}
{{- $legacyWorkerStorage := or (hasKey .Values.worker.env "S3_BUCKET_NAME") (hasKey .Values.worker.env "AWS_REGION") -}}
{{- if or $legacyGatewayStorage $legacyWorkerStorage -}}
{{- fail "legacy component storage keys are unsupported; use the root storage block" -}}
{{- end -}}
{{- $storageType := include "moduly.storageType" . -}}
{{- if not (has $storageType (list "LOCAL" "CLOUD")) -}}
{{- fail "storage.type must be LOCAL or CLOUD" -}}
{{- end -}}
{{- if eq $storageType "CLOUD" -}}
{{- if empty (trim (default "" .Values.storage.bucketName)) -}}
{{- fail "storage.bucketName is required when storage.type is CLOUD" -}}
{{- end -}}
{{- if empty (trim (default "" .Values.storage.region)) -}}
{{- fail "storage.region is required when storage.type is CLOUD" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Validate the bundled Knowledge worker's operational dependencies. The durable
ingestion contract requires a recovery scheduler and positive worker capacity.
*/}}
{{- define "moduly.validateKnowledgeWorker" -}}
{{- if .Values.knowledgeWorker.enabled -}}
{{- if not .Values.beat.enabled -}}
{{- fail "knowledge worker requires the bundled Celery Beat recovery scheduler" -}}
{{- end -}}
{{- $replicaCount := toString .Values.knowledgeWorker.replicaCount -}}
{{- if not (regexMatch "^[1-9][0-9]*$" $replicaCount) -}}
{{- fail "knowledgeWorker.replicaCount must be a positive integer" -}}
{{- end -}}
{{- $concurrency := toString .Values.knowledgeWorker.concurrency -}}
{{- if not (regexMatch "^[1-9][0-9]*$" $concurrency) -}}
{{- fail "knowledgeWorker.concurrency must be a positive integer" -}}
{{- end -}}
{{- $storageType := include "moduly.storageType" . -}}
{{- if and (eq $storageType "LOCAL") (not .Values.knowledgeWorker.localStorage.enabled) -}}
{{- fail "knowledgeWorker.localStorage.enabled must be true when Knowledge worker uses LOCAL storage" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Validate the operator-owned DNS peer wherever a strict egress policy consumes it.
Kubernetes permits an empty label value, so only the key must be non-empty here.
*/}}
{{- define "moduly.validateDnsEgressPeer" -}}
{{- $dnsConfig := default (dict) .Values.egressProxy.networkPolicy.dns -}}
{{- if not (kindIs "map" $dnsConfig) -}}
{{- fail "egressProxy.networkPolicy.dns must be a map" -}}
{{- end -}}
{{- $dnsNamespace := trim (toString (required "egressProxy.networkPolicy.dns.namespace is required" (get $dnsConfig "namespace"))) -}}
{{- if not (regexMatch "^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$" $dnsNamespace) -}}
{{- fail "egressProxy.networkPolicy.dns.namespace must be a valid Kubernetes namespace" -}}
{{- end -}}
{{- $dnsPodSelectorLabels := get $dnsConfig "podSelectorLabels" -}}
{{- if not (kindIs "map" $dnsPodSelectorLabels) -}}
{{- fail "egressProxy.networkPolicy.dns.podSelectorLabels must be a map" -}}
{{- end -}}
{{- range $labelKey, $_ := $dnsPodSelectorLabels -}}
{{- if empty (trim (toString $labelKey)) -}}
{{- fail "egressProxy.networkPolicy.dns.podSelectorLabels cannot contain empty keys" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
External DB/Redis/Sandbox routes may use private network ranges, but public
destinations must be exact hosts. This prevents multiple syntactically valid
broad CIDRs from reconstructing a public catch-all egress route.
*/}}
{{- define "moduly.validateExternalDependencyCidr" -}}
{{- $message := "worker.networkPolicy external dependency CIDRs must use valid private networks or exact public hosts" -}}
{{- $cidr := trim (toString .) -}}
{{- $parts := splitList "/" $cidr -}}
{{- if ne (len $parts) 2 -}}
{{- fail $message -}}
{{- end -}}
{{- $address := lower (index $parts 0) -}}
{{- $prefixText := index $parts 1 -}}
{{- if contains ":" $address -}}
{{- if or (not (regexMatch "^(0|[1-9]|[1-9][0-9]|1[01][0-9]|12[0-8])$" $prefixText)) (not (regexMatch "^[0-9a-f:]+$" $address)) (contains ":::" $address) (gt (len (regexFindAll "::" $address -1)) 1) (and (hasPrefix ":" $address) (not (hasPrefix "::" $address))) (and (hasSuffix ":" $address) (not (hasSuffix "::" $address))) -}}
{{- fail $message -}}
{{- end -}}
{{- $nonEmptySegments := 0 -}}
{{- range $segment := splitList ":" $address -}}
{{- if not (empty $segment) -}}
{{- if not (regexMatch "^[0-9a-f]{1,4}$" $segment) -}}
{{- fail $message -}}
{{- end -}}
{{- $nonEmptySegments = add1 $nonEmptySegments -}}
{{- end -}}
{{- end -}}
{{- if or (and (contains "::" $address) (gt $nonEmptySegments 7)) (and (not (contains "::" $address)) (ne $nonEmptySegments 8)) -}}
{{- fail $message -}}
{{- end -}}
{{- $prefix := int $prefixText -}}
{{- $isUla := regexMatch "^(fc|fd)[0-9a-f]{2}(:|$)" $address -}}
{{- $isUnsafe := or (eq $address "::") (eq $address "::1") (regexMatch "^(0{1,4}:){7}0{1,4}$" $address) (regexMatch "^(0{1,4}:){7}0{0,3}1$" $address) (regexMatch "^fe[89ab]" $address) (hasPrefix "ff" $address) (hasPrefix "::ffff:" $address) (regexMatch "^(0+:){5}ffff:" $address) -}}
{{- if $isUnsafe -}}
{{- fail $message -}}
{{- end -}}
{{- if $isUla -}}
{{- if lt $prefix 7 -}}
{{- fail $message -}}
{{- end -}}
{{- else if ne $prefix 128 -}}
{{- fail $message -}}
{{- end -}}
{{- else -}}
{{- if not (regexMatch "^(0|[1-9]|[12][0-9]|3[0-2])$" $prefixText) -}}
{{- fail $message -}}
{{- end -}}
{{- $octets := splitList "." $address -}}
{{- if ne (len $octets) 4 -}}
{{- fail $message -}}
{{- end -}}
{{- range $octet := $octets -}}
{{- if or (not (regexMatch "^(0|[1-9][0-9]{0,2})$" $octet)) (gt (int $octet) 255) -}}
{{- fail $message -}}
{{- end -}}
{{- end -}}
{{- $first := int (index $octets 0) -}}
{{- $second := int (index $octets 1) -}}
{{- $prefix := int $prefixText -}}
{{- $privateMinimumPrefix := 33 -}}
{{- if eq $first 10 -}}
{{- $privateMinimumPrefix = 8 -}}
{{- else if and (eq $first 172) (ge $second 16) (le $second 31) -}}
{{- $privateMinimumPrefix = 12 -}}
{{- else if and (eq $first 192) (eq $second 168) -}}
{{- $privateMinimumPrefix = 16 -}}
{{- end -}}
{{- $isUnsafe := or (eq $first 0) (eq $first 127) (and (eq $first 169) (eq $second 254)) (ge $first 224) -}}
{{- if $isUnsafe -}}
{{- fail $message -}}
{{- end -}}
{{- if le $privateMinimumPrefix 32 -}}
{{- if lt $prefix $privateMinimumPrefix -}}
{{- fail $message -}}
{{- end -}}
{{- else if ne $prefix 32 -}}
{{- fail $message -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Validate the immutable, server-owned outbound proxy boundary. The proxy source
CIDRs are deployment coordinates and must be supplied by the operator.
*/}}
{{- define "moduly.validateEgressProxy" -}}
{{- $nodeEnv := lower (.Values.gateway.env.NODE_ENV | default "production") -}}
{{- if .Values.egressProxy.enabled -}}
{{- if ne .Values.egressProxy.policyRevision "proxy-v1" -}}
{{- fail "egressProxy.policyRevision must be proxy-v1" -}}
{{- end -}}
{{- if .Values.frontend.enabled -}}
{{- if not .Values.gateway.enabled -}}
{{- fail "proxy-only frontend requires the bundled Gateway" -}}
{{- end -}}
{{- $frontendApiUrl := trim (toString (default "" .Values.frontend.env.API_URL)) -}}
{{- $bundledGatewayUrl := printf "http://%s-gateway:%d" (include "moduly.fullname" .) (.Values.gateway.service.port | int) -}}
{{- if and (not (empty $frontendApiUrl)) (ne $frontendApiUrl $bundledGatewayUrl) -}}
{{- fail "proxy-only frontend API_URL must target the bundled Gateway" -}}
{{- end -}}
{{- end -}}
{{- if ne (int .Values.egressProxy.service.httpsPort) 3128 -}}
{{- fail "egressProxy.service.httpsPort must be 3128" -}}
{{- end -}}
{{- if ne (int .Values.egressProxy.service.httpCompatiblePort) 3129 -}}
{{- fail "egressProxy.service.httpCompatiblePort must be 3129" -}}
{{- end -}}
{{- if ne (int .Values.egressProxy.service.connectorTcpPort) 3130 -}}
{{- fail "egressProxy.service.connectorTcpPort must be 3130" -}}
{{- end -}}
{{- $connectorPorts := required "egressProxy.connectorAllowedPorts is required" .Values.egressProxy.connectorAllowedPorts -}}
{{- if gt (len $connectorPorts) 16 -}}
{{- fail "egressProxy.connectorAllowedPorts must contain 1 to 16 unique ports" -}}
{{- end -}}
{{- $connectorPortSet := dict -}}
{{- range $rawPort := $connectorPorts -}}
{{- $portText := toString $rawPort -}}
{{- if not (regexMatch "^[1-9][0-9]{0,4}$" $portText) -}}
{{- fail "egressProxy.connectorAllowedPorts contains an invalid port" -}}
{{- end -}}
{{- $port := int $portText -}}
{{- if gt $port 65535 -}}
{{- fail "egressProxy.connectorAllowedPorts contains an invalid port" -}}
{{- end -}}
{{- $portKey := printf "%d" $port -}}
{{- if hasKey $connectorPortSet $portKey -}}
{{- fail "egressProxy.connectorAllowedPorts must contain 1 to 16 unique ports" -}}
{{- end -}}
{{- $_ := set $connectorPortSet $portKey true -}}
{{- end -}}
{{- range $rawPort := required "connectorTest.allowedPorts is required" .Values.connectorTest.allowedPorts -}}
{{- $portKey := printf "%d" (int $rawPort) -}}
{{- if not (hasKey $connectorPortSet $portKey) -}}
{{- fail "connectorTest.allowedPorts must be included in egressProxy.connectorAllowedPorts" -}}
{{- end -}}
{{- end -}}
{{- if not (regexMatch "^sha256:[a-f0-9]{64}$" (default "" .Values.egressProxy.image.digest)) -}}
{{- fail "egressProxy.image.digest must be an immutable sha256 digest" -}}
{{- end -}}
{{- include "moduly.validateDnsEgressPeer" . -}}
{{- if empty .Values.egressProxy.networkPolicy.authorizedSourceCidrs -}}
{{- fail "egressProxy.networkPolicy.authorizedSourceCidrs is required" -}}
{{- end -}}
{{- range $cidr := .Values.egressProxy.networkPolicy.authorizedSourceCidrs -}}
{{- $value := trim (toString $cidr) -}}
{{- if or (empty $value) (eq $value "0.0.0.0/0") (eq $value "::/0") (eq $value "127.0.0.0/8") (eq $value "169.254.0.0/16") (eq $value "fe80::/10") -}}
{{- fail "egressProxy authorized source CIDRs cannot be empty, catch-all, loopback, link-local, or metadata ranges" -}}
{{- end -}}
{{- end -}}
{{- if and (not .Values.postgresql.enabled) (empty .Values.worker.networkPolicy.externalDatabaseCidrs) -}}
{{- fail "worker.networkPolicy.externalDatabaseCidrs is required when PostgreSQL is external" -}}
{{- end -}}
{{- if and (not .Values.redis.enabled) (empty .Values.worker.networkPolicy.externalRedisCidrs) -}}
{{- fail "worker.networkPolicy.externalRedisCidrs is required when Redis is external" -}}
{{- end -}}
{{- $externalCidrs := concat .Values.worker.networkPolicy.externalDatabaseCidrs .Values.worker.networkPolicy.externalRedisCidrs .Values.worker.networkPolicy.externalSandboxCidrs -}}
{{- range $externalCidr := $externalCidrs -}}
{{- $cidr := trim (toString $externalCidr) -}}
{{- if empty $cidr -}}
{{- fail "worker.networkPolicy external dependency CIDRs cannot be empty" -}}
{{- end -}}
{{- include "moduly.validateExternalDependencyCidr" $cidr -}}
{{- end -}}
{{- if not (has .Values.egressProxy.networkPolicy.enforcementPhase (list "canary" "final")) -}}
{{- fail "egressProxy.networkPolicy.enforcementPhase must be canary or final" -}}
{{- end -}}
{{- if and (eq $nodeEnv "production") (lt (int .Values.egressProxy.replicaCount) 2) -}}
{{- fail "production egressProxy.replicaCount must be at least 2" -}}
{{- end -}}
{{- if and (eq $nodeEnv "production") (or (not .Values.egressProxy.networkPolicy.enabled) (not .Values.egressProxy.networkPolicy.enforced)) -}}
{{- fail "production egressProxy network policy must be enabled and enforced" -}}
{{- end -}}
{{- if and (eq $nodeEnv "production") .Values.worker.enabled (not .Values.worker.networkPolicy.enabled) -}}
{{- fail "production Worker proxy-only network policy must be enabled" -}}
{{- end -}}
{{- else if and (eq $nodeEnv "production") (or .Values.gateway.enabled .Values.worker.enabled .Values.knowledgeWorker.enabled) -}}
{{- fail "production public HTTP workloads require egressProxy.enabled" -}}
{{- end -}}
{{- end -}}

{{/*
Render the single operator-owned DNS peer used by every strict egress policy.
An empty podSelectorLabels map preserves the namespace-only legacy behavior.
*/}}
{{- define "moduly.dnsEgressPeer" -}}
{{- include "moduly.validateDnsEgressPeer" . -}}
{{- $dnsConfig := .Values.egressProxy.networkPolicy.dns -}}
{{- $labels := get $dnsConfig "podSelectorLabels" -}}
- namespaceSelector:
    matchLabels:
      kubernetes.io/metadata.name: {{ get $dnsConfig "namespace" | quote }}
{{- with $labels }}
  podSelector:
    matchLabels:
      {{- toYaml . | nindent 6 }}
{{- end }}
{{- end }}

{{- define "moduly.outboundProxyEnv" -}}
{{- if .context.Values.egressProxy.enabled }}
- name: OUTBOUND_TRANSPORT_MODE
  value: "proxy_guarded_external"
- name: OUTBOUND_PROXY_URL
  value: {{ printf "http://%s-egress-proxy:%d" (include "moduly.fullname" .context) (.port | int) | quote }}
- name: OUTBOUND_PROXY_ALLOWED_HOSTS
  value: {{ printf "%s-egress-proxy" (include "moduly.fullname" .context) | quote }}
- name: OUTBOUND_PROXY_POLICY_REVISION
  value: {{ .context.Values.egressProxy.policyRevision | quote }}
- name: CONNECTOR_EGRESS_PROXY_URL
  value: {{ printf "http://%s-egress-proxy:%d" (include "moduly.fullname" .context) (.context.Values.egressProxy.service.connectorTcpPort | int) | quote }}
- name: CONNECTOR_EGRESS_PROXY_ALLOWED_HOSTS
  value: {{ printf "%s-egress-proxy" (include "moduly.fullname" .context) | quote }}
- name: CONNECTOR_EGRESS_POLICY_REVISION
  value: "connector-egress-v1"
- name: CONNECTOR_EGRESS_ALLOWED_PORTS
  value: {{ join "," .context.Values.egressProxy.connectorAllowedPorts | quote }}
{{- else }}
- name: OUTBOUND_TRANSPORT_MODE
  value: "direct_pinned_internal_or_dedicated"
{{- end }}
{{- end -}}

{{/*
During canary rollout, strict workload egress selects only proxy-aware pods.
In final phase the revision label is intentionally omitted so every pod of the
component is covered and an unlabeled stale pod cannot retain direct egress.
*/}}
{{- define "moduly.egressWorkloadSelectorLabel" -}}
{{- if eq .Values.egressProxy.networkPolicy.enforcementPhase "canary" -}}
nodease.io/egress-mode: proxy-v1
{{- end -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "moduly.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "moduly.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "moduly.labels" -}}
helm.sh/chart: {{ include "moduly.chart" . }}
{{ include "moduly.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "moduly.selectorLabels" -}}
app.kubernetes.io/name: {{ include "moduly.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
  Schedule dispatch is intentionally explicit in both Gateway and Worker pods.
  Keeping the values in one chart-level block prevents the two admission
  boundaries from silently using different modes or deadlines.
*/}}
{{- define "moduly.scheduleDispatchFingerprint" -}}
{{- printf "v1|%v|%v|%v|%v|%v|%v|%v|%v|%v|%v|%v|%v|%v|%v" .Values.scheduleDispatch.mode .Values.scheduleDispatch.pollSeconds .Values.scheduleDispatch.occurrenceBatchSize .Values.scheduleDispatch.dispatchBatchSize .Values.scheduleDispatch.recoveryBatchSize .Values.scheduleDispatch.cleanupBatchSize .Values.scheduleDispatch.leaseSeconds .Values.scheduleDispatch.deliveryTimeoutSeconds .Values.scheduleDispatch.executionDeadlineSeconds .Values.scheduleDispatch.workflowRunVisibilityTimeoutSeconds .Values.scheduleDispatch.maxAttempts .Values.scheduleDispatch.retryBaseSeconds .Values.scheduleDispatch.retentionDays .Values.scheduleDispatch.deadLetterRetentionDays -}}
{{- end }}

{{- define "moduly.validateScheduleDispatchMode" -}}
{{- if ne .Values.scheduleDispatch.mode "disabled" -}}
{{- fail "non-disabled schedule dispatch is unsupported by the current provider-neutral deployment surface" -}}
{{- end -}}
{{- end }}

{{- define "moduly.scheduleDispatchEnv" -}}
- name: SCHEDULE_DISPATCH_MODE
  value: {{ .Values.scheduleDispatch.mode | quote }}
- name: SCHEDULE_DISPATCH_MODE_FINGERPRINT
  valueFrom:
    fieldRef:
      fieldPath: metadata.annotations['nodease.io/schedule-dispatch-fingerprint']
- name: SCHEDULE_DISPATCH_POLL_SECONDS
  value: {{ .Values.scheduleDispatch.pollSeconds | quote }}
- name: SCHEDULE_OCCURRENCE_BATCH_SIZE
  value: {{ .Values.scheduleDispatch.occurrenceBatchSize | quote }}
- name: SCHEDULE_DISPATCH_BATCH_SIZE
  value: {{ .Values.scheduleDispatch.dispatchBatchSize | quote }}
- name: SCHEDULE_RECOVERY_BATCH_SIZE
  value: {{ .Values.scheduleDispatch.recoveryBatchSize | quote }}
- name: SCHEDULE_CLEANUP_BATCH_SIZE
  value: {{ .Values.scheduleDispatch.cleanupBatchSize | quote }}
- name: SCHEDULE_DISPATCH_LEASE_SECONDS
  value: {{ .Values.scheduleDispatch.leaseSeconds | quote }}
- name: SCHEDULE_ENQUEUED_DELIVERY_TIMEOUT_SECONDS
  value: {{ .Values.scheduleDispatch.deliveryTimeoutSeconds | quote }}
- name: SCHEDULE_EXECUTION_DEADLINE_SECONDS
  value: {{ .Values.scheduleDispatch.executionDeadlineSeconds | quote }}
- name: SCHEDULE_WORKFLOW_RUN_VISIBILITY_TIMEOUT_SECONDS
  value: {{ .Values.scheduleDispatch.workflowRunVisibilityTimeoutSeconds | quote }}
- name: SCHEDULE_DISPATCH_MAX_ATTEMPTS
  value: {{ .Values.scheduleDispatch.maxAttempts | quote }}
- name: SCHEDULE_DISPATCH_RETRY_BASE_SECONDS
  value: {{ .Values.scheduleDispatch.retryBaseSeconds | quote }}
- name: SCHEDULE_DISPATCH_RETENTION_DAYS
  value: {{ .Values.scheduleDispatch.retentionDays | quote }}
- name: SCHEDULE_DISPATCH_DEAD_LETTER_RETENTION_DAYS
  value: {{ .Values.scheduleDispatch.deadLetterRetentionDays | quote }}
{{- end }}

{{/*
Component-specific labels
Usage: {{ include "moduly.componentLabels" (dict "component" "gateway" "context" .) }}
*/}}
{{- define "moduly.componentLabels" -}}
{{ include "moduly.labels" .context }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Component-specific selector labels
Usage: {{ include "moduly.componentSelectorLabels" (dict "component" "gateway" "context" .) }}
*/}}
{{- define "moduly.componentSelectorLabels" -}}
{{ include "moduly.selectorLabels" .context }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "moduly.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "moduly.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Get PostgreSQL host
*/}}
{{- define "moduly.postgresql.host" -}}
{{- if .Values.postgresql.enabled }}
{{- printf "%s-postgresql" .Release.Name }}
{{- else }}
{{- required "External PostgreSQL host must be specified when postgresql.enabled is false" .Values.postgresql.externalHost }}
{{- end }}
{{- end }}

{{/*
Get the canonical PostgreSQL port used by both application config and egress policy.
*/}}
{{- define "moduly.postgresql.port" -}}
{{- .Values.postgresql.auth.port | default 5432 -}}
{{- end }}

{{/*
Get Redis host
*/}}
{{- define "moduly.redis.host" -}}
{{- if .Values.redis.enabled }}
{{- printf "%s-redis-master" .Release.Name }}
{{- else }}
{{- required "External Redis host must be specified when redis.enabled is false" .Values.redis.externalHost }}
{{- end }}
{{- end }}

{{/*
Get the ConfigMap name
*/}}
{{- define "moduly.configMapName" -}}
{{- printf "%s-config" (include "moduly.fullname" .) }}
{{- end }}

{{/*
Get the Secret name
*/}}
{{- define "moduly.secretName" -}}
{{- printf "%s-secrets" (include "moduly.fullname" .) }}
{{- end }}

{{/*
Return the appropriate apiVersion for ingress
*/}}
{{- define "moduly.ingress.apiVersion" -}}
{{- if semverCompare ">=1.19-0" .Capabilities.KubeVersion.GitVersion }}
{{- print "networking.k8s.io/v1" }}
{{- else if semverCompare ">=1.14-0" .Capabilities.KubeVersion.GitVersion }}
{{- print "networking.k8s.io/v1beta1" }}
{{- else }}
{{- print "extensions/v1beta1" }}
{{- end }}
{{- end }}

{{/*
Return the appropriate apiVersion for NetworkPolicy
*/}}
{{- define "moduly.networkPolicy.apiVersion" -}}
{{- if semverCompare ">=1.7-0" .Capabilities.KubeVersion.GitVersion }}
{{- print "networking.k8s.io/v1" }}
{{- else }}
{{- print "extensions/v1beta1" }}
{{- end }}
{{- end }}
{{/* Shared claim used only when durable Knowledge workers consume LOCAL uploads. */}}
{{- define "moduly.knowledgeUploadClaimName" -}}
{{- default (printf "%s-knowledge-uploads" (include "moduly.fullname" .)) .Values.knowledgeWorker.localStorage.existingClaim -}}
{{- end -}}
