import { lazy, Suspense, useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { getToken, UNAUTHORIZED_EVENT } from "./services/api";
import { Loading } from "./components/ui";
import { SignInPage } from "./pages/SignIn";

const named = <K extends string>(loader: () => Promise<Record<K, React.ComponentType>>, name: K) =>
  lazy(() => loader().then((m) => ({ default: m[name] })));
const OverviewPage = named(() => import("./pages/Overview"), "OverviewPage");
const RepositoriesPage = named(() => import("./pages/Repositories"), "RepositoriesPage");
const BuildsPage = named(() => import("./pages/Builds"), "BuildsPage");
const RunDetailPage = named(() => import("./pages/RunDetail"), "RunDetailPage");
const ReviewsPage = named(() => import("./pages/Reviews"), "ReviewsPage");
const AnalyticsPage = named(() => import("./pages/Analytics"), "AnalyticsPage");
const SettingsPage = named(() => import("./pages/Settings"), "SettingsPage");

export function App() {
  const [signedIn, setSignedIn] = useState(() => Boolean(getToken()));

  useEffect(() => {
    const handler = () => setSignedIn(false);
    window.addEventListener(UNAUTHORIZED_EVENT, handler);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, handler);
  }, []);

  if (!signedIn) return <SignInPage onSignedIn={() => setSignedIn(true)} />;

  return (
    <BrowserRouter>
      <Suspense fallback={<Loading />}>
      <Routes>
        <Route element={<Layout onSignOut={() => setSignedIn(false)} />}>
          <Route index element={<OverviewPage />} />
          <Route path="repositories" element={<RepositoriesPage />} />
          <Route path="builds" element={<BuildsPage />} />
          <Route path="runs/:runId" element={<RunDetailPage />} />
          <Route path="reviews" element={<ReviewsPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<OverviewPage />} />
        </Route>
      </Routes>
      </Suspense>
    </BrowserRouter>
  );
}
