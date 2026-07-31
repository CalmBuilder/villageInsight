import { useCallback, useEffect, useMemo, useState } from "react";
import { RouterProvider } from "react-router-dom";
import {
  ApiError,
  getCurrentUser,
  logout,
  type CurrentUser,
} from "./lib/api";
import { LoginPage } from "./pages/LoginPage";
import { createAppRouter } from "./router";

const AUTH_RETRY_DELAY_MS = 1_500;

function isAbortError(cause: unknown): boolean {
  return (
    typeof cause === "object"
    && cause !== null
    && "name" in cause
    && cause.name === "AbortError"
  );
}

export function App() {
  const [currentUser, setCurrentUser] = useState<CurrentUser | null | undefined>();
  const [authUnavailable, setAuthUnavailable] = useState(false);
  const [authRetryKey, setAuthRetryKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let retryTimer: number | undefined;
    getCurrentUser(controller.signal)
      .then((user) => {
        setAuthUnavailable(false);
        setCurrentUser(user);
      })
      .catch((cause: unknown) => {
        if (isAbortError(cause)) return;
        if (cause instanceof ApiError && cause.status === 401) {
          setAuthUnavailable(false);
          setCurrentUser(null);
          return;
        }
        setAuthUnavailable(true);
        retryTimer = window.setTimeout(
          () => setAuthRetryKey((key) => key + 1),
          AUTH_RETRY_DELAY_MS,
        );
      });
    return () => {
      controller.abort();
      if (retryTimer !== undefined) window.clearTimeout(retryTimer);
    };
  }, [authRetryKey]);

  const signOut = useCallback(async () => {
    await logout().catch(() => undefined);
    setCurrentUser(null);
  }, []);

  const router = useMemo(
    () => currentUser ? createAppRouter(currentUser, signOut) : null,
    [currentUser, signOut],
  );

  if (currentUser === undefined) {
    return (
      <div className="route-pending" role="status">
        {authUnavailable ? "服务正在恢复，正在重新连接…" : "正在确认身份…"}
      </div>
    );
  }
  if (currentUser === null || router === null) {
    return <LoginPage onAuthenticated={setCurrentUser} />;
  }
  return <RouterProvider router={router} />;
}
