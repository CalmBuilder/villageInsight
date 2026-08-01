export type Batch = {
  id: string;
  tenant_id: string;
  administrative_unit_id: string;
  created_by_user_id: string;
  name: string;
  source_kind: string;
  status: string;
  total_files: number;
  completed_files: number;
  failed_files: number;
  deleted_files: number;
  upload_failures?: string[];
  created_at: string;
  updated_at: string;
};

export type CurrentUser = {
  user_id: string;
  username: string;
  display_name: string;
  tenant_id: string;
  tenant_name: string;
  membership_id: string;
  role: "tenant_admin" | "village_operator" | "platform_admin";
  scope_unit_id: string | null;
  scope_unit_name: string | null;
  scope_unit_type: "township" | "village" | null;
  include_descendants: boolean;
  permissions: string[];
  upload_units: Array<{
    id: string;
    name: string;
    tenant_id: string;
    tenant_name: string;
  }>;
};

export type AdministrativeUnit = {
  id: string;
  tenant_id: string;
  parent_id: string | null;
  unit_type: "township" | "village";
  name: string;
  status: "active" | "disabled";
};

export type ManagedTenant = {
  id: string;
  name: string;
  kind: "business" | "platform";
  status: "active" | "disabled";
  created_at: string;
  units: AdministrativeUnit[];
};

export type ManagedUser = {
  user_id: string;
  username: string;
  display_name: string;
  user_status: "active" | "disabled";
  tenant_id: string;
  tenant_name: string;
  tenant_kind: "business" | "platform";
  membership_id: string;
  membership_status: "active" | "disabled";
  role: CurrentUser["role"];
  scope_unit_id: string | null;
  scope_unit_name: string | null;
};

export type ManagedTenantPage = {
  items: ManagedTenant[];
  total: number;
  limit: number;
  offset: number;
};

export type ManagedUserPage = {
  items: ManagedUser[];
  total: number;
  limit: number;
  offset: number;
};

export type BatchItem = {
  id: string;
  batch_id: string;
  original_name: string;
  relative_path: string | null;
  size_bytes: number;
  status: string;
  evidence_status: string;
  formal_import_status: string;
  parser_name: string | null;
  error_code: string | null;
  error_message: string | null;
  build_result_deletion_status:
    | "active"
    | "deletion_pending"
    | "deleting"
    | "deleted"
    | "deletion_failed";
  build_result_deleted_at: string | null;
  build_result_deleted_by_user_id: string | null;
  created_at: string;
  updated_at: string;
};

export type FileLedgerItem = BatchItem & {
  tenant_id: string;
  tenant_name: string;
  administrative_unit_id: string;
  administrative_unit_name: string;
  created_by_user_id: string;
  created_by_display_name: string;
  batch_name: string;
  batch_source_kind: string;
  match_type: "exact" | "partial" | "none" | null;
  score_basis_points: number | null;
  requires_hermes: boolean | null;
  total_regions: number | null;
  matched_regions: number | null;
  coverage_basis_points: number | null;
  hermes_call_count: number;
  record_count: number;
  partial_record_count: number;
  governance_pending: boolean;
  sheet_count: number | null;
};

export type FileLedgerPage = {
  items: FileLedgerItem[];
  total: number;
  limit: number;
  offset: number;
  counts: Record<
    "all" | "imported" | "partial" | "processing" | "hermes" | "review" | "failed",
    number
  >;
};

export type BuildResultDeletion = {
  id: string;
  item_id: string;
  status: "pending" | "deleting" | "completed" | "failed";
  deleted_counts: Record<string, number>;
  retired_counts: Record<string, number>;
  error_code: string | null;
  requested_at: string;
  completed_at: string | null;
};

export type TemplateMatch = {
  item_id: string;
  source_sha256: string;
  profile_contract_version: string;
  layout_fingerprint: string;
  match_type: "exact" | "partial" | "none";
  score_basis_points: number;
  template_id: string | null;
  template_version: number | null;
  differences: {
    missing_headers?: string[];
    new_headers?: string[];
    unmatched_regions?: Array<{
      sheet_id: string;
      region_id: string;
      header_id: string;
      match_type: "partial" | "none";
      score_basis_points: number;
    }>;
    workbook_fast_route?: boolean;
  };
  requires_hermes: boolean;
  matcher_version: string;
  total_regions: number;
  matched_regions: number;
  coverage_basis_points: number;
};

export type RegionTemplateMatch = {
  id: string;
  item_id: string;
  sheet_id: string;
  region_id: string;
  header_id: string;
  region_fingerprint: string;
  match_type: "exact" | "partial" | "none";
  score_basis_points: number;
  template_region_component_id: string | null;
  template_id: string | null;
  template_version: number | null;
  region_template_id: string | null;
  region_template_version: number | null;
  differences: {
    missing_headers?: string[];
    new_headers?: string[];
    structural_mismatches?: string[];
  };
  requires_hermes: boolean;
  matcher_version: string;
};

export type FieldMatch = {
  id: string;
  item_id: string;
  sheet_id: string;
  region_id: string;
  header_id: string;
  source_column_id: string;
  header_path: string[];
  observed_data_type: string | null;
  semantic_field_code: string | null;
  semantic_field_version: number | null;
  match_type: "exact" | "partial" | "none";
  score_basis_points: number;
  context: {
    domain?: string | null;
    record_type?: string | null;
    header_parent?: string[];
    base_label?: string | null;
    concept_key?: string | null;
    role?: string | null;
    role_evidence?: string | null;
  };
  differences: {
    ambiguous?: boolean;
    candidates?: Array<{
      semantic_field_code: string;
      semantic_field_version: number;
      score_basis_points: number;
      reasons: string[];
      compatible_roles?: string[];
    }>;
  };
  requires_hermes: boolean;
  matcher_version: string;
};

