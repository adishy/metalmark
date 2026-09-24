// Agent tokens (ADR-0048): issue, list, revoke. The admin half of agent access;
// the agent half — `/api/agent` and `/api/anon_debug` — is called by agents with
// a bearer token and never by this app.
//
// Invalidation, not polling: nothing changes a token except these mutations,
// and `last_used_at` is not worth a timer — it is read when the card is opened.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { AgentToken, AgentTokenCreate, AgentTokenCreated, UUID } from "@/api/types";

const TOKENS = ["agent-tokens"] as const;

export function useAgentTokens() {
  return useQuery({
    queryKey: TOKENS,
    queryFn: () => api.get<AgentToken[]>("/admin/agent-tokens"),
  });
}

export function useCreateAgentToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AgentTokenCreate) =>
      api.post<AgentTokenCreated>("/admin/agent-tokens", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: TOKENS }),
  });
}

export function useRevokeAgentToken() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: UUID) => api.del<AgentToken>(`/admin/agent-tokens/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: TOKENS }),
  });
}
