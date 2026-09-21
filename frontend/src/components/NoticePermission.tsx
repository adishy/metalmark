// The desktop-notification control, on its own because of what it has to *say*
// rather than what it does.
//
// It lives here rather than inside `pages/Admin.tsx` for one reason: the four
// answers a browser can give have to be told apart, and one of them — permission
// granted, no service worker to display through — is unreachable from both jsdom
// and the e2e stack (neither has `navigator.serviceWorker` at all), so it can be
// tested only by rendering this component directly. That branch is not
// hypothetical: it is the state `docker compose up` puts a granted browser in.

import { useEffect, useState } from "react";

import { useRefreshNotices } from "@/api/sync";
import { Button } from "@/components/form";
import * as notify from "@/lib/notify";
import type { NotifySupport } from "@/lib/notify";

/**
 * The desktop-notification permission, asked for from a click and from nowhere
 * else (ADR-0037 §4).
 *
 * A browser permission prompt that appears unbidden is the one everyone
 * reflexively denies, and a denial is permanent for the origin — so the request
 * lives behind a button, and the button is preceded by a sentence saying what
 * will be sent. That sentence is the point of the control: *the institution and
 * what went wrong, never an amount*, which is the same limit the notice's body
 * is built to (ADR-0037 §6).
 *
 * Every state here renders a sentence rather than a control, and the four matter
 * for the same reason. Granted splits in two, because permission and *delivery*
 * are different questions: a page with no registered service worker shows
 * nothing whatever the permission says, and `vite dev` — which is what
 * `docker compose up` runs — has no worker at all. So "on" is only said when
 * there is something to be on about (see `notify.canShow`). Denied cannot be
 * re-asked — a page may call `requestPermission()` as often as it likes after a
 * denial and the browser answers "denied" without prompting, so the copy has to
 * say where the fix actually is rather than offering a control that does
 * nothing. Unsupported is the browser saying it has no such facility here at
 * all, and it is the one a self-hosted deployment over http:// is most likely
 * to meet.
 */
export default function NoticePermission() {
  // The browser's answer, which can change without this page being told — a user
  // who unblocks the site in their browser's own settings should not have to
  // reload to see that said. So it is read per render, and the click's own
  // answer (if there is one) takes precedence for this session.
  const [answered, setAnswered] = useState<NotifySupport | null>(null);
  const state = answered ?? notify.support();
  const refresh = useRefreshNotices();
  // Whether a notification could actually be displayed, as opposed to merely
  // allowed. `null` is "not answered yet" — one microtask in the ordinary case,
  // and on a page whose worker is still registering as long as that takes.
  // Renders nothing meanwhile, which is the one state that cannot be wrong.
  const [showable, setShowable] = useState<boolean | null>(null);

  useEffect(() => {
    if (state !== "granted") {
      setShowable(null);
      return;
    }
    let live = true;
    const check = () => {
      void notify.canShow().then((yes) => {
        if (live) setShowable(yes);
      });
    };
    check();
    // Asked once, and then again when a worker becomes *ready* — because
    // registration is asynchronous and outlives the first paint, so the first
    // answer can honestly be "no worker yet" on a page that is about to have
    // one. Without this the panel would say "cannot" on a production build for
    // the rest of the session: nothing else re-runs this effect, since `state`
    // did not change. `ready` settles exactly when a worker is active for this
    // page, and on a page that never gets one — every `vite dev` session — it
    // simply never settles, leaving the correct answer standing.
    void navigator.serviceWorker.ready.then(check).catch(() => {
      /* never ready is the answer, not an error */
    });
    return () => {
      live = false;
    };
  }, [state]);

  // `unsupported` is a real answer, and it is not a rare one: the API sits
  // behind a secure-context gate, so `http://<lan-host>:5173` — the ordinary way
  // to reach a self-hosted app — has no `navigator.serviceWorker` at all. A
  // control that does nothing is worse than a sentence that explains the
  // absence, so this branch says which two things are missing rather than
  // quietly rendering a panel that will never notify.
  if (state === "unsupported") {
    return (
      <p className="mt-2 text-xs text-fg-muted" data-testid="notify-state">
        This browser cannot show desktop notifications for this page. They need https:// or
        localhost, and a browser that supports them.
      </p>
    );
  }

  if (state === "granted") {
    // One microtask after a grant, and one render after a page load with
    // permission already granted.
    if (showable === null) return null;
    if (showable) {
      return (
        <p className="mt-2 text-xs text-fg-muted" data-testid="notify-state">
          Desktop notifications are on. A bank connection that breaks will say so here.
        </p>
      );
    }
    return (
      <p className="mt-2 text-xs text-fg-muted" data-testid="notify-state">
        This browser is allowed to show notifications, but this page cannot: they are displayed
        by the app's service worker, which only a production build has. Everything a notice
        would have said is in the run history below.
      </p>
    );
  }

  if (state === "denied") {
    return (
      <p className="mt-2 text-xs text-warning" data-testid="notify-state">
        Desktop notifications are blocked for this site. A page cannot ask twice — the
        permission has to be changed in this browser's own settings for the site.
      </p>
    );
  }

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <Button
        variant="secondary"
        onClick={async () => {
          const next = await notify.requestPermission();
          setAnswered(next);
          // The poll has been running all along and declining to show anything
          // while permission was missing, so ask it again now rather than
          // leaving the first notice to wait out the rest of the minute.
          if (next === "granted") void refresh();
        }}
        data-testid="notify-enable"
      >
        Enable desktop notifications
      </Button>
      <span className="text-xs text-fg-muted">
        A connection that breaks will say so here — the institution and what went wrong, and no
        amount or account.
      </span>
    </div>
  );
}
