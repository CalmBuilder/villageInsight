import { afterEach, expect, test, vi } from "vitest";
import {
  deleteQuestionConversations,
  getQuestionConversation,
  getQuestionConversations,
  renameQuestionConversation,
  streamQuestionRun,
  uploadBatch,
} from "./api";

afterEach(() => vi.unstubAllGlobals());

test("batch upload uses a bounded three-file request pool", async () => {
  let activeUploads = 0;
  let maximumActiveUploads = 0;
  let uploadCount = 0;
  const batch = {
    id: "batch-1",
    name: "并发测试",
    source_kind: "upload",
    status: "pending",
    total_files: 5,
    completed_files: 0,
    failed_files: 0,
    created_at: "2026-07-29T00:00:00Z",
    updated_at: "2026-07-29T00:00:00Z",
  };

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/api/batches" && init?.method === "POST") {
        return new Response(JSON.stringify({ ...batch, total_files: 0 }), {
          status: 201,
        });
      }
      if (url === "/api/batches/batch-1/files") {
        activeUploads += 1;
        uploadCount += 1;
        maximumActiveUploads = Math.max(maximumActiveUploads, activeUploads);
        await new Promise((resolve) => window.setTimeout(resolve, 10));
        activeUploads -= 1;
        return new Response(
          JSON.stringify({
            id: `item-${uploadCount}`,
            batch_id: "batch-1",
            original_name: "test.csv",
            relative_path: null,
            size_bytes: 1,
            status: "pending",
            evidence_status: "pending",
            formal_import_status: "pending",
            parser_name: null,
            error_code: null,
            error_message: null,
            created_at: "2026-07-29T00:00:00Z",
            updated_at: "2026-07-29T00:00:00Z",
          }),
          { status: 201 },
        );
      }
      if (url === "/api/batches/batch-1") {
        return new Response(JSON.stringify(batch), { status: 200 });
      }
      return new Response(null, { status: 404 });
    }),
  );

  const files = Array.from(
    { length: 5 },
    (_, index) => new File([String(index)], `file-${index}.csv`, { type: "text/csv" }),
  ) as unknown as FileList;

  const result = await uploadBatch("并发测试", files);

  expect(result.id).toBe("batch-1");
  expect(uploadCount).toBe(5);
  expect(maximumActiveUploads).toBe(2);
});

test("question stream decodes SSE events across network chunks", async () => {
  const encoder = new TextEncoder();
  let requestBody = "";
  const chunks = [
    'event: run.started\ndata: {"sequence":1,"run_id":"run-1",',
    '"conversation_id":"conversation-1"}\n\nevent: tool.completed\n',
    'data: {"sequence":2,"run_id":"run-1","conversation_id":"conversation-1",',
    '"tool_call_id":"tool-1","label":"统计人数","record_count":7}\n\n',
  ];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      requestBody = String(init?.body ?? "");
      return new Response(
        new ReadableStream({
          start(controller) {
            for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
            controller.close();
          },
        }),
        {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        },
      );
    }),
  );
  const events: string[] = [];

  await streamQuestionRun(
    "conversation-1",
    "全村总人数是多少？",
    (event) => events.push(`${event.event}:${event.sequence}`),
    undefined,
    "run-original",
  );

  expect(events).toEqual(["run.started:1", "tool.completed:2"]);
  expect(JSON.parse(requestBody)).toEqual({
    question: "全村总人数是多少？",
    retry_of_run_id: "run-original",
  });
});

test("question history uses scoped pagination and batch soft delete", async () => {
  const requests: Array<{ url: string; method: string }> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      requests.push({ url, method: init?.method ?? "GET" });
      if (url.startsWith("/api/questions/conversations?")) {
        return new Response(JSON.stringify({
          items: [],
          page: 2,
          page_size: 12,
          total: 21,
          total_pages: 2,
        }), { status: 200 });
      }
      if (url.includes("run_offset=20")) {
        return new Response(JSON.stringify({
          conversation: {},
          runs: [],
          run_total: 20,
          has_more_before: false,
        }), { status: 200 });
      }
      if (url === "/api/questions/conversations/bulk-delete") {
        return new Response(JSON.stringify({ deleted: 2 }), { status: 200 });
      }
      if (
        url === "/api/questions/conversations/conversation-1"
        && init?.method === "PATCH"
      ) {
        return new Response(JSON.stringify({ id: "conversation-1", title: "新标题" }), {
          status: 200,
        });
      }
      return new Response(null, { status: 404 });
    }),
  );

  await getQuestionConversations("scope-1", "source-1", 2, "人口");
  await getQuestionConversation("conversation-1", 20);
  await deleteQuestionConversations(["conversation-1", "conversation-2"]);
  await renameQuestionConversation("conversation-1", "新标题");

  expect(requests[0]).toEqual({
    method: "GET",
    url: "/api/questions/conversations?scope_unit_id=scope-1&page=2&page_size=12&source_item_id=source-1&search=%E4%BA%BA%E5%8F%A3",
  });
  expect(requests[1].url).toContain("run_offset=20");
  expect(requests[2]).toEqual({
    method: "POST",
    url: "/api/questions/conversations/bulk-delete",
  });
  expect(requests[3]).toEqual({
    method: "PATCH",
    url: "/api/questions/conversations/conversation-1",
  });
});
