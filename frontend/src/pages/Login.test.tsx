import { describe, it, expect, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Login from "@/pages/Login";

// The page pulls `login` from the auth context; stub it so the render test
// stays a pure component test (no network, no provider tree).
const loginMock = vi.hoisted(() => vi.fn());
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ login: loginMock, logout: vi.fn(), me: null, loading: false }),
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

  it("is marked up so a password manager can find and fill it", () => {
    renderLogin();
    const form = screen.getByTestId("login-form");
    expect(form).toHaveAttribute("method", "post");
    expect(form).toHaveAttribute("action", "/api/auth/login");
    const user = screen.getByLabelText("Email");
    expect(user).toHaveAttribute("name", "username");
    expect(user).toHaveAttribute("autocomplete", "username");
    expect(user).toHaveAttribute("autocapitalize", "none");
    const pw = screen.getByLabelText("Password");
    expect(pw).toHaveAttribute("name", "password");
    expect(pw).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByTestId("login-submit")).toHaveAttribute("type", "submit");
  });

  it("signs in with what a password manager wrote, even with no input events", async () => {
    // Bitwarden and iOS AutoFill set `.value` by script. A controlled input can
    // miss that write; the form reads the DOM on submit, so it cannot.
    loginMock.mockResolvedValue(undefined);
    renderLogin();
    (screen.getByLabelText("Email") as HTMLInputElement).value = "owner@example.com";
    (screen.getByLabelText("Password") as HTMLInputElement).value = "hunter2hunter2";
    fireEvent.submit(screen.getByTestId("login-form"));
    await waitFor(() =>
      expect(loginMock).toHaveBeenCalledWith("owner@example.com", "hunter2hunter2"),
    );
  });

  it("shows the mark above the name, decoratively", () => {
    // The sign-in page is outside the shell, so it renders the mark itself
    // rather than inheriting the header's. Same decorative rule as there: the
    // h1 underneath already says the name.
    renderLogin();

    const mark = screen.getByTestId("metalmark-mark");
    expect(mark).toHaveAttribute("src", "/favicon.svg");
    expect(mark).toHaveAttribute("alt", "");
  });

  it("does not show an error message on first render", () => {
    renderLogin();
    expect(screen.queryByTestId("login-error")).not.toBeInTheDocument();
  });
});
