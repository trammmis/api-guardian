"""构建并冻结 E3-B 的 pre-system Ground Truth 数据集。.

本脚本只读取 Git commit 元数据与不可变 revision 的 OpenAPI artifact；它不会导入、
调用或评价 Guardian production Core。Guardian System Output 只能在用户确认冻结数据后
由后续独立步骤生成。
"""

import argparse
import hashlib
import json
import subprocess
import urllib.parse
import urllib.request
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from rich.console import Console

from benchmarks.guardian.real_world_models import (
    BreakingChangeEvidence,
    ContractVerdict,
    FrozenArtifact,
    GroundTruthFreezeManifest,
    PreSystemReviewRecord,
    RepositorySnapshot,
    ReviewConfidence,
    ReviewStatus,
)

MAX_SPEC_BYTES = 5 * 1024 * 1024
HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put", "trace"}

ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = ROOT / "benchmarks" / "guardian" / "datasets" / "real_world"
REVIEW_ROOT = DATASET_ROOT / "reviews" / "pre_system"
REPORT_ROOT = ROOT / "benchmarks" / "guardian" / "reports" / "e3"
WORKTREE_ROOT = ROOT / "benchmarks" / "guardian" / "worktrees" / "e3"

CORE_PATHS = (
    "app/services/guardian/oasdiff.py",
    "app/services/guardian/scope.py",
    "app/services/guardian/schemathesis.py",
    "app/services/guardian/nodes.py",
    "app/services/guardian/graph.py",
    "app/services/guardian/models.py",
    "app/services/guardian/security.py",
    "app/services/guardian/errors.py",
)

SYSTEM_OUTPUT_PATHS = (
    "benchmarks/guardian/reports/e3/raw_guardian_results.jsonl",
    "benchmarks/guardian/reports/e3/real_world_misses.jsonl",
    "benchmarks/guardian/reports/e3/summary.json",
    "benchmarks/guardian/reports/e3/summary.csv",
    "benchmarks/guardian/reports/e3/e3_contract_scope_report.md",
)

PRIOR_ARTIFACT_PATHS = (
    (
        "benchmarks/guardian/reports/e2/latest/runtime_raw_results.jsonl",
        "benchmarks/guardian/reports/e2/latest/raw_results.jsonl",
    ),
    (
        "benchmarks/guardian/reports/e2/latest/contract_raw_results.jsonl",
        "benchmarks/guardian/reports/e2/latest/contract_raw_results.jsonl",
    ),
    (
        "benchmarks/guardian/reports/e2_5/scaling/raw_results.jsonl",
        "benchmarks/guardian/reports/e2_5/scaling/raw_results.jsonl",
    ),
    (
        "benchmarks/guardian/reports/e2_5/miss_analysis/miss_summary.json",
        "benchmarks/guardian/reports/e2_5/miss_analysis/miss_summary.json",
    ),
    (
        "benchmarks/guardian/reports/e2_5/scaling/summary.json",
        "benchmarks/guardian/reports/e2_5/scaling/summary.json",
    ),
)

REPOSITORIES: dict[str, dict[str, Any]] = {
    "immich": {
        "repo": "immich-app/immich",
        "slug": "immich-app/immich",
        "remote_url": "https://github.com/immich-app/immich.git",
        "license": "AGPL-3.0",
        "worktree": "immich",
        "spec_path": "open-api/immich-openapi-specs.json",
        "clone_timestamp": "2026-09-07T05:30:23.2776821Z",
        "runtime_candidate": False,
    },
    "supabase": {
        "repo": "supabase/auth",
        "slug": "supabase/auth",
        "remote_url": "https://github.com/supabase/auth.git",
        "license": "MIT",
        "worktree": "supabase-auth",
        "spec_path": "openapi.yaml",
        "clone_timestamp": "2026-09-07T05:29:56.0319812Z",
        "runtime_candidate": True,
    },
    "netbird": {
        "repo": "netbirdio/netbird",
        "slug": "netbirdio/netbird",
        "remote_url": "https://github.com/netbirdio/netbird.git",
        "license": "BSD-3-Clause for shared/",
        "worktree": "netbird",
        "spec_path": "shared/management/http/api/openapi.yml",
        "clone_timestamp": "2026-09-07T05:30:44.6050953Z",
        "runtime_candidate": False,
    },
    "appwrite": {
        "repo": "appwrite/specs",
        "slug": "appwrite/specs",
        "remote_url": "https://github.com/appwrite/specs.git",
        "license": "BSD-3-Clause",
        "worktree": "appwrite-specs",
        "spec_path": "specs/1.9.x/open-api3-1.9.x-server.json",
        "clone_timestamp": "2026-09-07T05:30:07.9861117Z",
        "runtime_candidate": False,
    },
}


IMMICH_ASSET_SCOPE = [
    "GET /assets/{id}",
    "PUT /assets/{id}",
    "GET /duplicates",
    "GET /memories",
    "POST /memories",
    "GET /memories/{id}",
    "PUT /memories/{id}",
    "GET /search/cities",
    "GET /search/explore",
    "POST /search/large-assets",
    "POST /search/metadata",
    "POST /search/random",
    "POST /search/smart",
    "GET /shared-links",
    "POST /shared-links",
    "POST /shared-links/login",
    "GET /shared-links/me",
    "GET /shared-links/{id}",
    "PATCH /shared-links/{id}",
    "GET /stacks",
    "POST /stacks",
    "GET /stacks/{id}",
    "PUT /stacks/{id}",
    "GET /view/folder",
]