export type TemplateProposal = {
  id: string;
  tenant_id: string | null;
  administrative_unit_id: string | null;
  created_by_user_id: string | null;
  source: string;
  source_item_id: string;
  model_name: string | null;
  prompt_version: string | null;
  proposal: {
    template_suggestion?: {
      template_code: string;
      template_name: string;
      domain: string;
      record_type: string;
      confidence: number;
      evidence_ids: string[];
    } | null;
    record_grain?: { value: string; confidence: number };
    field_decisions?: Array<{
      source_column_id: string;
      action: string;
      semantic_field_code?: string | null;
      proposed_field_code?: string | null;
      confidence: number;
    }>;
  };
  status: "pending" | "accepted" | "rejected";
  resolution_comment: string;
  resolved_by_user_id: string | null;
};

export type ReviewQueueItem = {
  proposal_id: string;
  batch_id: string;
  batch_name: string;
  tenant_id: string;
  tenant_name: string;
  administrative_unit_id: string;
  administrative_unit_name: string;
  created_by_user_id: string;
  created_by_display_name: string;
  item_id: string;
  file_name: string;
  relative_path: string | null;
  match_type: "exact" | "partial" | "none";
  score_basis_points: number;
  confidence: number | null;
  reason_codes: string[];
  proposal: TemplateProposal["proposal"];
  matched_template_code: string | null;
  matched_template_name: string | null;
  matched_domain: string | null;
  matched_record_type: string | null;
  matched_record_grain: string | null;
  formal_import_status: string;
  governance_issue_codes: string[];
  review_kind: "field" | "structure";
  field_evidence: ReviewFieldEvidence[];
  field_count: number;
  created_at: string;
};

export type ReviewQueuePage = {
  items: ReviewQueueItem[];
  total: number;
  limit: number;
  offset: number;
};

export type ReviewFieldEvidence = {
  source_column_id: string;
  sheet_id: string;
  sheet_name: string;
  region_id: string;
  column_index: number;
  column_coordinate: string;
  header_path: string[];
  parent_path: string[];
  leaf_header: string;
  observed_data_type: string | null;
  match_type: string;
  score_basis_points: number;
  candidates: Array<{
    semantic_field_code: string;
    semantic_field_version: number;
    score_basis_points: number;
    reasons: string[];
  }>;
  hermes_suggestion: {
    action?: string;
    semantic_field_code?: string | null;
    proposed_field_code?: string | null;
    layer?: "base" | "domain" | null;
    data_type?: string | null;
    unit?: string | null;
    role?: string | null;
    confidence?: number;
  };
  requires_resolution: boolean;
};

export type GovernanceFieldResolution = {
  source_column_id: string;
  mode: "reuse_existing" | "create_new" | "ignore";
  semantic_field_code: string | null;
  expected_field_version: number | null;
  learn_alias: string | null;
  learn_path: boolean;
  role: string | null;
  unit: string | null;
  new_field_code: string | null;
  new_field_name: string | null;
  new_field_layer: "base" | "domain" | null;
  new_field_data_type: string | null;
  ignore_scope: "file" | "context" | null;
  ignore_reason: string | null;
};

export type WorkerCapacity = {
  lanes: {
    parse: number;
    hermes: number;
    materialize: number;
  };
  queued: {
    parse: number;
    hermes: number;
    materialize: number;
  };
  running: {
    parse: number;
    hermes: number;
    materialize: number;
  };
  resources: {
    available_memory_mb: number | null;
    total_memory_mb: number | null;
    admission_floor_mb: number;
    admission_paused: boolean;
  };
};

export type WorkbookProfile = {
  contract_version: "workbook-profile/v2";
  workbook_id: string;
  source_sha256: string;
  parser_name: string;
  parser_version: string;
  warnings: string[];
  detection: {
    format: "xlsx" | "xls" | "csv";
    signature: string;
    extension_matches: boolean;
    warnings: string[];
  };
  sheets: Array<{
    id: string;
    name: string;
    cells: unknown[];
    merges: unknown[];
    region_candidates: unknown[];
    header_candidates: unknown[];
    warnings: string[];
  }>;
};

export type SemanticField = {
  id: string;
  code: string;
  name: string;
  description: string;
  layer: "base" | "domain";
  data_type: string;
  unit_dimension: string | null;
  aliases: string[];
  validators: Array<Record<string, unknown>>;
  source: string;
  source_metadata: Record<string, unknown>;
  variants: Array<{
    id: string;
    kind: "alias" | "header_path" | "role_context";
    alias: string | null;
    header_path: string[];
    role: string | null;
    domain: string | null;
    record_type: string | null;
    source: string;
    confidence_basis_points: number;
  }>;
  variant_count?: number;
  version: number;
  status: string;
  published_version: number | null;
};

export type SemanticFieldDetail = {
  field: SemanticField;
  versions: Array<{
    version: number;
    status: string;
    name: string;
    description: string;
    layer: "base" | "domain";
    data_type: string;
    unit_dimension: string | null;
    alias_count: number;
    variant_count: number;
    source: string;
    source_metadata: Record<string, unknown>;
    created_at: string;
  }>;
  referenced_by: Array<{
    template_id: string;
    template_code: string;
    template_name: string;
    template_version: number;
    template_status: string;
  }>;
};

export type CatalogDirectoryPage<T> = {
  items: T[];
  counts: Record<string, number>;
  total: number;
  limit: number;
  offset: number;
};

