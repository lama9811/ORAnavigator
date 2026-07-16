// Shared auth helpers. Centralizes JWT decoding + expiry checks so the route
// guard and the API layer agree on what "logged in" means. Previously the app
// only checked whether a token *existed*, so an expired token still rendered
// the page and then every API call 403'd silently -- this fixes that.

export function parseJwt(token) {
  try {
    const b64 = token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/");
    const json = decodeURIComponent(
      atob(b64)
        .split("")
        .map((c) => "%" + ("00" + c.charCodeAt(0).toString(16)).slice(-2))
        .join("")
    );
    return JSON.parse(json);
  } catch {
    return {};
  }
}

// True only if a token exists AND its `exp` claim is still in the future.
// A small skew allowance avoids logging out a user whose clock is a few
// seconds ahead of the server.
export function isTokenValid(token) {
  if (!token) return false;
  const { exp } = parseJwt(token);
  if (!exp) return false; // no expiry claim -> treat as invalid, be safe
  const nowSec = Date.now() / 1000;
  const SKEW_SEC = 5;
  return exp > nowSec - SKEW_SEC;
}

// Read the current token from storage and validate it in one step.
export function hasValidToken() {
  return isTokenValid(localStorage.getItem("token"));
}

// Clear all session state. Kept here so logout on expiry and manual logout
// wipe exactly the same keys.
export function clearSession() {
  localStorage.removeItem("token");
  localStorage.removeItem("chat_sessions");
}
