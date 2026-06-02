import type { Meta, RunDetail, RunListItem } from "./types";

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  if (res.status === 401) {
    // Session expired / missing — bounce to the login page.
    window.location.assign("/login");
    throw new Error("unauthorized");
  }
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export const getMeta = (): Promise<Meta> => getJson<Meta>("/api/meta");

export const listRuns = (): Promise<RunListItem[]> =>
  getJson<{ runs: RunListItem[] }>("/api/taskruns").then((r) => r.runs);

export const getRun = (id: string): Promise<RunDetail> =>
  getJson<RunDetail>(`/api/taskruns/${encodeURIComponent(id)}`);