export type RegionTemplate = {
  id: string;
  code: string;
  name: string;
  description: string;
  version: number;
  status: string;
  region_fingerprint: string;
  published_version: number | null;
  definition: {
    domain: string;
    region_kind: "table" | "form" | "matrix";
    record_type: string;
    record_grain: string;
    header_signature: string[][];
    field_bindings: Array<Record<string, unknown>>;
  };
  source: string;
};

export type RegionSourcePreview = {
  template_id: string;
  template_name: string;
  source_file: string;
  source_location: string;
  sheet_name: string;
  sheet_index: number;
  source_range: string;
  header_rows: number[];
  layout_mode: string;
  evidence_count: number;
  columns: Array<{
    excel_column: string;
    column_number: number | null;
    header_path: string[];
    source_header: string;
    sample_values: string[];
    semantic_field_code: string;
    semantic_field_name: string;
    match_status: string;
    role: string | null;
  }>;
  warning: string | null;
};

export type SheetComposition = {
  id: string;
  code: string;
  name: string;
  description: string;
  version: number;
  status: string;
  composition_fingerprint: string;
  published_version: number | null;
  region_slots: Array<{
    slot_key: string;
    region_template_id: string;
    region_template_version: number;
    ordinal: number;
    required: boolean;
    cardinality: "one" | "zero_or_one" | "one_or_more";
    materialize: boolean;
  }>;
  source: string;
};

export type WorkbookRoute = {
  id: string;
  code: string;
  name: string;
  description: string;
  version: number;
  status: string;
  route_fingerprint: string;
  published_version: number | null;
  sheet_slots: Array<{
    slot_key: string;
    sheet_composition_id: string;
    sheet_composition_version: number;
    ordinal: number;
    required: boolean;
    cardinality: "one" | "zero_or_one" | "one_or_more";
    materialize: boolean;
  }>;
  source: string;
  source_metadata: {
    members?: Array<{
      representative_path?: string;
      source_paths?: string[];
    }>;
  };
};

export type WorkbookRouteSourcePreview = {
  route_id: string;
  route_name: string;
  source_file_count: number;
  source_files: Array<{
    name: string;
    location: string;
  }>;
  sheets: Array<{
    sheet_index: number;
    sheet_name: string;
    table_count: number;
    required: boolean;
  }>;
  warning: string | null;
};

export type Template = {
  id: string;
  code: string;
  name: string;
  description: string;
  version: number;
  status: string;
  layout_fingerprint: string;
  published_version: number | null;
};

export type MetricAnswer = {
  metric_code: string;
  metric_version?: number;
  metric_name: string;
  result_grade?: "official_metric" | "bounded_sql";
  value: number | string | null;
  unit: string | null;
  aggregation: string;
  record_count: number;
  source_file_count: number;
  query_plan: string;
};

export type QuestionConversation = {
  id: string;
  title: string;
  status: string;
  scope_name: string;
  scope_unit_id: string;
  scope_mode: "all_villages" | "village";
  source_item_id: string | null;
  source_name: string | null;
  run_count: number;
  created_at: string;
  updated_at: string;
};

export type QuestionConversationPage = {
  items: QuestionConversation[];
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
};

export type QuestionToolTrace = {
  tool_call_id?: string;
  tool_name: string;
  label: string;
  status: "running" | "completed" | "error";
  result_type?: string | null;
  row_count?: number | null;
  record_count?: number | null;
  source_file_count?: number | null;
  data_village_count?: number | null;
  duration_ms?: number | null;
  message?: string | null;
};

export type QuestionAnswer = {
  status?: "success" | "error";
  tool?: string;
  result_type?: "metric" | "table" | "record";
  result_grade?:
    | "official_metric"
    | "contract_query"
    | "bounded_plan"
    | "bounded_sql";
  metric?: MetricAnswer;
  value?: number | string | null;
  aggregation?: string | null;
  columns?: string[];
  rows?: Array<Record<string, unknown>>;
  row_count?: number;
  truncated?: boolean;
  evidence_summary?: {
    record_count?: number;
    source_file_count?: number;
    data_village_count?: number;
  };
  message?: string;
};

export type QuestionRun = {
  id: string;
  retry_of_run_id: string | null;
  question: string;
  answer_text: string;
  answer: QuestionAnswer;
  status: string;
  route: string;
  source_item_id: string | null;
  tool_trace: QuestionToolTrace[];
  evidence: Array<{
    record_count?: number;
    source_file_count?: number;
    data_village_count?: number;
  }>;
  error_code: string | null;
  started_at: string;
  created_at: string;
  completed_at: string | null;
};

export type QuestionConversationDetail = {
  conversation: QuestionConversation;
  runs: QuestionRun[];
  run_total: number;
  has_more_before: boolean;
};

export type QuestionStreamEvent = {
  event: string;
  sequence: number;
  run_id: string;
  conversation_id: string;
  message?: string;
  started_at?: string;
  delta?: string;
  content?: string;
  answer?: QuestionAnswer;
  tool_call_id?: string;
  label?: string;
  status?: string;
  result_type?: string | null;
  row_count?: number | null;
  record_count?: number | null;
  source_file_count?: number | null;
  data_village_count?: number | null;
  duration_ms?: number | null;
  completed_at?: string;
  question?: string;
  choices?: string[];
  error_code?: string;
  active?: boolean;
};

export type QuestionSource = {
  id: string;
  file_name: string;
  administrative_unit_id: string;
  administrative_unit_name: string;
  record_count: number;
  updated_at: string;
  is_default: boolean;
  superseded_by_item_id: string | null;
};

