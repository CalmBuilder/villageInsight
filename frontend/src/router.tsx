import { lazy, Suspense, type ReactNode } from "react";
import { createBrowserRouter, Navigate } from "react-router-dom";
import { AppShell } from "./layout/AppShell";
import type { CurrentUser } from "./lib/api";

const BatchPage = lazy(() =>
  import("./pages/BatchPage").then((module) => ({ default: module.BatchPage })),
);
const CatalogPage = lazy(() =>
  import("./pages/CatalogPage").then((module) => ({ default: module.CatalogPage })),
);
const QuestionPage = lazy(() =>
  import("./pages/QuestionPage").then((module) => ({ default: module.QuestionPage })),
);
const NotFoundPage = lazy(() =>
  import("./pages/NotFoundPage").then((module) => ({ default: module.NotFoundPage })),
);
const SettingsPage = lazy(() =>
  import("./pages/SettingsPage").then((module) => ({ default: module.SettingsPage })),
);
const RecordsPage = lazy(() =>
  import("./pages/RecordsPage").then((module) => ({ default: module.RecordsPage })),
);
const ReviewsPage = lazy(() =>
  import("./pages/ReviewsPage").then((module) => ({ default: module.ReviewsPage })),
);
const AccessManagementPage = lazy(() =>
  import("./pages/AccessManagementPage").then((module) => ({
    default: module.AccessManagementPage,
  })),
);

function pending(element: ReactNode) {
  return (
    <Suspense fallback={<div className="route-pending">页面载入中…</div>}>
      {element}
    </Suspense>
  );
}

export function createAppRouter(
  currentUser: CurrentUser,
  onLogout: () => void,
) {
  const businessRoutes = [
    { index: true, element: pending(<BatchPage currentUser={currentUser} />) },
    { path: "batches", element: pending(<BatchPage currentUser={currentUser} />) },
    { path: "questions", element: pending(<QuestionPage currentUser={currentUser} />) },
  ];
  const governanceChildren = [
    { index: true, element: pending(<ReviewsPage />) },
    {
      path: "access",
      element: pending(<AccessManagementPage currentUser={currentUser} />),
    },
    { path: "reviews", element: pending(<ReviewsPage />) },
    { path: "records", element: pending(<RecordsPage />) },
    { path: "catalog", element: pending(<CatalogPage />) },
    { path: "settings", element: pending(<SettingsPage />) },
    { path: "*", element: pending(<NotFoundPage />) },
  ];
  const routes = currentUser.role === "platform_admin"
    ? [
        {
          path: "admin",
          element: <AppShell currentUser={currentUser} onLogout={onLogout} space="admin" />,
          children: governanceChildren,
        },
        {
          path: "batches",
          element: <AppShell currentUser={currentUser} onLogout={onLogout} space="user" />,
          children: [
            {
              index: true,
              element: pending(<BatchPage currentUser={currentUser} />),
            },
          ],
        },
        { path: "*", element: <Navigate replace to="/admin/reviews" /> },
      ]
    : [
        {
          element: <AppShell currentUser={currentUser} onLogout={onLogout} space="user" />,
          children: [
            ...businessRoutes,
            { path: "*", element: pending(<NotFoundPage />) },
          ],
        },
      ];
  return createBrowserRouter(routes);
}
