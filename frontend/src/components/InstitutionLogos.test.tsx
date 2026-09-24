import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import AccountMark from "@/components/AccountMark";
import { InstitutionLogosProvider } from "@/components/InstitutionLogos";

vi.mock("@/api/institutions", async () => {
  const actual = await vi.importActual<typeof import("@/api/institutions")>("@/api/institutions");
  return {
    ...actual,
    useInstitutions: () => ({
      data: [
        { name: "Chase Bank", key: "chase bank", fetchable: true, has_logo: true,
          logo_source: "fetched", logo_updated_at: "2026-09-24T00:00:00Z" },
        { name: "Tiny CU", key: "tiny cu", fetchable: false, has_logo: false,
          logo_source: null, logo_updated_at: null },
      ],
    }),
  };
});

describe("institution logos on account marks", () => {
  it("draws the household's logo, from this server, keeping the account's name", () => {
    render(
      <InstitutionLogosProvider>
        <AccountMark name="Checking" institution="CHASE  bank" />
      </InstitutionLogosProvider>,
    );
    const mark = screen.getByRole("img", { name: /Checking/ });
    const img = screen.getByTestId("account-mark-logo");
    expect(img.getAttribute("src")).toMatch(/^\/api\/institutions\/chase%20bank\/logo\?v=/);
    expect(mark).toContainElement(img);
  });

  it("falls back to initials without a logo, and when one fails to load", () => {
    render(
      <InstitutionLogosProvider>
        <AccountMark name="Pot" institution="Tiny CU" />
        <AccountMark name="Savings Account" institution="Chase Bank" />
      </InstitutionLogosProvider>,
    );
    expect(screen.getByRole("img", { name: /Pot/ })).toHaveTextContent("PO");
    fireEvent.error(screen.getByTestId("account-mark-logo"));
    expect(screen.queryByTestId("account-mark-logo")).not.toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Savings Account/ })).toHaveTextContent("SA");
  });

  it("draws initials outside the provider, as before", () => {
    render(<AccountMark name="Checking" institution="Chase Bank" />);
    expect(screen.queryByTestId("account-mark-logo")).not.toBeInTheDocument();
  });
});