export type QuestionSourcePage = {
  items: QuestionSource[];
  page: number;
  page_size: number;
  total: number;
  default_total: number;
  total_pages: number;
};

export type LLMConfiguration = {
  provider: string;
  preset_id: string;
  api_mode: "openai_chat" | "anthropic_messages";
  model: string;
  fast_model: string;
  reasoning_model: string;
  base_url: string;
  thinking_protocol: "none" | "deepseek";
  api_key_configured: boolean;
  api_key_hint: string | null;
  api_key_reentry_required: boolean;
  max_tokens: number | null;
  source: string;
};

export type LLMProviderPreset = {
  id: string;
  name: string;
  provider: string;
  api_mode: "openai_chat" | "anthropic_messages";
  base_url: string;
  default_model: string;
  fast_model: string;
  reasoning_model: string;
  supports_model_discovery: boolean;
  description: string;
  billing_notice: string | null;
  api_key_configured: boolean;
  api_key_hint: string | null;
  api_key_reentry_required: boolean;
};

export type LLMConfigurationInput = {
  provider: string;
  preset_id: string;
  api_mode: "openai_chat" | "anthropic_messages";
  model: string;
  fast_model: string;
  reasoning_model: string;
  base_url: string;
  thinking_protocol: "none" | "deepseek";
  api_key: string | null;
  max_tokens: number | null;
};

export type MetricDefinition = {
  id: string;
  code: string;
  name: string;
  semantic_field_code: string;
  semantic_field_version: number;
  aggregation: string;
  unit: string | null;
  allowed_filter_fields: string[];
  aliases: string[];
  enabled: boolean;
};

export type DatasetRecord = {
  id: string;
  tenant_id: string;
  administrative_unit_id: string;
  ingestion_batch_id: string;
  item_id: string;
  source_file_name: string;
  administrative_unit_name: string;
  approved_plan_id: string;
  plan_source: string;
  template_id: string | null;
  template_version: number | null;
  region_template_id: string | null;
  region_template_version: number | null;
  record_type: string;
  sheet_id: string;
  region_id: string;
  source_row: number;
  raw_data: Record<string, unknown>;
  semantic_data: Record<string, unknown>;
  mapping_status: string;
  quality_status: string;
  created_at: string;
};

export type DatasetRecordPage = {
  items: DatasetRecord[];
  total: number;
  limit: number;
  offset: number;
};

export type DatasetRecordGroup = {
  item_id: string;
  source_file_name: string;
  administrative_unit_name: string;
  sheet_id: string;
  sheet_name: string;
  region_id: string;
  record_type: string;
  record_count: number;
  passed_count: number;
  failed_count: number;
  pending_rebuild_count: number;
  min_source_row: number;
  max_source_row: number;
  latest_created_at: string;
};

export type DatasetRecordGroupPage = {
  items: DatasetRecordGroup[];
  total: number;
  limit: number;
  offset: number;
};

export type DatasetRecordFile = {
  item_id: string;
  source_file_name: string;
  administrative_unit_name: string;
  record_count: number;
  passed_count: number;
  failed_count: number;
  pending_rebuild_count: number;
  dataset_count: number;
  latest_created_at: string;
  children: DatasetRecordGroup[];
};

export type DatasetRecordFilePage = {
  items: DatasetRecordFile[];
  total: number;
  limit: number;
  offset: number;
};

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function responseJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new ApiError(
      response.status,
      body?.detail ?? `请求失败（${response.status}）`,
    );
  }
  return response.json() as Promise<T>;
}

export async function getCurrentUser(signal?: AbortSignal): Promise<CurrentUser> {
  return responseJson<CurrentUser>(
    await fetch("/api/auth/me", { signal }),
  );
}

export async function login(
  username: string,
  password: string,
): Promise<CurrentUser> {
  return responseJson<CurrentUser>(
    await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    }),
  );
}

export async function logout(): Promise<void> {
  const response = await fetch("/api/auth/logout", { method: "POST" });
  if (!response.ok && response.status !== 401) {
    await responseJson(response);
  }
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
): Promise<void> {
  const response = await fetch("/api/auth/password", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
    }),
  });
  if (!response.ok) await responseJson(response);
}

export async function getManagedTenants(
  filters: {
    search?: string;
    status?: string;
    limit?: number;
    offset?: number;
  } = {},
  signal?: AbortSignal,
): Promise<ManagedTenantPage> {
  const query = new URLSearchParams({
    limit: String(filters.limit ?? 20),
    offset: String(filters.offset ?? 0),
  });
  if (filters.search) query.set("search", filters.search);
  if (filters.status && filters.status !== "all") query.set("status", filters.status);
  return responseJson<ManagedTenantPage>(
    await fetch(`/api/admin/tenants?${query.toString()}`, { signal }),
  );
}

export async function createManagedTenant(
  name: string,
  townshipName: string,
): Promise<ManagedTenant> {
  return responseJson<ManagedTenant>(
    await fetch("/api/admin/tenants", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, township_name: townshipName }),
    }),
  );
}

