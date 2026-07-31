import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ReviewFieldEvidence, SemanticField } from "../lib/api";
import { initialResolution, ReviewsPage } from "./ReviewsPage";

const api = vi.hoisted(() => ({
  getFields: vi.fn(),
  getReviewQueue: vi.fn(),
}));

vi.mock("../lib/api", () => ({
  acceptReviewProposal: vi.fn(),
  getFields: api.getFields,
  getReview: vi.fn(),
  getReviewQueue: api.getReviewQueue,
  rejectReviewProposal: vi.fn(),
}));

beforeEach(() => {
  api.getFields.mockResolvedValue([]);
  api.getReviewQueue.mockResolvedValue({
    items: [],
    total: 0,
    limit: 20,
    offset: 0,
  });
});

afterEach(cleanup);

test("explains governance choices with examples", () => {
  render(<ReviewsPage />);

  fireEvent.click(screen.getByRole("button", { name: "查看数据治理说明" }));

  expect(
    screen.getByRole("dialog", { name: "把来源列确认成可复用的标准含义" }),
  ).toBeVisible();
  expect(screen.getByText("例：“户别” → 户别类型")).toBeVisible();
  expect(screen.getByText("例：首次出现的本地业务分类")).toBeVisible();
  expect(screen.getByText("例：“序号”或脱敏展示辅助列")).toBeVisible();
  expect(screen.getByText(/原始单元格仍完整保留/)).toBeVisible();
});

test("closes the governance help with Escape", () => {
  render(<ReviewsPage />);

  fireEvent.click(screen.getByRole("button", { name: "查看数据治理说明" }));
  fireEvent.keyDown(window, { key: "Escape" });

  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

test("does not preselect a weak type-compatible candidate", () => {
  const evidence: ReviewFieldEvidence = {
    source_column_id: "sheet:1:column:18",
    sheet_id: "sheet:1",
    sheet_name: "党员名册",
    region_id: "region:1",
    column_index: 18,
    column_coordinate: "R",
    header_path: [],
    parent_path: [],
    leaf_header: "未命名列",
    observed_data_type: "text",
    match_type: "none",
    score_basis_points: 1_000,
    candidates: [{
      semantic_field_code: "address.city",
      semantic_field_version: 1,
      score_basis_points: 1_000,
      reasons: ["data_type"],
    }],
    hermes_suggestion: { confidence: 0 },
    requires_resolution: true,
  };
  const field: SemanticField = {
    id: "field:address.city",
    code: "address.city",
    name: "市级行政区",
    description: "",
    layer: "base",
    data_type: "text",
    unit_dimension: null,
    aliases: [],
    validators: [],
    variants: [],
    version: 1,
    status: "published",
    published_version: 1,
  };

  expect(initialResolution(evidence, [field])).toMatchObject({
    mode: "ignore",
    semantic_field_code: null,
    ignore_reason: "",
  });
});
