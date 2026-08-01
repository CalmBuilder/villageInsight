import { expect, test } from "vitest";
import type { QuestionRun } from "../lib/api";
import {
  formatAnswerForClipboard,
  formatQueryDuration,
} from "./questionFormatting";
import { questionInputKeyAction } from "./questionInput";

test("copied answer contains visible rows but omits internal record identifiers", () => {
  const run = {
    id: "run-1",
    retry_of_run_id: null,
    question: "甲村有多少人？",
    answer_text: "甲村共有 2 人。",
    answer: {
      result_type: "table",
      columns: ["village_name", "record_id", "total"],
      rows: [{
        village_name: "甲村",
        record_id: "internal-record-id",
        total: 2,
      }],
      row_count: 1,
    },
    status: "succeeded",
    route: "hermes_studio",
    source_item_id: null,
    tool_trace: [],
    evidence: [],
    error_code: null,
    started_at: "2026-07-29T00:00:00Z",
    created_at: "2026-07-29T00:00:00Z",
    completed_at: "2026-07-29T00:00:01Z",
  } satisfies QuestionRun;

  const copied = formatAnswerForClipboard(run);

  expect(copied).toContain("甲村共有 2 人。");
  expect(copied).toContain("| village_name | total |");
  expect(copied).not.toContain("record_id");
  expect(copied).not.toContain("internal-record-id");
});

test("query duration uses compact Chinese seconds and minutes", () => {
  expect(formatQueryDuration(9_999)).toBe("9秒");
  expect(formatQueryDuration(60_000)).toBe("1分");
  expect(formatQueryDuration(62_800)).toBe("1分 2秒");
});

test("question input submits only plain Enter and preserves editing chords", () => {
  const key = {
    key: "Enter",
    shiftKey: false,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    isComposing: false,
    keyCode: 13,
  };

  expect(questionInputKeyAction(key)).toBe("submit");
  expect(questionInputKeyAction({ ...key, shiftKey: true })).toBe("newline");
  expect(questionInputKeyAction({ ...key, ctrlKey: true })).toBe("newline");
  expect(questionInputKeyAction({ ...key, isComposing: true })).toBe("native");
  expect(questionInputKeyAction({ ...key, keyCode: 229 })).toBe("native");
  expect(questionInputKeyAction({ ...key, metaKey: true })).toBe("native");
});
