// Notification click handler, appended to the generated service worker
// (ADR-0037). `vite-plugin-pwa` runs in `generateSW` mode, which does not let us
// author the worker at all — `workbox.importScripts` is the one seam it offers,
// and this is the only thing that needs the seam.
//
// **Nothing checks this file.** It is outside `src/`, outside the TypeScript
// program, outside eslint (there is no eslint config — see the frontend gate)
// and outside design-lint's scan. That is the cost the ADR accepts, and it is
// why the file is one listener and nothing else. `vite.config.ts` names it and
// `src/lib/notify.test.ts` fails if this file stops registering
// `notificationclick`, which is the failure that would otherwise ship silently.
//
// Display is *not* here: the page calls `registration.showNotification(...)`
// itself, which routes through this worker's registration and is the only path
// that works on Android Chrome (`new Notification` throws there). The page is
// the side that knows the household and the connection; sending that to the
// worker just to have it call the same API adds a message hop that breaks
// whenever the page is not yet controlled.
self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  // Where the notification said to go. `data` is set by the page; the fallback
  // is the sync activity panel, which is what every notice so far is about.
  const data = event.notification.data || {};
  const url = typeof data.url === "string" && data.url ? data.url : "/admin";

  event.waitUntil(
    (async () => {
      const scope = self.registration.scope;
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      // A second tab showing the same app is the thing this avoids: the user
      // clicked a notification about a connection, and the answer is on a page
      // they very likely already have open.
      for (const client of windows) {
        if (!client.url.startsWith(scope)) continue;
        await client.focus();
        if (client.url !== new URL(url, scope).href) await client.navigate(url);
        return;
      }
      await self.clients.openWindow(url);
    })(),
  );
});