export async function updateManagedTenant(
  tenantId: string,
  payload: { name?: string; status?: "active" | "disabled" },
): Promise<ManagedTenant> {
  return responseJson<ManagedTenant>(
    await fetch(`/api/admin/tenants/${tenantId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function deleteManagedTenant(tenantId: string): Promise<void> {
  const response = await fetch(`/api/admin/tenants/${tenantId}`, {
    method: "DELETE",
  });
  if (!response.ok) await responseJson(response);
}

export async function createManagedUnit(
  tenantId: string,
  payload: {
    name: string;
    unit_type: "township" | "village";
    parent_id: string | null;
  },
): Promise<AdministrativeUnit> {
  return responseJson<AdministrativeUnit>(
    await fetch(`/api/admin/tenants/${tenantId}/units`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function updateManagedUnit(
  unitId: string,
  payload: { name?: string; status?: "active" | "disabled" },
): Promise<AdministrativeUnit> {
  return responseJson<AdministrativeUnit>(
    await fetch(`/api/admin/units/${unitId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function deleteManagedUnit(unitId: string): Promise<void> {
  const response = await fetch(`/api/admin/units/${unitId}`, {
    method: "DELETE",
  });
  if (!response.ok) await responseJson(response);
}

export async function getManagedUsers(
  filters: {
    search?: string;
    status?: string;
    role?: string;
    limit?: number;
    offset?: number;
  } = {},
  signal?: AbortSignal,
): Promise<ManagedUserPage> {
  const query = new URLSearchParams({
    limit: String(filters.limit ?? 20),
    offset: String(filters.offset ?? 0),
  });
  if (filters.search) query.set("search", filters.search);
  if (filters.status && filters.status !== "all") query.set("status", filters.status);
  if (filters.role && filters.role !== "all") query.set("role", filters.role);
  return responseJson<ManagedUserPage>(
    await fetch(`/api/admin/users?${query.toString()}`, { signal }),
  );
}

export async function createManagedUser(payload: {
  username: string;
  display_name: string;
  password: string;
  tenant_id: string;
  role: CurrentUser["role"];
  scope_unit_id: string | null;
}): Promise<ManagedUser> {
  return responseJson<ManagedUser>(
    await fetch("/api/admin/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function updateManagedUser(
  userId: string,
  payload: Partial<{
    username: string;
    display_name: string;
    password: string;
    status: "active" | "disabled";
    tenant_id: string;
    role: CurrentUser["role"];
    scope_unit_id: string | null;
  }>,
): Promise<ManagedUser> {
  return responseJson<ManagedUser>(
    await fetch(`/api/admin/users/${userId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function deleteManagedUser(userId: string): Promise<void> {
  const response = await fetch(`/api/admin/users/${userId}`, {
    method: "DELETE",
  });
  if (!response.ok) await responseJson(response);
}

export async function getBatches(signal?: AbortSignal): Promise<Batch[]> {
  return responseJson<Batch[]>(await fetch("/api/batches", { signal }));
}

export async function getFiles(
  filters: {
    search?: string;
    status?: string;
    tenantId?: string;
    administrativeUnitId?: string;
    limit?: number;
    offset?: number;
  } = {},
  signal?: AbortSignal,
): Promise<FileLedgerPage> {
  const query = new URLSearchParams({
    limit: String(filters.limit ?? 20),
    offset: String(filters.offset ?? 0),
  });
  if (filters.search) query.set("search", filters.search);
  if (filters.status && filters.status !== "all") query.set("status", filters.status);
  if (filters.tenantId) query.set("tenant_id", filters.tenantId);
  if (filters.administrativeUnitId) {
    query.set("administrative_unit_id", filters.administrativeUnitId);
  }
  return responseJson<FileLedgerPage>(
    await fetch(`/api/files?${query.toString()}`, { signal }),
  );
}

export async function reimportFile(
  batchId: string,
  itemId: string,
): Promise<BatchItem> {
  return responseJson<BatchItem>(
    await fetch(`/api/batches/${batchId}/items/${itemId}/reimport`, {
      method: "POST",
    }),
  );
}

export async function deleteBuildResult(
  batchId: string,
  itemId: string,
): Promise<BuildResultDeletion> {
  return responseJson<BuildResultDeletion>(
    await fetch(`/api/batches/${batchId}/items/${itemId}/build-result`, {
      method: "DELETE",
    }),
  );
}

export async function getBatchItems(
  batchId: string,
  signal?: AbortSignal,
): Promise<BatchItem[]> {
  return responseJson<BatchItem[]>(
    await fetch(`/api/batches/${batchId}/items`, { signal }),
  );
}

export async function getItemProfile(
  batchId: string,
  itemId: string,
  signal?: AbortSignal,
): Promise<WorkbookProfile> {
  return responseJson<WorkbookProfile>(
    await fetch(`/api/batches/${batchId}/items/${itemId}/profile`, { signal }),
  );
}

export async function getItemMatch(
  batchId: string,
  itemId: string,
  signal?: AbortSignal,
): Promise<TemplateMatch> {
  return responseJson<TemplateMatch>(
    await fetch(`/api/batches/${batchId}/items/${itemId}/match`, { signal }),
  );
}

export async function getItemRegionMatches(
  batchId: string,
  itemId: string,
  signal?: AbortSignal,
): Promise<RegionTemplateMatch[]> {
  return responseJson<RegionTemplateMatch[]>(
    await fetch(`/api/batches/${batchId}/items/${itemId}/region-matches`, { signal }),
  );
}

export async function getItemFieldMatches(
  batchId: string,
  itemId: string,
  signal?: AbortSignal,
): Promise<FieldMatch[]> {
  return responseJson<FieldMatch[]>(
    await fetch(`/api/batches/${batchId}/items/${itemId}/field-matches`, { signal }),
  );
}

export async function getItemProposals(
  batchId: string,
  itemId: string,
  signal?: AbortSignal,
): Promise<TemplateProposal[]> {
  return responseJson<TemplateProposal[]>(
    await fetch(`/api/batches/${batchId}/items/${itemId}/proposals`, { signal }),
  );
}

export async function acceptReviewProposal(
  review: ReviewQueueItem,
  payload: {
    template_code: string | null;
    template_name: string;
    domain: string;
    record_type: string;
    record_grain: string | null;
    field_resolutions: GovernanceFieldResolution[];
  },
): Promise<TemplateProposal> {
  return responseJson<TemplateProposal>(
    await fetch(
      `/api/batches/${review.batch_id}/items/${review.item_id}/proposals/${review.proposal_id}/accept`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...payload,
          actor: "local-admin",
          comment: "管理员处理自动通行阻断项",
        }),
      },
    ),
  );
}

export async function rejectReviewProposal(
  review: ReviewQueueItem,
  comment: string,
): Promise<TemplateProposal> {
  return responseJson<TemplateProposal>(
    await fetch(
      `/api/batches/${review.batch_id}/items/${review.item_id}/proposals/${review.proposal_id}/reject`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ actor: "local-admin", comment }),
      },
    ),
  );
}

export async function getReviewQueue(
  filters: {
    tenantId?: string;
    administrativeUnitId?: string;
    search?: string;
    limit?: number;
    offset?: number;
  } = {},
  signal?: AbortSignal,
): Promise<ReviewQueuePage> {
  const query = new URLSearchParams({
    limit: String(filters.limit ?? 20),
    offset: String(filters.offset ?? 0),
  });
  if (filters.tenantId) query.set("tenant_id", filters.tenantId);
  if (filters.administrativeUnitId) {
    query.set("administrative_unit_id", filters.administrativeUnitId);
  }
  if (filters.search) query.set("search", filters.search);
  return responseJson<ReviewQueuePage>(
    await fetch(`/api/reviews?${query.toString()}`, { signal }),
  );
}

export async function getReview(
  proposalId: string,
  signal?: AbortSignal,
): Promise<ReviewQueueItem> {
  return responseJson<ReviewQueueItem>(
    await fetch(`/api/reviews/${proposalId}`, { signal }),
  );
}

export async function getWorkerCapacity(
  signal?: AbortSignal,
): Promise<WorkerCapacity> {
  return responseJson<WorkerCapacity>(
    await fetch("/api/health/capacity", { signal }),
  );
}

export async function approveImportPlan(
  item: BatchItem,
  match: TemplateMatch,
): Promise<void> {
  if (!match.template_id || !match.template_version) {
    throw new Error("当前没有可批准的已发布模版");
  }
  await responseJson(
    await fetch(`/api/batches/${item.batch_id}/items/${item.id}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        template_id: match.template_id,
        template_version: match.template_version,
        layout_plan: {},
        field_mappings: [],
        actor: "user",
        comment: "用户确认精确模版匹配",
      }),
    }),
  );
}

export async function getFields(signal?: AbortSignal): Promise<SemanticField[]> {
  return responseJson<SemanticField[]>(await fetch("/api/fields", { signal }));
}

export async function getCatalogDirectory<T>(
  filters: {
    section: string;
    search?: string;
    status?: string;
    layer?: string;
    dataType?: string;
    limit?: number;
    offset?: number;
  },
  signal?: AbortSignal,
): Promise<CatalogDirectoryPage<T>> {
  const query = new URLSearchParams({
    section: filters.section,
    limit: String(filters.limit ?? 20),
    offset: String(filters.offset ?? 0),
  });
  if (filters.search) query.set("search", filters.search);
  if (filters.status && filters.status !== "all") query.set("status", filters.status);
  if (filters.layer && filters.layer !== "all") query.set("layer", filters.layer);
  if (filters.dataType && filters.dataType !== "all") {
    query.set("data_type", filters.dataType);
  }
  return responseJson<CatalogDirectoryPage<T>>(
    await fetch(`/api/catalog/directory?${query.toString()}`, { signal }),
  );
}

export async function getFieldDetails(
  fieldId: string,
  signal?: AbortSignal,
): Promise<SemanticFieldDetail> {
  return responseJson<SemanticFieldDetail>(
    await fetch(`/api/fields/${fieldId}/details`, { signal }),
  );
}

export async function getTemplates(signal?: AbortSignal): Promise<Template[]> {
  return responseJson<Template[]>(await fetch("/api/templates", { signal }));
}

export async function getRegionTemplates(
  signal?: AbortSignal,
): Promise<RegionTemplate[]> {
  return responseJson<RegionTemplate[]>(
    await fetch("/api/region-templates", { signal }),
  );
}

export async function getRegionTemplateSourcePreview(
  templateId: string,
  signal?: AbortSignal,
): Promise<RegionSourcePreview> {
  return responseJson<RegionSourcePreview>(
    await fetch(`/api/region-templates/${templateId}/source-preview`, { signal }),
  );
}

export async function getWorkbookRouteSourcePreview(
  routeId: string,
  signal?: AbortSignal,
): Promise<WorkbookRouteSourcePreview> {
  return responseJson<WorkbookRouteSourcePreview>(
    await fetch(`/api/workbook-routes/${routeId}/source-preview`, { signal }),
  );
}

export async function getSheetCompositions(
  signal?: AbortSignal,
): Promise<SheetComposition[]> {
  return responseJson<SheetComposition[]>(
    await fetch("/api/sheet-compositions", { signal }),
  );
}

export async function getWorkbookRoutes(
  signal?: AbortSignal,
): Promise<WorkbookRoute[]> {
  return responseJson<WorkbookRoute[]>(
    await fetch("/api/workbook-routes", { signal }),
  );
}

export async function getMetrics(signal?: AbortSignal): Promise<MetricDefinition[]> {
  return responseJson<MetricDefinition[]>(await fetch("/api/metrics", { signal }));
}

export async function getDatasetRecords(
  filters: {
    mappingStatus?: string;
    qualityStatus?: string;
    limit?: number;
    offset?: number;
    itemId?: string;
    sheetId?: string;
    regionId?: string;
    recordType?: string;
  } = {},
  signal?: AbortSignal,
): Promise<DatasetRecordPage> {
  const query = new URLSearchParams({
    limit: String(filters.limit ?? 50),
    offset: String(filters.offset ?? 0),
  });
  if (filters.mappingStatus) query.set("mapping_status", filters.mappingStatus);
  if (filters.qualityStatus) query.set("quality_status", filters.qualityStatus);
  if (filters.itemId) query.set("item_id", filters.itemId);
  if (filters.sheetId) query.set("sheet_id", filters.sheetId);
  if (filters.regionId) query.set("region_id", filters.regionId);
  if (filters.recordType) query.set("record_type", filters.recordType);
  return responseJson<DatasetRecordPage>(
    await fetch(`/api/records?${query.toString()}`, { signal }),
  );
}

export async function getDatasetRecordGroups(
  filters: { qualityStatus?: string; limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<DatasetRecordGroupPage> {
  const query = new URLSearchParams({
    limit: String(filters.limit ?? 20),
    offset: String(filters.offset ?? 0),
  });
  if (filters.qualityStatus) query.set("quality_status", filters.qualityStatus);
  return responseJson<DatasetRecordGroupPage>(
    await fetch(`/api/records/groups?${query.toString()}`, { signal }),
  );
}

export async function getDatasetRecordTree(
  filters: { qualityStatus?: string; limit?: number; offset?: number } = {},
  signal?: AbortSignal,
): Promise<DatasetRecordFilePage> {
  const query = new URLSearchParams({
    limit: String(filters.limit ?? 10),
    offset: String(filters.offset ?? 0),
  });
  if (filters.qualityStatus) query.set("quality_status", filters.qualityStatus);
  return responseJson<DatasetRecordFilePage>(
    await fetch(`/api/records/tree?${query.toString()}`, { signal }),
  );
}

export async function createMetric(payload: {
  code: string;
  name: string;
  semantic_field_code: string;
  semantic_field_version: number;
  aggregation: string;
  unit: string | null;
}): Promise<MetricDefinition> {
  return responseJson<MetricDefinition>(
    await fetch("/api/metrics", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...payload,
        description: "",
        allowed_filter_fields: [],
        aliases: [],
      }),
    }),
  );
}

export async function createField(payload: {
  code: string;
  name: string;
  description: string;
  layer: "base" | "domain";
  data_type: string;
  unit_dimension: string | null;
}): Promise<SemanticField> {
  return responseJson<SemanticField>(
    await fetch("/api/fields", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...payload, aliases: [], validators: [] }),
    }),
  );
}

