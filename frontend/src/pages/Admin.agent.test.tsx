import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { AgentToken, AgentTokenCreate, AgentTokenCreated } from "@/api/types";
import { AgentAccess } from "@/pages/Admin";

const h = vi.hoisted(() => ({
  tokens: [] as unknown[],
  create: vi.fn(),
  revoke: vi.fn(),
}));

vi.mock("@/api/agent", () => ({
  useAgentTokens: () => ({ data: h.tokens, isPending: false, isError: false, error: null }),
  useCreateAgentToken: () => ({ mutate: h.create, isPending: false }),
  useRevokeAgentToken: () => ({ mutate: h.revoke, isPending: false }),
}));

const ACTIVE: AgentToken = {
  id: "t1",
  name: "Claude, debugging reports",
  prefix: "mmk_AbCdEfGh",
  scopes: ["agent:read", "debug:read"],
  created_at: "2026-09-20T10:00:00Z",
  expires_at: "2026-12-19T10:00:00Z",
  last_used_at: null,
  revoked_at: null,
  created_by: "Alex",
  status: "active",
};

const REVOKED: AgentToken = {
  ...ACTIVE,
  id: "t2",
  name: "Old agent",
  scopes: ["debug:read"],
  revoked_at: "2026-09-21T10:00:00Z",
  last_used_at: "2026-09-21T09:00:00Z",
  status: "revoked",
};

beforeEach(() => {
  h.tokens = [];
  h.create.mockReset();
  h.revoke.mockReset();
});

describe("AgentAccess", () => {
  it("lists tokens by name, prefix and scope, never by value", () => {
    h.tokens = [ACTIVE, REVOKED];
    render(<AgentAccess />);

    const active = screen.getByTestId("agent-token-t1");
    expect(within(active).getByText("Claude, debugging reports")).toBeInTheDocument();
    expect(within(active).getByText("mmk_AbCdEfGh…")).toBeInTheDocument();
    expect(within(active).getByText(/Agent API, Debug views/)).toBeInTheDocument();
    expect(within(active).getByText(/Never used/)).toBeInTheDocument();
    expect(within(active).getByText("active")).toBeInTheDocument();

    const revoked = screen.getByTestId("agent-token-t2");
    expect(within(revoked).getByText("revoked")).toBeInTheDocument();
    // A revoked token has nothing left to revoke.
    expect(within(revoked).queryByText("Revoke")).not.toBeInTheDocument();
    expect(screen.queryByTestId("agent-token-value")).not.toBeInTheDocument();
  });

  it("says so when there are no tokens", () => {
    render(<AgentAccess />);
    expect(screen.getByText("No tokens yet.")).toBeInTheDocument();
  });

  it("issues a token with the chosen scopes and expiry, and shows it once", () => {
    const created: AgentTokenCreated = { ...ACTIVE, id: "t3", token: "mmk_issued1" };
    h.create.mockImplementation(
      (_body: AgentTokenCreate, opts: { onSuccess: (t: AgentTokenCreated) => void }) =>
        opts.onSuccess(created),
    );
    render(<AgentAccess />);

    fireEvent.change(screen.getByTestId("agent-token-name"), { target: { value: " Claude " } });
    fireEvent.click(screen.getByTestId("agent-scope-debug:read"));
    fireEvent.change(screen.getByTestId("agent-token-expiry"), { target: { value: "30" } });
    fireEvent.click(screen.getByText("Issue token"));

    expect(h.create).toHaveBeenCalledWith(
      { name: "Claude", scopes: ["agent:read"], expires_in_days: 30 },
      expect.anything(),
    );
    expect(screen.getByTestId("agent-token-value")).toHaveValue("mmk_issued1");
    expect(screen.getByText(/not shown again/)).toBeInTheDocument();

    fireEvent.click(screen.getByText("Done"));
    expect(screen.queryByTestId("agent-token-value")).not.toBeInTheDocument();
  });

  it("refuses to issue a token with no name or no scope", () => {
    render(<AgentAccess />);
    // Submitted directly: the browser's own `required` check would stop a click
    // first, and this is the check behind it.
    fireEvent.submit(screen.getByTestId("agent-token-form"));
    expect(screen.getByTestId("agent-token-error")).toHaveTextContent(/Name the token/);

    fireEvent.change(screen.getByTestId("agent-token-name"), { target: { value: "x" } });
    fireEvent.click(screen.getByTestId("agent-scope-agent:read"));
    fireEvent.click(screen.getByTestId("agent-scope-debug:read"));
    fireEvent.submit(screen.getByTestId("agent-token-form"));
    expect(screen.getByTestId("agent-token-error")).toHaveTextContent(/at least one/);
    expect(h.create).not.toHaveBeenCalled();
  });

  it("shows the server's refusal", () => {
    h.create.mockImplementation((_b: unknown, opts: { onError: (e: Error) => void }) =>
      opts.onError(new Error("Administrator or owner role required")),
    );
    render(<AgentAccess />);
    fireEvent.change(screen.getByTestId("agent-token-name"), { target: { value: "x" } });
    fireEvent.click(screen.getByText("Issue token"));
    expect(screen.getByTestId("agent-token-error")).toHaveTextContent("owner role required");
  });

  it("revokes only after a confirmation", () => {
    h.tokens = [ACTIVE];
    render(<AgentAccess />);

    fireEvent.click(screen.getByTestId("agent-token-revoke-t1"));
    expect(h.revoke).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("Keep"));
    expect(screen.queryByTestId("agent-token-confirm-t1")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("agent-token-revoke-t1"));
    fireEvent.click(screen.getByTestId("agent-token-confirm-t1"));
    expect(h.revoke).toHaveBeenCalledWith("t1", expect.anything());
  });
});
