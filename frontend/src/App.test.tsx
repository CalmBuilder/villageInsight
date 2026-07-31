import { StrictMode } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { App } from "./App";

const currentUser = {
  user_id: "user-1",
  username: "village-operator",
  display_name: "村级数据员",
  tenant_id: "tenant-1",
  tenant_name: "测试乡镇",
  membership_id: "membership-1",
  role: "village_operator",
  scope_unit_id: "village-1",
  scope_unit_name: "测试村",
  scope_unit_type: "village",
  include_descendants: false,
  permissions: [
    "imports.create",
    "imports.read.village",
    "records.read.village",
    "questions.ask.village",
  ],
};

function currentUserResponse() {
  return new Response(JSON.stringify(currentUser), { status: 200 });
}

async function defaultFetch(input: RequestInfo | URL): Promise<Response> {
  const url = String(input);
  if (url.includes("/api/auth/me")) return currentUserResponse();
  if (url.includes("/api/health/capacity")) {
    return new Response(
      JSON.stringify({
        lanes: { parse: 2, hermes: 1, materialize: 1 },
        queued: { parse: 0, hermes: 0, materialize: 0 },
        running: { parse: 0, hermes: 0, materialize: 0 },
        resources: {
          available_memory_mb: 8192,
          total_memory_mb: 32768,
          admission_floor_mb: 4096,
          admission_paused: false,
        },
      }),
      { status: 200 },
    );
  }
  if (url.includes("/api/files?")) {
    return new Response(
      JSON.stringify({
        items: [],
        total: 0,
        limit: 20,
        offset: 0,
        counts: {
          all: 0,
          imported: 0,
          processing: 0,
          hermes: 0,
          review: 0,
          failed: 0,
        },
      }),
      { status: 200 },
    );
  }
  return new Response("[]", { status: 200 });
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(defaultFetch));
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

test("renders the ingestion workbench", async () => {
  window.history.pushState({}, "", "/");
  render(<App />);
  expect(
    await screen.findByRole("heading", { name: "文件入库" }),
  ).toBeVisible();
  expect(await screen.findByText("文件台账")).toBeVisible();
  expect(screen.getByRole("link", { name: /文件入库/ })).toHaveAttribute(
    "href",
    "/batches",
  );
});

test("shows login only when the identity endpoint returns 401", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/api/auth/me")) {
        return new Response(JSON.stringify({ detail: "请先登录" }), {
          status: 401,
        });
      }
      return defaultFetch(input);
    }),
  );

  render(<App />);

  expect(await screen.findByLabelText("用户名")).toBeVisible();
});

test("keeps the identity pending and retries after a network failure", async () => {
  let authCalls = 0;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes("/api/auth/me")) {
        authCalls += 1;
        if (authCalls === 1) throw new TypeError("Failed to fetch");
        return currentUserResponse();
      }
      return defaultFetch(input);
    }),
  );

  render(<App />);

  expect(
    await screen.findByText("服务正在恢复，正在重新连接…"),
  ).toBeVisible();
  expect(screen.queryByLabelText("用户名")).not.toBeInTheDocument();
  await waitFor(
    () => expect(screen.getByRole("heading", { name: "文件入库" })).toBeVisible(),
    { timeout: 3_000 },
  );
  expect(authCalls).toBe(2);
});

test("ignores the StrictMode cleanup AbortError", async () => {
  let authCalls = 0;
  let resolveSecond!: (response: Response) => void;
  const secondResponse = new Promise<Response>((resolve) => {
    resolveSecond = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(
      (
        input: RequestInfo | URL,
        init?: RequestInit,
      ): Promise<Response> => {
        if (!String(input).includes("/api/auth/me")) return defaultFetch(input);
        authCalls += 1;
        if (authCalls === 1) {
          return new Promise((_resolve, reject) => {
            init?.signal?.addEventListener(
              "abort",
              () => reject(new DOMException("Aborted", "AbortError")),
              { once: true },
            );
          });
        }
        return secondResponse;
      },
    ),
  );

  render(
    <StrictMode>
      <App />
    </StrictMode>,
  );

  await waitFor(() => expect(authCalls).toBe(2));
  expect(screen.getByText("正在确认身份…")).toBeVisible();
  expect(screen.queryByLabelText("用户名")).not.toBeInTheDocument();

  await act(async () => resolveSecond(currentUserResponse()));
  expect(
    await screen.findByRole("heading", { name: "文件入库" }),
  ).toBeVisible();
});
