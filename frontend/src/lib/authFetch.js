// Global fetch interceptor for auth expiry.
//
// The app makes fetch() calls from many components with no shared wrapper, so
// there was nowhere to notice that the session had ended. Rather than rewrite
// every call site, we wrap window.fetch ONCE: when a request to our own API
// comes back 401 (or 403 on an authenticated call), we treat the session as
// expired -- clear it and dispatch a `session-expired` event that App listens
// for to redirect to /login with a message.
//
// Scoped carefully: only same-API responses to requests that carried an
// Authorization header trigger logout, so a public endpoint's 403 (or a
// third-party request) never boots the user.

import { getApiBase } from "./apiBase";
import { clearSession } from "./auth";

let installed = false;
let firing = false; // debounce a burst of parallel 401s into one logout

export const SESSION_EXPIRED_EVENT = "session-expired";

export function installAuthFetch() {
  if (installed) return;
  installed = true;

  const apiBase = getApiBase();
  const originalFetch = window.fetch.bind(window);

  window.fetch = async (input, init = {}) => {
    const response = await originalFetch(input, init);

    try {
      const url = typeof input === "string" ? input : input?.url || "";
      const isApiCall = url.startsWith(apiBase) || url.startsWith("/api");

      // Did this request carry an auth token? (Header can live on init or on a
      // Request object.) We only log out on failures of authenticated calls.
      const authHeader =
        init?.headers?.Authorization ||
        init?.headers?.authorization ||
        (input instanceof Request ? input.headers.get("Authorization") : null);

      const isAuthExpiry =
        isApiCall &&
        !!authHeader &&
        (response.status === 401 || response.status === 403);

      if (isAuthExpiry && !firing) {
        firing = true;
        clearSession();
        window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
        // Allow future logins to arm the handler again.
        setTimeout(() => { firing = false; }, 1000);
      }
    } catch {
      // Never let the interceptor's own error break a real response.
    }

    return response;
  };
}