export async function publishField(
  field: SemanticField,
  actor = "admin",
): Promise<SemanticField> {
  return responseJson<SemanticField>(
    await fetch(`/api/fields/${field.id}/versions/${field.version}/publish`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ actor, comment: "管理员发布" }),
    }),
  );
}

export async function runTemplateAction(
  template: Template,
  action: "confirm" | "submit-review" | "approve" | "reject" | "deprecate",
  comment = "",
): Promise<Template> {
  return responseJson<Template>(
    await fetch(
      `/api/templates/${template.id}/versions/${template.version}/${action}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          actor: action === "confirm" || action === "submit-review" ? "user" : "admin",
          comment,
        }),
      },
    ),
  );
}

export async function uploadBatch(
  name: string,
  files: FileList,
  administrativeUnitId?: string,
): Promise<Batch> {
  const batch = await responseJson<Batch>(
    await fetch("/api/batches", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        administrative_unit_id: administrativeUnitId,
      }),
    }),
  );
  const queue = Array.from(files);
  const failures: string[] = [];
  let nextIndex = 0;

  async function uploadWorker() {
    while (nextIndex < queue.length) {
      const file = queue[nextIndex];
      nextIndex += 1;
      const body = new FormData();
      body.append("file", file);
      body.append("relative_path", file.webkitRelativePath || file.name);
      try {
        await responseJson<BatchItem>(
          await fetch(`/api/batches/${batch.id}/files`, {
            method: "POST",
            body,
          }),
        );
      } catch (cause) {
        failures.push(
          `${file.name}：${cause instanceof Error ? cause.message : "上传失败"}`,
        );
      }
    }
  }

  const configuredConcurrency = Number(
    import.meta.env.VITE_UPLOAD_CONCURRENCY ?? "2",
  );
  const uploadConcurrency =
    Number.isInteger(configuredConcurrency) && configuredConcurrency > 0
      ? Math.min(configuredConcurrency, 3)
      : 2;
  const workerCount = Math.min(uploadConcurrency, queue.length);
  await Promise.all(Array.from({ length: workerCount }, () => uploadWorker()));
  if (failures.length === queue.length) {
    throw new Error(`全部文件上传失败：${failures.join("；")}`);
  }
  const refreshed = responseJson<Batch>(await fetch(`/api/batches/${batch.id}`));
  if (failures.length) {
    const result = await refreshed;
    return { ...result, upload_failures: failures };
  }
  return refreshed;
}

export async function importDirectory(
  name: string,
  directory: string,
  administrativeUnitId?: string,
): Promise<Batch> {
  return responseJson<Batch>(
    await fetch("/api/batches/directory", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        directory,
        recursive: true,
        administrative_unit_id: administrativeUnitId,
      }),
    }),
  );
}

export async function getQuestionConversations(
  scopeUnitId: string,
  sourceItemId = "",
  page = 1,
  search = "",
  signal?: AbortSignal,
): Promise<QuestionConversationPage> {
  const query = new URLSearchParams({
    scope_unit_id: scopeUnitId,
    page: String(page),
    page_size: "12",
  });
  if (sourceItemId) query.set("source_item_id", sourceItemId);
  if (search) query.set("search", search);
  return responseJson<QuestionConversationPage>(
    await fetch(`/api/questions/conversations?${query.toString()}`, { signal }),
  );
}

export async function getQuestionSources(
  scopeUnitId: string,
  page = 1,
  search = "",
  signal?: AbortSignal,
): Promise<QuestionSourcePage> {
  const query = new URLSearchParams({
    scope_unit_id: scopeUnitId,
    page: String(page),
    page_size: "12",
  });
  if (search) query.set("search", search);
  return responseJson<QuestionSourcePage>(
    await fetch(`/api/questions/sources?${query.toString()}`, { signal }),
  );
}

export async function createQuestionConversation(
  scopeUnitId: string,
  sourceItemId?: string,
): Promise<QuestionConversation> {
  return responseJson<QuestionConversation>(
    await fetch("/api/questions/conversations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        scope_unit_id: scopeUnitId,
        source_item_id: sourceItemId || null,
      }),
    }),
  );
}

export async function getQuestionConversation(
  conversationId: string,
  runOffset = 0,
  signal?: AbortSignal,
): Promise<QuestionConversationDetail> {
  const query = new URLSearchParams({
    run_offset: String(runOffset),
    run_limit: "20",
  });
  return responseJson<QuestionConversationDetail>(
    await fetch(
      `/api/questions/conversations/${conversationId}?${query.toString()}`,
      { signal },
    ),
  );
}

export async function deleteQuestionConversations(
  conversationIds: string[],
): Promise<{ deleted: number }> {
  if (conversationIds.length === 1) {
    return responseJson<{ deleted: number }>(
      await fetch(`/api/questions/conversations/${conversationIds[0]}`, {
        method: "DELETE",
      }),
    );
  }
  return responseJson<{ deleted: number }>(
    await fetch("/api/questions/conversations/bulk-delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_ids: conversationIds }),
    }),
  );
}

export async function renameQuestionConversation(
  conversationId: string,
  title: string,
): Promise<QuestionConversation> {
  return responseJson<QuestionConversation>(
    await fetch(`/api/questions/conversations/${conversationId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }),
  );
}