SUPABASE_WEBAUTHN_SCOPE = [
    "POST /token",
    "POST /verify",
    "POST /signup",
    "GET /user",
    "PUT /user",
    "POST /factors/{factorId}/challenge",
    "POST /factors/{factorId}/verify",
    "POST /invite",
    "GET /admin/users",
    "GET /admin/users/{userId}",
    "PUT /admin/users/{userId}",
    "DELETE /admin/users/{userId}",
    "GET /admin/users/{userId}/factors",
    "PUT /admin/users/{userId}/factors/{factorId}",
    "DELETE /admin/users/{userId}/factors/{factorId}",
]

NETBIRD_SETTINGS_SCOPE = [
    "POST /api/agent-network/providers",
    "PUT /api/agent-network/providers/{providerId}",
    "GET /api/agent-network/settings",
    "POST /api/agent-network/settings",
    "PUT /api/agent-network/settings",
]

APPWRITE_PROJECT_SCOPE = [
    "GET /organization/projects",
    "POST /organization/projects",
    "GET /organization/projects/{projectId}",
    "PATCH /organization/projects/{projectId}",
    "GET /project",
    "PATCH /project/auth-methods/{methodId}",
    "PUT /project/labels",
    "PUT /project/oauth2-server",
    "PATCH /project/policies/deny-aliased-email",
    "PATCH /project/policies/deny-corporate-email",
    "PATCH /project/policies/deny-disposable-email",
    "PATCH /project/policies/deny-free-email",
    "PATCH /project/policies/membership-privacy",
    "PATCH /project/policies/password-dictionary",
    "PATCH /project/policies/password-history",
    "PATCH /project/policies/password-personal-data",
    "PATCH /project/policies/session-alert",
    "PATCH /project/policies/session-duration",
    "PATCH /project/policies/session-invalidation",
    "PATCH /project/policies/session-limit",
    "PATCH /project/policies/user-limit",
    "PATCH /project/protocols/{protocolId}",
    "PATCH /project/services/{serviceId}",
    "PATCH /project/smtp",
]


def breaking(
    change_type: str,
    base_fact: str,
    head_fact: str,
    reason: str,
    *,
    operation: str | None = None,
    schema_name: str | None = None,
) -> dict[str, Any]:
    """创建紧凑的人工审查证据定义。."""
    return {
        "change_type": change_type,
        "operation": operation,
        "schema_name": schema_name,
        "base_fact": base_fact,
        "head_fact": head_fact,
        "compatibility_reason": reason,
    }


