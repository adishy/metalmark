import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import NoticePermission from "@/components/NoticePermission";
import * as notify from "@/lib/notify";

// The four answers a browser can give, rendered. It matches the copy loosely —
// a substring, not the whole sentence — because the sentences are meant to be
// editable and the *branch* is the thing under test.

/** Put a browser under the component. `registration: false` is a browser that
 *  has the API but no worker: every `vite dev` session, and therefore every
 *  `docker compose up` deployment.
 *
 *  `answers` is what the prompt comes back with, and the stub applies it to
 *  `Notification.permission` itself — which is what a real browser does, and
 *  what makes the click tests below honest. A stub whose `permission` stayed
 *  "default" forever would let "does not claim success" pass for the wrong
 *  reason: the panel would say "cannot" because the stub still reported no
 *  permission, and never because there was no worker. */
function browser({
  permission = "default" as NotificationPermission,
  registration = false,
  answers = "granted" as NotificationPermission,
}: {
  permission?: NotificationPermission;
  registration?: boolean;
  answers?: NotificationPermission;
} = {}) {
  let current = permission;
  const requestPermission = vi.fn(async () => {
    current = answers;
    return current;
  });
  Object.defineProperty(window, "Notification", {
    configurable: true,
    value: {
      get permission() {
        return current;
      },
      requestPermission,
    },
  });
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: {
      getRegistration: vi.fn().mockResolvedValue(registration ? { showNotification: vi.fn() } : undefined),
    },
  });
  return { requestPermission };
}

function renderControl() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <NoticePermission />
    </QueryClientProvider>,
  );
}

const said = () => screen.getByTestId("notify-state").textContent ?? "";

beforeEach(() => notify.resetForTests());

afterEach(() => {
  Reflect.deleteProperty(window, "Notification");
  Reflect.deleteProperty(navigator, "serviceWorker");
  vi.restoreAllMocks();
});

describe("<NoticePermission />", () => {
  it("says the browser has no such facility when the API is absent", () => {
    // No stubs: jsdom, and any origin that is not a secure context — which is
    // how a self-hosted deployment over http:// is reached. A sentence, because
    // a button here could not do anything.
    renderControl();

    expect(said()).toMatch(/cannot show desktop notifications/i);
    expect(screen.queryByTestId("notify-enable")).not.toBeInTheDocument();
  });

  it("offers the button, and the sentence that justifies it, before anything is asked", () => {
    browser();
    renderControl();

    // The sentence is the point of the control (ADR-0037 §4): it says what a
    // notice will carry *before* the prompt appears, so granting is informed.
    expect(screen.getByTestId("notify-enable")).toBeInTheDocument();
    expect(screen.getByText(/no amount or account/i)).toBeInTheDocument();
    // And nothing has been asked for yet. An unbidden prompt is the one
    // everybody denies, and the denial is permanent.
    expect(window.Notification.requestPermission).not.toHaveBeenCalled();
  });

  it("says notifications are on only when there is a worker to show them through", async () => {
    browser({ permission: "granted", registration: true });
    renderControl();

    await waitFor(() => expect(said()).toMatch(/notifications are on/i));
  });

  it("refuses to say 'on' when the page has no worker, and points at the run history", async () => {
    // The bug this test exists for. Permission is granted, so the panel said
    // "Desktop notifications are on." — and `deliver()` can show nothing,
    // because display goes through `registration.showNotification` and `vite
    // dev` registers no worker. That is not a corner case: it is what
    // `docker compose up` runs, so it is the state the deployment's own user is
    // in the moment they click Enable. Saying "on" there is worse than saying
    // nothing, because they have just granted a permission and watched the app
    // confirm it worked.
    browser({ permission: "granted", registration: false });
    renderControl();

    await waitFor(() => expect(said()).toMatch(/cannot/i));
    expect(said()).not.toMatch(/are on/i);
    // And it says where the notices actually are, so the state is not a dead
    // end — the run history carries the same sentences.
    expect(said()).toMatch(/run history/i);
  });

  it("says where a denial can be undone, rather than offering a control", () => {
    browser({ permission: "denied" });
    renderControl();

    // A page may call `requestPermission()` after a denial as often as it likes
    // and the browser answers "denied" without prompting, so a button here
    // would be a control that does nothing.
    expect(said()).toMatch(/blocked/i);
    expect(screen.queryByTestId("notify-enable")).not.toBeInTheDocument();
  });

  it("does not claim success the moment a click is granted, on a page with no worker", async () => {
    // The moment the fix is most load-bearing. The user clicks, the prompt is
    // granted, `answered` takes precedence over the browser's own reading — and
    // this page still has no worker, so it still cannot show anything. Saying
    // "on" here is the worst version of the lie: the reader has just granted a
    // permission and watched the app confirm it worked.
    const { requestPermission } = browser({ permission: "default", registration: false });
    renderControl();

    await userEvent.setup().click(screen.getByTestId("notify-enable"));

    expect(requestPermission).toHaveBeenCalled();
    await waitFor(() => expect(said()).toMatch(/cannot/i));
    expect(said()).not.toMatch(/are on/i);
  });

  it("does claim success the moment a click is granted, on a page that has one", async () => {
    // The other half, so the test above cannot pass by the panel being unable
    // to say "on" at all.
    browser({ permission: "default", registration: true });
    renderControl();

    await userEvent.setup().click(screen.getByTestId("notify-enable"));

    await waitFor(() => expect(said()).toMatch(/notifications are on/i));
  });
});
