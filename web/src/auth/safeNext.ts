/**
 * The client-side mirror of `accounts/providers/__init__.py::safe_next`.
 *
 * `?next=` came straight off the URL and into `navigate()` unvalidated. react-router 7 refuses
 * an external target, so it was never an open redirect — it was worse-looking: the promise
 * rejected, nothing caught it, and someone who landed on `/sign-in?next=//evil.example` (a
 * phishing link, a pasted absolute URL, a stale bookmark) typed their password, was
 * authenticated server-side, and watched *nothing at all* happen. No error, no navigation, no
 * sign that they were now signed in. Submitting again repeated it.
 *
 * The rule is the backend's, character for character: one leading `/`, not followed by another
 * `/` or a `\` (both of which a browser reads as the start of a protocol-relative URL), then
 * only characters that can legally appear in a path, query or fragment.
 */

const SAFE_NEXT = /^\/(?![/\\])[A-Za-z0-9._~!$&'()*+,;=:@%/?#-]{0,511}$/;
const DISALLOWED = ["\r", "\n", "\t", "\0"];

/** A same-origin path safe to hand to `navigate()`, or `"/"`. */
export function safeNext(raw: string | null | undefined): string {
  if (!raw) return "/";
  let candidate = raw;
  try {
    // A doubly-encoded `%2f%2fevil.example` decodes to `//evil.example`; check what the
    // browser would end up with, not what the query string literally spells.
    candidate = decodeURIComponent(raw);
  } catch {
    return "/";
  }
  if (DISALLOWED.some((character) => candidate.includes(character))) return "/";
  if (!SAFE_NEXT.test(candidate)) return "/";
  return candidate;
}