CASES: list[dict[str, Any]] = [
    {
        "case_id": "rw_immich_001_cluster_group_regenerate_added",
        "repo_key": "immich",
        "base": "351be9576996230ed8390abfc454d1fc81a78b62",
        "head": "c7d24321bf30ba83ebb4d4ae43bf9742c91b847f",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["endpoint_added"],
        "verdict": "pass",
        "breaking": [],
        "contract_scope": [],
        "runtime_scope": [],
        "evidence": [
            "OpenAPI paths adds POST /cluster-groups/{id}/regenerate-people and removes no operation.",
            "The adjacent commit message is 'feat: allow users to re-run facial recognition for their group (#30965)'.",
        ],
        "notes": ["An added operation does not invalidate existing callers."],
    },
    {
        "case_id": "rw_immich_002_api_key_response_fields_added",
        "repo_key": "immich",
        "base": "7165eb61ebc1330b91ad35126a532eed5b7344fa",
        "head": "47c5a3dbf6d3eef7fa9284c5cd1b2d4b5ae31276",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["response_property_added", "shared_component_changed"],
        "verdict": "pass",
        "breaking": [],
        "contract_scope": [],
        "runtime_scope": [],
        "evidence": [
            "ApiKeyCreateResponseDto retains apiKey and secret and adds createdAt, id, name, permissions, and updatedAt.",
            "The response additions narrow server output without removing or changing an existing response field.",
        ],
        "notes": ["Required response-field additions are additive for existing response consumers."],
    },
    {
        "case_id": "rw_immich_003_album_user_description_only",
        "repo_key": "immich",
        "base": "cd308ad93093735135f99d85ce6980c8e93df231",
        "head": "4c2d2284aa51ace226ebab6c6f44bffe0fc07f15",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["documentation_only"],
        "verdict": "pass",
        "breaking": [],
        "contract_scope": [],
        "runtime_scope": [],
        "evidence": [
            "DELETE and PUT /albums/{id}/user/{userId} only gain parameter descriptions and x-immich-history metadata.",
            "Parameter names, locations, types, and required flags are unchanged.",
        ],
        "notes": [],
    },
    {
        "case_id": "rw_immich_004_tag_name_pattern_narrowed",
        "repo_key": "immich",
        "base": "2feb889b9cdb189b08ef55fb94904315fdac04be",
        "head": "1b3aa9cd512a01a44ffe807ac509c456310c7750",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["request_schema_changed", "shared_component_changed"],
        "verdict": "fail",
        "breaking": [
            breaking(
                "request_pattern_added",
                "TagCreateDto.name is a required string without a pattern.",
                "TagCreateDto.name adds pattern ^[^/]*$.",
                "Previously valid tag names containing '/' no longer satisfy the request contract.",
                schema_name="TagCreateDto",
            )
        ],
        "contract_scope": ["POST /tags"],
        "runtime_scope": ["POST /tags"],
        "evidence": [
            "The only semantic schema delta is the new TagCreateDto.name pattern.",
            "A fragment-reference closure from POST /tags reaches TagCreateDto; no other operation does.",
        ],
        "notes": [],
    },
    {
        "case_id": "rw_immich_005_server_theme_endpoint_removed",
        "repo_key": "immich",
        "base": "7d181f0686aa70f910bfa2f985a8d5471995b95b",
        "head": "2f8be45fe0fc2c343f6255cd3fed5c4cb3a4d4f2",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["endpoint_removed"],
        "verdict": "fail",
        "breaking": [
            breaking(
                "endpoint_removed",
                "GET /server/theme exists.",
                "GET /server/theme and ServerThemeDto are absent.",
                "Existing callers can no longer invoke the documented operation.",
                operation="GET /server/theme",
            )
        ],
        "contract_scope": ["GET /server/theme"],
        "runtime_scope": [],
        "evidence": [
            "The OpenAPI operation count decreases from 240 to 239 and the removed operation is GET /server/theme."
        ],
        "notes": ["The removed operation belongs to ContractAffectedScope but is not executable from the Head Spec."],
    },
    {
        "case_id": "rw_immich_006_asset_face_response_removed",
        "repo_key": "immich",
        "base": "a278c10c7594746bbc99cccacdf9eb51ac228436",
        "head": "38438c8d9a4b19e8563327b26d098bec6d862711",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["response_property_removed", "response_schema_changed", "shared_component_changed"],
        "verdict": "fail",
        "breaking": [
            breaking(
                "response_property_removed",
                "AssetResponseDto exposes unassignedFaces and people items use PersonWithFacesResponseDto.",
                "unassignedFaces is removed and people items use PersonResponseDto; face-bearing component schemas are removed.",
                "Clients relying on the former face fields can no longer obtain the documented response shape.",
                schema_name="AssetResponseDto",
            )
        ],
        "contract_scope": IMMICH_ASSET_SCOPE,
        "runtime_scope": IMMICH_ASSET_SCOPE,
        "evidence": [
            "The component diff removes AssetResponseDto.unassignedFaces and replaces the people item schema.",
            "Independent transitive fragment-reference review maps AssetResponseDto to 24 Head operations.",
        ],
        "notes": ["The broad scope is caused by genuine shared response-component reuse, not a fallback assumption."],
    },
    {
        "case_id": "rw_immich_007_upload_duration_maximum_widened",
        "repo_key": "immich",
        "base": "d788169bf3333cb5f075482c4eb66ee16c262e16",
        "head": "a278c10c7594746bbc99cccacdf9eb51ac228436",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["request_schema_changed", "shared_component_changed"],
        "verdict": "pass",
        "breaking": [],
        "contract_scope": [],
        "runtime_scope": [],
        "evidence": [
            "AssetMediaCreateDto.duration maximum increases from 2147483647 to 9007199254740991.",
            "The integer type and minimum are unchanged, so every previously valid request remains valid.",
        ],
        "notes": [],
    },
    {
        "case_id": "rw_supabase_001_oauth_grants_endpoints_added",
        "repo_key": "supabase",
        "base": "6296a5a226b3c60bcd9d20786750a808af9cd529",
        "head": "cc640b277989d57b39f3805cd9433ef4fe16bf83",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["endpoint_added"],
        "verdict": "pass",
        "breaking": [],
        "contract_scope": [],
        "runtime_scope": [],
        "evidence": [
            "GET and DELETE /user/oauth/grants are added; no operation or existing schema member is removed.",
            "Operation count increases from 52 to 54.",
        ],
        "notes": ["Marked runtime_candidate for a later separately approved E3-C review."],
    },
    {
        "case_id": "rw_supabase_002_oauth_client_update_endpoint_added",
        "repo_key": "supabase",
        "base": "cf39a8ae2cc386f2672f0ecbb8d84dd77f04e56f",
        "head": "6296a5a226b3c60bcd9d20786750a808af9cd529",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["endpoint_added"],
        "verdict": "pass",
        "breaking": [],
        "contract_scope": [],
        "runtime_scope": [],
        "evidence": ["PUT /admin/oauth/clients/{client_id} is added and the operation count increases from 51 to 52."],
        "notes": ["Marked runtime_candidate for a later separately approved E3-C review."],
    },
    {
        "case_id": "rw_supabase_003_oauth_client_optional_fields_added",
        "repo_key": "supabase",
        "base": "68cb8d2ba3ded878c68d7cb76465bfaaac58436a",
        "head": "cf39a8ae2cc386f2672f0ecbb8d84dd77f04e56f",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["request_schema_changed", "response_property_added", "shared_component_changed"],
        "verdict": "pass",
        "breaking": [],
        "contract_scope": [],
        "runtime_scope": [],
        "evidence": [
            "client_type, logo_uri, and token_endpoint_auth_method are added as optional request properties to two OAuth client registration operations.",
            "OAuthClientSchema adds optional response property logo_uri; existing properties and required lists are retained.",
        ],
        "notes": ["Marked runtime_candidate for a later separately approved E3-C review."],
    },
    {
        "case_id": "rw_supabase_004_webauthn_schema_rework",
        "repo_key": "supabase",
        "base": "1f804a2795012a1a165ff07afdb9dd98ad8ff291",
        "head": "68cb8d2ba3ded878c68d7cb76465bfaaac58436a",
        "status": "confirmed",
        "confidence": "high",
        "change_types": [
            "response_schema_changed",
            "response_property_removed",
            "shared_component_changed",
            "mixed_change",
        ],
        "verdict": "fail",
        "breaking": [
            breaking(
                "response_property_renamed",
                "MFAFactorSchema contains web_authn_credential.",
                "The property is removed and webauthn_credential is added.",
                "A response consumer using the old property name loses that field.",
                schema_name="MFAFactorSchema",
            ),
            breaking(
                "response_shape_replaced",
                "WebAuthnChallengeResponse requires credential_options.",
                "credential_options is removed and a required discriminated webauthn object is added.",
                "The challenge response shape is replaced rather than additively extended.",
                schema_name="WebAuthnChallengeResponse",
            ),
        ],
        "contract_scope": SUPABASE_WEBAUTHN_SCOPE,
        "runtime_scope": SUPABASE_WEBAUTHN_SCOPE,
        "evidence": [
            "The OpenAPI component diff directly shows both removed response properties and new replacements.",
            "Independent transitive fragment-reference review maps MFAFactorSchema to 14 operations and WebAuthnChallengeResponse to POST /factors/{factorId}/challenge.",
        ],
        "notes": ["Marked runtime_candidate for a later separately approved E3-C review."],
    },
    {
        "case_id": "rw_supabase_005_authorize_flow_type_removed",
        "repo_key": "supabase",
        "base": "19545601676675a923bb9850499717e89cf91a0b",
        "head": "f4c3cdb020e6808333ec460470f9e081f0d9bc93",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["request_parameter_removed"],
        "verdict": "fail",
        "breaking": [
            breaking(
                "request_parameter_removed",
                "GET /authorize documents optional query parameter flow_type.",
                "The flow_type query parameter is absent.",
                "Existing callers using a previously documented parameter no longer have that request represented by the Head contract.",
                operation="GET /authorize",
            )
        ],
        "contract_scope": ["GET /authorize"],
        "runtime_scope": ["GET /authorize"],
        "evidence": [
            "The parameter set difference for GET /authorize is exactly removal of ('flow_type', 'query').",
            "A signup example also drops flow_type, but examples are not used as breaking evidence.",
        ],
        "notes": ["Marked runtime_candidate for a later separately approved E3-C review."],
    },
    {
        "case_id": "rw_netbird_001_provider_validation_422_ambiguous",
        "repo_key": "netbird",
        "base": "ebc259e30b42e98f46952e9f61f80a09c6e4432f",
        "head": "6aaeed744e8c05f4974f6207a68dd18b00763f6c",
        "status": "ambiguous",
        "confidence": "medium",
        "change_types": ["response_status_added", "documentation_only", "mixed_change"],
        "verdict": None,
        "breaking": [],
        "contract_scope": [
            "POST /api/agent-network/providers",
            "PUT /api/agent-network/providers/{providerId}",
        ],
        "runtime_scope": [
            "POST /api/agent-network/providers",
            "PUT /api/agent-network/providers/{providerId}",
        ],
        "evidence": [
            "Both provider write operations add a documented 422 response.",
            "Descriptions state that new credential/upstream validation may now reject a write that would previously be stored.",
        ],
        "ambiguity_reason": "Adding a response status is structurally additive, while the accompanying behavior text describes a newly rejecting validation path; compatibility depends on whether behavioral acceptance is part of Ground Truth.",
        "notes": [
            "Excluded from headline accuracy until a human reviewer chooses the behavioral contract convention."
        ],
    },
    {
        "case_id": "rw_netbird_002_bundle_parameters_fields_added",
        "repo_key": "netbird",
        "base": "5e88d3f87afd6debab57e149bae754a3c46dcb75",
        "head": "1081ca006d46f26ea770602e2936012465d7163c",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["request_schema_changed", "response_property_added", "shared_component_changed"],
        "verdict": "pass",
        "breaking": [],
        "contract_scope": [],
        "runtime_scope": [],
        "evidence": [
            "BundleParameters adds optional anonymize_level and upload_url properties without changing its required list.",
            "The shared component is referenced by three peer-job operations, but no formerly valid request or response member is removed.",
        ],
        "notes": [],
    },
    {
        "case_id": "rw_netbird_003_policy_ports_documentation_only",
        "repo_key": "netbird",
        "base": "ebfdf7d7b8c6b475c66cd7790d2f2f2a5e9275e4",
        "head": "58c09ead211d2283f78b24bd9a4ccaa5a1a517e8",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["documentation_only", "shared_component_changed"],
        "verdict": "pass",
        "breaking": [],
        "contract_scope": [],
        "runtime_scope": [],
        "evidence": [
            "PolicyRuleMinimum and RulePortRange descriptions document mutual exclusivity and single-port encoding.",
            "The only non-description addition is vendor extension x-omit-from-example; validation keywords are unchanged.",
        ],
        "notes": [],
    },
    {
        "case_id": "rw_netbird_004_agent_network_settings_rework",
        "repo_key": "netbird",
        "base": "bc7a15ab71d9e9ed944fa3ec17952e77a696595d",
        "head": "ebfdf7d7b8c6b475c66cd7790d2f2f2a5e9275e4",
        "status": "confirmed",
        "confidence": "high",
        "change_types": [
            "request_schema_changed",
            "response_property_removed",
            "shared_component_changed",
            "endpoint_added",
            "mixed_change",
        ],
        "verdict": "fail",
        "breaking": [
            breaking(
                "request_property_removed",
                "AgentNetworkProviderRequest accepts bootstrap_cluster.",
                "bootstrap_cluster is removed.",
                "Provider create/update callers can no longer rely on the documented request property.",
                schema_name="AgentNetworkProviderRequest",
            ),
            breaking(
                "response_property_removed",
                "AgentNetworkSettings requires cluster and subdomain.",
                "cluster and subdomain are removed and replaced by proxy_address and dedicated.",
                "Existing settings-response consumers lose two documented fields.",
                schema_name="AgentNetworkSettings",
            ),
            breaking(
                "request_required_changed",
                "AgentNetworkSettingsRequest accepts cluster and requires only collection toggles.",
                "cluster is removed; endpoint, proxy_address, and access_log_retention_days become required.",
                "Existing valid settings updates may be rejected by the stricter request contract.",
                schema_name="AgentNetworkSettingsRequest",
            ),
        ],
        "contract_scope": NETBIRD_SETTINGS_SCOPE,
        "runtime_scope": NETBIRD_SETTINGS_SCOPE,
        "evidence": [
            "The component diff directly removes/renames request and response members.",
            "Independent transitive fragment-reference review maps the three breaking components to five Head operations.",
            "POST and DELETE /api/agent-network/settings are additions; only POST is also in scope because it uses changed settings components.",
        ],
        "notes": ["This is a mixed but localized adjacent commit, not a cross-release aggregate diff."],
    },
    {
        "case_id": "rw_netbird_005_proxy_cluster_response_reworked",
        "repo_key": "netbird",
        "base": "07cbfdbedec2804f2014c42049f23268b0dc2ec7",
        "head": "d250f92c435bac83fd55f00fad3ee2c292eee910",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["response_property_removed", "response_property_added", "shared_component_changed"],
        "verdict": "fail",
        "breaking": [
            breaking(
                "response_property_removed",
                "ProxyCluster requires boolean self_hosted.",
                "self_hosted is removed; type and online become required and capability flags are added.",
                "Existing cluster-list consumers lose the documented self_hosted field.",
                schema_name="ProxyCluster",
            )
        ],
        "contract_scope": ["GET /api/reverse-proxies/clusters"],
        "runtime_scope": ["GET /api/reverse-proxies/clusters"],
        "evidence": [
            "The ProxyCluster component diff removes self_hosted and adds ProxyClusterType plus capability fields.",
            "Independent fragment-reference review maps ProxyCluster only to GET /api/reverse-proxies/clusters.",
        ],
        "notes": [],
    },
    {
        "case_id": "rw_appwrite_001_empty_array_examples_preserved",
        "repo_key": "appwrite",
        "base": "698f5834d5662e2608393e32a7e68a2d18d4eef2",
        "head": "6124ab004f417a4279fdc9943c11b0b7ae5f9e73",
        "status": "confirmed",
        "confidence": "high",
        "change_types": ["documentation_only", "shared_component_changed"],
        "verdict": "pass",
        "breaking": [],
        "contract_scope": [],
        "runtime_scope": [],
        "evidence": [
            "The adjacent commit changes empty-array examples across 72 list schemas and does not add/remove operations.",
            "Schema types, properties, required lists, and validation constraints are unchanged.",
        ],
        "notes": ["Both artifacts are below the 5 MiB Guardian input limit."],
    },
    {
        "case_id": "rw_appwrite_002_migration_cutover_replaced",
        "repo_key": "appwrite",
        "base": "0aff819e4744849f2cb12a4fdf58cde50d3ad8ac",
        "head": "7e76b048625236d2cfd129b4d66f899c7010f551",
        "status": "probable",
        "confidence": "medium",
        "change_types": [
            "endpoint_removed",
            "endpoint_added",
            "response_schema_changed",
            "shared_component_changed",
            "mixed_change",
        ],
        "verdict": "fail",
        "breaking": [
            breaking(
                "endpoint_removed",
                "POST /tablesdb/{databaseId}/migrations/{migrationId}/cutover exists.",
                "The singular /cutover operation is removed and a plural /cutovers operation is added.",
                "Adding a replacement path does not preserve existing callers of the removed path.",
                operation="POST /tablesdb/{databaseId}/migrations/{migrationId}/cutover",
            ),
            breaking(
                "response_required_changed",
                "frameworkAdapter requires fallbackFile and does not allow null.",
                "fallbackFile is no longer required and becomes nullable.",
                "GET /sites/frameworks may now return a response shape that old clients did not account for.",
                schema_name="frameworkAdapter",
            ),
        ],
        "contract_scope": [
            "POST /tablesdb/{databaseId}/migrations/{migrationId}/cutover",
            "GET /sites/frameworks",
        ],
        "runtime_scope": ["GET /sites/frameworks"],
        "evidence": [
            "The OpenAPI path diff removes singular /cutover and adds plural /cutovers.",
            "The component diff removes fallbackFile from frameworkAdapter.required and adds nullable=true.",
            "Independent fragment-reference review maps frameworkAdapter to GET /sites/frameworks.",
        ],
        "notes": [
            "The Head artifact is 5,127,762 bytes, below but close to the 5,242,880-byte limit.",
            "Probable/medium reflects a mixed generated-spec commit even though the two cited breaking facts are explicit.",
        ],
    },
    {
        "case_id": "rw_appwrite_003_usage_project_and_health_update",
        "repo_key": "appwrite",
        "base": "8c00ba356b01d43fbc6c88c60fb2a4fc99e04b40",
        "head": "7d74bf9f3c0c990745e822f5935bcfaca310fd06",
        "status": "probable",
        "confidence": "medium",
        "change_types": ["endpoint_removed", "response_schema_changed", "shared_component_changed", "mixed_change"],
        "verdict": "fail",
        "breaking": [
            breaking(
                "endpoint_removed",
                "GET /health/queue/builds-priority exists.",
                "The operation is absent.",
                "Existing callers can no longer invoke the documented health operation.",
                operation="GET /health/queue/builds-priority",
            ),
            breaking(
                "response_shape_replaced",
                "usageEventList and usageGaugeList expose metric plus groups.",
                "Both replace metric/groups with a metrics collection.",
                "The response object shape changes for existing usage consumers.",
                schema_name="usageEventList / usageGaugeList",
            ),
            breaking(
                "response_required_changed",
                "project requires multiple OAuth2 server fields.",
                "Those fields remain properties but are removed from required and become nullable.",
                "Project responses may omit or null fields that old consumers could treat as always present.",
                schema_name="project",
            ),
        ],
        "contract_scope": [
            *APPWRITE_PROJECT_SCOPE,
            "GET /usage/events",
            "GET /usage/gauges",
            "GET /health/queue/builds-priority",
        ],
        "runtime_scope": [
            *APPWRITE_PROJECT_SCOPE,
            "GET /usage/events",
            "GET /usage/gauges",
        ],
        "evidence": [
            "The operation set diff removes GET /health/queue/builds-priority.",
            "The two usage response components remove metric/groups and add metrics.",
            "The project required-list diff removes the OAuth2 server fields and the same fields gain nullable=true.",
            "Independent transitive fragment-reference review maps project to 24 operations and each usage list to one operation.",
        ],
        "notes": ["Probable/medium reflects a mixed generated-spec commit with several independent contract facts."],
    },
]


