import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Login from "@/pages/Login";

// The page pulls `login` from the auth context; stub it so the render test
// stays a pure component test (no network, no provider tree).
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ login: vi.fn(), logout: vi.fn(), me: null, loading: false }),
}));

function renderLogin() {
  return render(
    <MemoryRouter>
      <Login />
    </MemoryRouter>,
  );
}

describe("<Login />", () => {
  it("renders the email, password and submit controls", () => {
    renderLogin();
    expect(screen.getByTestId("login-form")).toBeInTheDocument();
    expect(screen.getByTestId("email")).toBeInTheDocument();
    expect(screen.getByTestId("password")).toBeInTheDocument();
    expect(screen.getByTestId("login-submit")).toBeInTheDocument();
  });

  it("uses the correct input types for credentials", () => {
    renderLogin();
    expect(screen.getByTestId("email")).toHaveAttribute("type", "email");
    expect(screen.getByTestId("password")).toHaveAttribute("type", "password");
  });

  it("does not show an error message on first render", () => {
    renderLogin();
    expect(screen.queryByTestId("login-error")).not.toBeInTheDocument();
  });
});
