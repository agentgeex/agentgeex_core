/**
 * providers/dataProvider.ts — Refine DataProvider (connects to the Win3 BFF)
 *
 * [2026-10-01 read-only wiring to the real backend]
 *   - Wraps the BFF address (VITE_API_URL) with `@refinedev/simple-rest`,
 *     honoring the contract `_page/_limit/_sort/_order` + `X-Total-Count` (see api-contract-draft.md §0).
 *   - Injects an axios instance carrying the JWT (the interceptor adds Authorization automatically).
 *   - Zero page-layer changes: this file is the 'single data exit' described in architecture.md §3.
 *
 * Note: AgentGeeX business pages currently go through the semantic hooks in `providers/agentgeex.ts`
 *      (reading the BFF snapshot); this DataProvider serves Refine-native useList/useOne scenarios
 *      (case pages / later CRUD).
 */
import simpleRestProvider from "@refinedev/simple-rest";
import type { DataProvider } from "@refinedev/core";
import axios from "axios";
import { API_BASE, getToken } from "@/providers/api";

/** Axios instance carrying the JWT (simple-rest v4's httpClient param). */
const httpClient = axios.create({ baseURL: API_BASE });

httpClient.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    config.headers = config.headers ?? {};
    (config.headers as Record<string, string>)["Authorization"] = `Bearer ${token}`;
  }
  return config;
});

export const dataProvider: DataProvider = simpleRestProvider(
  API_BASE,
  httpClient,
) as unknown as DataProvider;