function decodeSseBlock(block: string): QuestionStreamEvent | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (!dataLines.length) return null;
  const payload = JSON.parse(dataLines.join("\n")) as Omit<QuestionStreamEvent, "event">;
  return { event, ...payload };
}

export async function streamQuestionRun(
  conversationId: string,
  question: string,
  onEvent: (event: QuestionStreamEvent) => void,
  signal?: AbortSignal,
  retryOfRunId?: string,
): Promise<void> {
  const response = await fetch(
    `/api/questions/conversations/${conversationId}/runs`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        question,
        retry_of_run_id: retryOfRunId ?? null,
      }),
      signal,
    },
  );
  if (!response.ok) {
    await responseJson(response);
    return;
  }
  if (!response.body) throw new Error("浏览器无法读取流式回答");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) {
      const event = decodeSseBlock(block);
      if (event) onEvent(event);
    }
    if (done) break;
  }
  const trailing = decodeSseBlock(buffer.trim());
  if (trailing) onEvent(trailing);
}

export async function stopQuestionRun(
  conversationId: string,
): Promise<{ run_id: string; stopped: boolean }> {
  return responseJson<{ run_id: string; stopped: boolean }>(
    await fetch(`/api/questions/conversations/${conversationId}/stop`, {
      method: "POST",
    }),
  );
}