def sha256_bytes(data: bytes) -> str:
    """返回原始字节的 SHA256。."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """流式计算文件 SHA256，避免读取旧大型 raw artifact 到内存。."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(git_executable: str, worktree: Path, *args: str) -> str:
    """在只读外部仓库中执行 Git 元数据命令。."""
    result = subprocess.run(
        [git_executable, "-C", str(worktree), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def fetch_spec(slug: str, revision: str, path: str) -> bytes:
    """从不可变 40-char revision 读取 Spec；不使用 GitHub REST。."""
    encoded_path = urllib.parse.quote(path, safe="/")
    url = f"https://raw.githubusercontent.com/{slug}/{revision}/{encoded_path}"
    request = urllib.request.Request(url, headers={"User-Agent": "api-guardian-e3-benchmark"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        data = response.read(MAX_SPEC_BYTES + 1)
    if len(data) > MAX_SPEC_BYTES:
        raise ValueError(f"spec exceeds 5 MiB: {slug}@{revision}:{path}")
    return data


def parse_spec(data: bytes, path: str) -> dict[str, Any]:
    """按真实后缀解析 JSON/YAML，并验证 E3-B eligibility。."""
    parsed = json.loads(data) if path.endswith(".json") else yaml.safe_load(data)
    if not isinstance(parsed, dict):
        raise ValueError("OpenAPI document must be an object")
    version = parsed.get("openapi")
    if not isinstance(version, str) or not version.startswith("3."):
        raise ValueError(f"unsupported OpenAPI version: {version!r}")
    return parsed


def collect_external_refs(value: Any) -> list[str]:
    """收集非 fragment-local 的 $ref，E3-B 对此直接排除。."""
    refs: set[str] = set()
    if isinstance(value, dict):
        ref = value.get("$ref")
        if isinstance(ref, str) and not ref.startswith("#"):
            refs.add(ref)
        for child in value.values():
            refs.update(collect_external_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(collect_external_refs(child))
    return sorted(refs)


def operation_set(spec: dict[str, Any]) -> set[str]:
    """提取规范化 METHOD + path operation 集合。."""
    operations: set[str] = set()
    for path, path_item in (spec.get("paths") or {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in path_item:
            if method.lower() in HTTP_METHODS:
                operations.add(f"{method.upper()} {path}")
    return operations


def default_branch(git_executable: str, worktree: Path) -> str:
    """从固定 clone 的 fetch refspec 解析默认分支。."""
    fetch = git(git_executable, worktree, "config", "--get", "remote.origin.fetch")
    source = fetch.split(":", maxsplit=1)[0]
    return source.rsplit("/", maxsplit=1)[-1]


def json_bytes(value: Any) -> bytes:
    """生成稳定、可审计的 UTF-8 JSON。."""
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def write_bytes(path: Path, data: bytes) -> None:
    """写入 Benchmark artifact。."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def verify_frozen_inputs(candidate_manifest: dict[str, Any]) -> tuple[dict[str, str], dict[str, str]]:
    """确认 production Core 和 E2/E2.5 raw artifact 未变化。."""
    expected_core = candidate_manifest["production_core_hashes_after"]
    current_core = {path: sha256_file(ROOT / path) for path in CORE_PATHS}
    if current_core != expected_core:
        raise RuntimeError("production Core hash mismatch before Ground Truth freeze")

    expected_prior = candidate_manifest["frozen_benchmark_artifacts"]
    current_prior_by_manifest_key = {
        manifest_path: sha256_file(ROOT / actual_path) for manifest_path, actual_path in PRIOR_ARTIFACT_PATHS
    }
    if current_prior_by_manifest_key != expected_prior:
        raise RuntimeError("E2/E2.5 artifact hash mismatch before Ground Truth freeze")
    return {
        **current_core,
    }, {
        actual_path: current_prior_by_manifest_key[manifest_path]
        for manifest_path, actual_path in PRIOR_ARTIFACT_PATHS
    }


def build_records(git_executable: str) -> tuple[list[PreSystemReviewRecord], list[RepositorySnapshot]]:
    """从人工 Case 定义和独立 source evidence 构建已验证记录。."""
    cache: dict[tuple[str, str], tuple[bytes, dict[str, Any]]] = {}
    records: list[PreSystemReviewRecord] = []
    pair_counts = Counter(case["repo_key"] for case in CASES)

    for seed in CASES:
        repo = REPOSITORIES[seed["repo_key"]]
        worktree = WORKTREE_ROOT / repo["worktree"]
        base = seed["base"]
        head = seed["head"]
        spec_history = git(
            git_executable,
            worktree,
            "log",
            "--format=%H",
            "-n",
            "2",
            head,
            "--",
            repo["spec_path"],
        ).splitlines()
        if spec_history != [head, base]:
            raise ValueError(f"pair is not adjacent in path-scoped Spec history: {seed['case_id']}")

        for revision in (base, head):
            key = (seed["repo_key"], revision)
            if key not in cache:
                raw = fetch_spec(repo["slug"], revision, repo["spec_path"])
                cache[key] = (raw, parse_spec(raw, repo["spec_path"]))

        base_raw, base_spec = cache[(seed["repo_key"], base)]
        head_raw, head_spec = cache[(seed["repo_key"], head)]
        base_refs = collect_external_refs(base_spec)
        head_refs = collect_external_refs(head_spec)
        if base_refs or head_refs:
            raise ValueError(f"external refs make pair ineligible: {seed['case_id']}")

        base_ops = operation_set(base_spec)
        head_ops = operation_set(head_spec)
        contract_scope = list(seed["contract_scope"])
        runtime_scope = list(seed["runtime_scope"])
        if not set(contract_scope) <= base_ops | head_ops:
            raise ValueError(f"unknown contract operation: {seed['case_id']}")
        if not set(runtime_scope) <= head_ops:
            raise ValueError(f"non-executable runtime operation: {seed['case_id']}")

        commit_meta = git(git_executable, worktree, "show", "-s", "--format=%aI%x00%s", head)
        commit_date, commit_message = commit_meta.split("\x00", maxsplit=1)
        records.append(
            PreSystemReviewRecord(
                case_id=seed["case_id"],
                repo=repo["repo"],
                license=repo["license"],
                base_commit=base,
                head_commit=head,
                base_spec_path=repo["spec_path"],
                head_spec_path=repo["spec_path"],
                base_spec_sha256=sha256_bytes(base_raw),
                head_spec_sha256=sha256_bytes(head_raw),
                base_spec_bytes=len(base_raw),
                head_spec_bytes=len(head_raw),
                base_openapi_version=str(base_spec["openapi"]),
                head_openapi_version=str(head_spec["openapi"]),
                base_operation_count=len(base_ops),
                head_operation_count=len(head_ops),
                commit_date_head=commit_date,
                commit_message_head=commit_message,
                review_status=ReviewStatus(seed["status"]),
                review_confidence=ReviewConfidence(seed["confidence"]),
                change_types=seed["change_types"],
                ground_truth_contract_verdict=(ContractVerdict(seed["verdict"]) if seed["verdict"] else None),
                breaking_changes=[BreakingChangeEvidence(**item) for item in seed["breaking"]],
                contract_affected_operations=contract_scope,
                runtime_executable_operations=runtime_scope,
                evidence=seed["evidence"],
                ambiguity_reason=seed.get("ambiguity_reason"),
                notes=seed["notes"],
                external_refs=[],
            )
        )

    snapshots: list[RepositorySnapshot] = []
    for key, repo in REPOSITORIES.items():
        worktree = WORKTREE_ROOT / repo["worktree"]
        remote_url = git(git_executable, worktree, "config", "--get", "remote.origin.url")
        if remote_url.rstrip("/") != repo["remote_url"].rstrip("/"):
            raise ValueError(f"unexpected remote URL for {repo['repo']}: {remote_url}")
        snapshots.append(
            RepositorySnapshot(
                repo=repo["repo"],
                remote_url=remote_url,
                default_branch=default_branch(git_executable, worktree),
                clone_timestamp=repo["clone_timestamp"],
                clone_head_sha=git(git_executable, worktree, "rev-parse", "HEAD"),
                local_checkout_mode="partial clone, blob:none, no checkout, single branch",
                spec_path=repo["spec_path"],
                selected_pairs=pair_counts[key],
                runtime_candidate=repo["runtime_candidate"],
            )
        )
    return records, snapshots


def checkpoint_markdown(records: list[PreSystemReviewRecord], manifest: GroundTruthFreezeManifest) -> str:
    """生成人工确认前的实验检查点记录。."""
    rows = []
    for record in records:
        verdict = record.ground_truth_contract_verdict.value if record.ground_truth_contract_verdict else "ambiguous"
        rows.append(
            "| "
            + " | ".join(
                [
                    record.repo,
                    record.case_id,
                    record.base_commit[:12],
                    record.head_commit[:12],
                    ", ".join(record.change_types),
                    record.review_status.value,
                    record.review_confidence.value,
                    verdict,
                    str(len(record.contract_affected_operations)),
                    str(len(record.runtime_executable_operations)),
                ]
            )
            + " |"
        )

    counts = manifest.review_counts
    return "\n".join(
        [
            "# API Guardian E3-B Ground Truth Freeze Checkpoint",
            "",
            "> 状态：Pre-system review 已冻结；尚未运行 oasdiff、Guardian、Schemathesis、LLM 或准确率指标。",
            "",
            "## Material Passport",
            "",
            "- 审查者：`ai_assisted_initial_review`",
            "- 证据来源：固定 40-char Git commit、Base/Head OpenAPI、Git commit message；未读取 Guardian 输出。",
            "- Spec 获取：本地 partial clone 提供 commit/tree 元数据；由于 Git blob 回填连接被重置，artifact 通过 raw.githubusercontent.com 的固定 commit URL 只读获取。",
            f"- 冻结时间：`{manifest.frozen_at}`",
            f"- Review aggregate SHA256：`{manifest.aggregate_review_sha256}`",
            "- 人工确认状态：尚未 human-confirmed，等待用户审查本检查点。",
            "",
            "## Counts",
            "",
            f"- Total: {counts['total']}",
            f"- Confirmed: {counts['confirmed']}",
            f"- Probable: {counts['probable']}",
            f"- Ambiguous: {counts['ambiguous']}",
            f"- Excluded: {counts['excluded']}",
            f"- Headline eligible before human confirmation: {counts['headline_eligible']}",
            "",
            "## Cases",
            "",
            "| Repo | Case ID | Base | Head | Change types | Status | Confidence | Verdict | Contract ops | Executable ops |",
            "|---|---|---|---|---|---|---|---|---:|---:|",
            *rows,
            "",
            "## Freeze Boundary",
            "",
            "`ground_truth_freeze_manifest.json` 已记录每个 review 文件 SHA256。所有 System Output 目标文件在冻结时不存在。",
            "在用户确认前，本阶段不得运行 Guardian accuracy metric。",
            "",
        ]
    )


def main() -> None:
    """生成 Ground Truth artifacts，并在任何 System Output 前完成冻结。."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--git", required=True, dest="git_executable")
    args = parser.parse_args()
    console = Console()

    candidate_manifest = json.loads((DATASET_ROOT / "candidates.json").read_text(encoding="utf-8"))
    core_hashes, prior_hashes = verify_frozen_inputs(candidate_manifest)
    existing_system_outputs = [path for path in SYSTEM_OUTPUT_PATHS if (ROOT / path).exists()]
    if existing_system_outputs:
        raise RuntimeError(f"system output exists before Ground Truth freeze: {existing_system_outputs}")

    records, repositories = build_records(args.git_executable)
    if not 17 <= len(records) <= 23:
        raise ValueError("E3-B must freeze 17-23 cases")
    if len({record.repo for record in records}) != 4:
        raise ValueError("E3-B must use exactly four approved primary repositories")

    REVIEW_ROOT.mkdir(parents=True, exist_ok=True)
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    artifacts: list[FrozenArtifact] = []
    for old_record in REVIEW_ROOT.glob("*.json"):
        old_record.unlink()
    for record in records:
        path = REVIEW_ROOT / f"{record.case_id}.json"
        data = json_bytes(record.model_dump(mode="json"))
        write_bytes(path, data)
        artifacts.append(
            FrozenArtifact(
                path=path.relative_to(ROOT).as_posix(),
                sha256=sha256_bytes(data),
                bytes=len(data),
            )
        )

    review_lines = b"".join(
        json.dumps(record.model_dump(mode="json"), ensure_ascii=False, sort_keys=True).encode() + b"\n"
        for record in records
    )
    review_jsonl = REPORT_ROOT / "review_records.jsonl"
    write_bytes(review_jsonl, review_lines)
    artifacts.append(
        FrozenArtifact(
            path=review_jsonl.relative_to(ROOT).as_posix(),
            sha256=sha256_bytes(review_lines),
            bytes=len(review_lines),
        )
    )

    case_rows = [
        {
            "case_id": record.case_id,
            "repo": record.repo,
            "license": record.license,
            "base_commit": record.base_commit,
            "head_commit": record.head_commit,
            "base_spec_path": record.base_spec_path,
            "head_spec_path": record.head_spec_path,
            "base_spec_sha256": record.base_spec_sha256,
            "head_spec_sha256": record.head_spec_sha256,
            "base_operation_count": record.base_operation_count,
            "head_operation_count": record.head_operation_count,
            "review_status": record.review_status.value,
            "review_confidence": record.review_confidence.value,
        }
        for record in records
    ]
    cases_bytes = b"".join(json.dumps(row, ensure_ascii=False, sort_keys=True).encode() + b"\n" for row in case_rows)
    cases_path = REPORT_ROOT / "real_world_cases.jsonl"
    write_bytes(cases_path, cases_bytes)
    artifacts.append(
        FrozenArtifact(
            path=cases_path.relative_to(ROOT).as_posix(),
            sha256=sha256_bytes(cases_bytes),
            bytes=len(cases_bytes),
        )
    )

    selected_repos_path = REPORT_ROOT / "selected_repos.json"
    selected_repos_bytes = json_bytes([repo.model_dump(mode="json") for repo in repositories])
    write_bytes(selected_repos_path, selected_repos_bytes)

    status_counts = Counter(record.review_status.value for record in records)
    review_counts = {
        "total": len(records),
        "confirmed": status_counts["confirmed"],
        "probable": status_counts["probable"],
        "ambiguous": status_counts["ambiguous"],
        "excluded": status_counts["excluded"],
        "headline_eligible": sum(
            record.review_status in {ReviewStatus.CONFIRMED, ReviewStatus.PROBABLE}
            and record.review_confidence != ReviewConfidence.LOW
            for record in records
        ),
    }
    repo_counts = Counter(record.repo for record in records)
    aggregate = sha256_bytes(
        "\n".join(
            f"{artifact.path}:{artifact.sha256}" for artifact in sorted(artifacts, key=lambda item: item.path)
        ).encode()
    )

    root_git_commit = git(args.git_executable, ROOT, "rev-parse", "HEAD")
    git_status = git(args.git_executable, ROOT, "status", "--porcelain")
    frozen_at = datetime.now(UTC).isoformat()
    manifest = GroundTruthFreezeManifest(
        frozen_at=frozen_at,
        reviewer_basis="AI-assisted initial review; no record is represented as human-confirmed at this checkpoint.",
        material_passport={
            "guardian_git_commit": root_git_commit,
            "guardian_worktree_status": "dirty" if git_status else "clean",
            "git_version": git(args.git_executable, ROOT, "--version"),
            "spec_materialization": "raw.githubusercontent.com exact 40-char commit URL",
            "commit_metadata": "local partial Git clones; no GitHub REST",
            "ground_truth_sources": "Base OpenAPI, Head OpenAPI, Git diff facts, commit message",
            "system_output_used": "none",
            "e2_path_note": "E3-A labeled the E2 runtime artifact runtime_raw_results.jsonl; the unchanged file on disk is latest/raw_results.jsonl and its expected SHA256 matches.",
        },
        repositories=repositories,
        review_artifacts=sorted(artifacts, key=lambda item: item.path),
        aggregate_review_sha256=aggregate,
        review_counts=review_counts,
        repository_pair_counts=dict(sorted(repo_counts.items())),
        production_core_hashes=core_hashes,
        frozen_prior_artifact_hashes=prior_hashes,
        guardian_system_output_status="not_generated_before_ground_truth_freeze",
        guardian_output_paths_absent_at_freeze=list(SYSTEM_OUTPUT_PATHS),
    )
    manifest_path = REPORT_ROOT / "ground_truth_freeze_manifest.json"
    write_bytes(manifest_path, json_bytes(manifest.model_dump(mode="json")))

    checkpoint_path = REPORT_ROOT / "ground_truth_review_checkpoint.md"
    write_bytes(checkpoint_path, checkpoint_markdown(records, manifest).encode("utf-8"))
    console.print(
        f"[green]Frozen {len(records)} pre-system review records[/green] "
        f"(aggregate={manifest.aggregate_review_sha256})"
    )
    console.print("[yellow]Guardian system output was not generated. Await user confirmation.[/yellow]")


if __name__ == "__main__":
    main()
