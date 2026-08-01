import type {
  GovernanceFieldResolution,
  ReviewFieldEvidence,
  SemanticField,
} from "../lib/api";

export function initialResolution(
  evidence: ReviewFieldEvidence,
  fields: SemanticField[],
): GovernanceFieldResolution {
  const action = evidence.hermes_suggestion.action;
  const targetCode = action === "REUSE_FIELD"
    || action === "ADD_ALIAS"
    || action === "ROLE_VARIANT"
    ? evidence.hermes_suggestion.semantic_field_code ?? null
    : null;
  const target = fields.find((field) => field.code === targetCode);
  const createNew = action === "PROPOSE_NEW_FIELD" && !target;
  return {
    source_column_id: evidence.source_column_id,
    mode: createNew ? "create_new" : target ? "reuse_existing" : "ignore",
    semantic_field_code: target?.code ?? null,
    expected_field_version: target?.published_version ?? null,
    learn_alias: action === "ADD_ALIAS" ? evidence.leaf_header : null,
    learn_path: true,
    role: evidence.hermes_suggestion.role ?? null,
    unit: evidence.hermes_suggestion.unit ?? null,
    new_field_code: createNew
      ? (evidence.hermes_suggestion.proposed_field_code ?? null)
      : null,
    new_field_name: createNew ? evidence.leaf_header : null,
    new_field_layer: createNew
      ? (evidence.hermes_suggestion.layer ?? "domain")
      : null,
    new_field_data_type: createNew
      ? (evidence.hermes_suggestion.data_type
        ?? evidence.observed_data_type
        ?? "text")
      : null,
    ignore_scope: target || createNew ? null : "file",
    ignore_reason: target || createNew ? null : "",
  };
}
