import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

export interface Institution {
  name: string;
  key: string;
  fetchable: boolean;
  has_logo: boolean;
  logo_source: "fetched" | "uploaded" | null;
  logo_updated_at: string | null;
}

export interface FetchLogosResult {
  fetched: string[];
  failed: string[];
  unknown: string[];
  kept: number;
}

/** The key the server files a logo under: lower-case letters and digits,
 *  single spaces. Must match `services/institutions.normalise`. */
export function institutionKey(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
    .replace(/\s+/g, " ");
}

/** The logo's URL, from this server. Versioned by its timestamp so a new logo
 *  is fetched and an unchanged one comes from the browser's cache. */
export function logoUrl(inst: Pick<Institution, "key" | "logo_updated_at">): string {
  return `${API_BASE}/institutions/${encodeURIComponent(inst.key)}/logo?v=${encodeURIComponent(
    inst.logo_updated_at ?? "",
  )}`;
}

export function useInstitutions() {
  return useQuery({
    queryKey: ["institutions"],
    queryFn: () => api.get<Institution[]>("/institutions"),
    staleTime: 5 * 60_000,
  });
}

export function useUploadLogo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (v: { key: string; name: string; dataUrl: string }) =>
      api.put<Institution>(`/institutions/${encodeURIComponent(v.key)}/logo`, {
        name: v.name,
        data_base64: v.dataUrl,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["institutions"] }),
  });
}

export function useDeleteLogo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) => api.del<void>(`/institutions/${encodeURIComponent(key)}/logo`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["institutions"] }),
  });
}

export function useFetchLogos() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<FetchLogosResult>("/institutions/fetch-logos", {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["institutions"] }),
  });
}