export async function getQuestionRunEvidence(
  runId: string,
  signal?: AbortSignal,
): Promise<{
  run_id: string;
  query_plan: Record<string, unknown>;
  evidence: QuestionRun["evidence"];
  tool_trace: QuestionToolTrace[];
}> {
  return responseJson(
    await fetch(`/api/questions/runs/${runId}/evidence`, { signal }),
  );
}

export async function getLLMConfiguration(
  signal?: AbortSignal,
): Promise<LLMConfiguration> {
  return responseJson<LLMConfiguration>(
    await fetch("/api/settings/llm", { signal }),
  );
}

export async function getLLMProviderPresets(
  signal?: AbortSignal,
): Promise<LLMProviderPreset[]> {
  return responseJson<LLMProviderPreset[]>(
    await fetch("/api/settings/llm/presets", { signal }),
  );
}

export async function saveLLMConfiguration(
  payload: LLMConfigurationInput,
): Promise<LLMConfiguration> {
  return responseJson<LLMConfiguration>(
    await fetch("/api/settings/llm", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}

export async function testLLMConfiguration(
  payload?: LLMConfigurationInput,
): Promise<{
  status: string;
  provider: string;
  model: string;
  api_mode: "openai_chat" | "anthropic_messages";
  latency_ms: number;
  stages: string[];
}> {
  return responseJson(
    await fetch("/api/settings/llm/test", {
      method: "POST",
      headers: payload ? { "Content-Type": "application/json" } : undefined,
      body: payload ? JSON.stringify(payload) : undefined,
    }),
  );
}

export async function discoverLLMModels(
  payload: LLMConfigurationInput,
): Promise<{
  status: string;
  models: string[];
  latency_ms: number;
}> {
  return responseJson(
    await fetch("/api/settings/llm/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }),
  );
}
