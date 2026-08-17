#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
terraform_dir="${repo_root}/infrastructure/terraform/environments/staging"
evidence_dir="${1:?usage: capture_staging_evidence.sh EVIDENCE_DIRECTORY}"

mkdir -p "$evidence_dir"

terraform_output() {
  terraform -chdir="$terraform_dir" output -raw "$1"
}

application_url="$(terraform_output application_url)"
cluster_name="$(terraform_output ecs_cluster_name)"
target_group_arn="$(terraform_output web_target_group_arn)"
database_identifier="$(terraform_output database_identifier)"
load_balancer_suffix="$(terraform_output load_balancer_arn_suffix)"
recording_profile="$(terraform_output recording_profile_enabled)"
cloudfront_distribution_id="$(
  terraform -chdir="$terraform_dir" output -json cloudfront_distribution_id | jq -r '. // empty'
)"

test -n "$application_url"
test "${application_url#https://}" != "$application_url"

terraform -chdir="$terraform_dir" state list >"${evidence_dir}/terraform-resources.txt"

curl \
  --fail \
  --silent \
  --show-error \
  --retry 12 \
  --retry-all-errors \
  --retry-delay 10 \
  --output /dev/null \
  --write-out '{"http_code":%{http_code},"ssl_verify_result":%{ssl_verify_result},"time_total_seconds":%{time_total}}\n' \
  "$application_url" >"${evidence_dir}/https-smoke-test.json"

aws ecs describe-services \
  --cluster "$cluster_name" \
  --services api web \
  --query 'services[].{service:serviceName,status:status,desired:desiredCount,running:runningCount,pending:pendingCount,rollout:deployments[0].rolloutState}' \
  --output json >"${evidence_dir}/ecs-services.json"

aws elbv2 describe-target-health \
  --target-group-arn "$target_group_arn" \
  --query 'TargetHealthDescriptions[].{state:TargetHealth.State,reason:TargetHealth.Reason,description:TargetHealth.Description}' \
  --output json >"${evidence_dir}/alb-target-health.json"

aws rds describe-db-instances \
  --db-instance-identifier "$database_identifier" \
  --query 'DBInstances[0].{status:DBInstanceStatus,engine:Engine,engineVersion:EngineVersion,instanceClass:DBInstanceClass,multiAZ:MultiAZ,storageEncrypted:StorageEncrypted,publiclyAccessible:PubliclyAccessible,allocatedStorageGiB:AllocatedStorage}' \
  --output json >"${evidence_dir}/rds-health.json"

aws cloudwatch describe-alarms \
  --alarm-name-prefix finsight-staging \
  --query 'MetricAlarms[].{name:AlarmName,state:StateValue,metric:MetricName,namespace:Namespace,threshold:Threshold,comparison:ComparisonOperator}' \
  --output json >"${evidence_dir}/cloudwatch-alarms.json"

metric_start="$(date -u -d '45 minutes ago' +'%Y-%m-%dT%H:%M:%SZ')"
metric_end="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"

capture_metric() {
  local output_file="$1"
  local namespace="$2"
  local metric_name="$3"
  local statistic="$4"
  shift 4

  aws cloudwatch get-metric-statistics \
    --namespace "$namespace" \
    --metric-name "$metric_name" \
    --dimensions "$@" \
    --statistics "$statistic" \
    --start-time "$metric_start" \
    --end-time "$metric_end" \
    --period 300 \
    --query '{label:Label,datapoints:sort_by(Datapoints,&Timestamp)}' \
    --output json >"${evidence_dir}/${output_file}"
}

capture_metric \
  ecs-api-cpu.json AWS/ECS CPUUtilization Average \
  "Name=ClusterName,Value=${cluster_name}" "Name=ServiceName,Value=api"
capture_metric \
  ecs-web-cpu.json AWS/ECS CPUUtilization Average \
  "Name=ClusterName,Value=${cluster_name}" "Name=ServiceName,Value=web"
capture_metric \
  rds-cpu.json AWS/RDS CPUUtilization Average \
  "Name=DBInstanceIdentifier,Value=${database_identifier}"
capture_metric \
  alb-request-count.json AWS/ApplicationELB RequestCount Sum \
  "Name=LoadBalancer,Value=${load_balancer_suffix}"
capture_metric \
  alb-target-response-time.json AWS/ApplicationELB TargetResponseTime Average \
  "Name=LoadBalancer,Value=${load_balancer_suffix}"

if [[ -n "$cloudfront_distribution_id" ]]; then
  aws cloudfront get-distribution \
    --id "$cloudfront_distribution_id" \
    --query 'Distribution.{status:Status,domainName:DomainName,enabled:DistributionConfig.Enabled,priceClass:DistributionConfig.PriceClass,httpVersion:DistributionConfig.HttpVersion}' \
    --output json >"${evidence_dir}/cloudfront-distribution.json"

  aws cloudwatch get-metric-statistics \
    --region us-east-1 \
    --namespace AWS/CloudFront \
    --metric-name Requests \
    --dimensions \
      "Name=DistributionId,Value=${cloudfront_distribution_id}" \
      "Name=Region,Value=Global" \
    --statistics Sum \
    --start-time "$metric_start" \
    --end-time "$metric_end" \
    --period 300 \
    --query '{label:Label,datapoints:sort_by(Datapoints,&Timestamp)}' \
    --output json >"${evidence_dir}/cloudfront-requests.json"
fi

commit_sha="${GITHUB_SHA:-$(git -C "$repo_root" rev-parse HEAD)}"
run_url=""
if [[ -n "${GITHUB_SERVER_URL:-}" && -n "${GITHUB_REPOSITORY:-}" && -n "${GITHUB_RUN_ID:-}" ]]; then
  run_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
fi

jq -n \
  --arg captured_at_utc "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" \
  --arg metric_window_start_utc "$metric_start" \
  --arg metric_window_end_utc "$metric_end" \
  --arg application_url "$application_url" \
  --arg recording_profile "$recording_profile" \
  --arg commit_sha "$commit_sha" \
  --arg workflow_run_url "$run_url" \
  '{
    captured_at_utc: $captured_at_utc,
    metric_window_start_utc: $metric_window_start_utc,
    metric_window_end_utc: $metric_window_end_utc,
    application_url: $application_url,
    recording_profile: ($recording_profile == "true"),
    commit_sha: $commit_sha,
    workflow_run_url: $workflow_run_url,
    contains_secrets: false
  }' >"${evidence_dir}/manifest.json"
