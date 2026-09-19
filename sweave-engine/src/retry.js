// Provider-call retry (opencode `session/retry.ts` lesson, zero-dep port).
//
// When: the sidecar's fetch to the provider fails transiently — rate
// limit (429), server blips (5xx), or the network path drops
// (internet cut, DNS, reset, timeout). The turn waits and retries
// instead of failing loudly on the first blip.
//
// Never retried: auth (401/403, bad key, quota-exhausted), DETERMINISTIC
// bad request (400 with no transient text — wrong-transport flavor
// errors included), context overflow. A 400 carrying transient text
// (overload/unavailable blips the gateway surfaces as 400, observed
// live: "Model is unavailable") DOES retry. Those fail the turn
// immediately with their own named error.
//
// Delays mirror opencode: honor `retry-after-ms` / `retry-after`
// response headers when present, else 2s * 2^(n-1) + 25% jitter,
// capped at 30s. `maxRetries` counts retries AFTER the first attempt
// (default 3 → up to 4 tries), matching opencode's RETRY_MAX_RETRIES
// semantics. Abort-aware throughout: a turn stop settles immediately,
// never sleeps out a backoff.

export const RETRY_INITIAL_DELAY_MS = 2000;
export const RETRY_BACKOFF_FACTOR = 2;
export const RETRY_JITTER_FACTOR = 0.25;
export const RETRY_MAX_DELAY_NO_HEADERS_MS = 30000;
export const DEFAULT_MAX_RETRIES = 3;

const RETRYABLE_STATUS = new Set([408, 429, 500, 502, 503, 504, 524]);

// Message-shape subset of opencode's RETRYABLE_MESSAGE_PATTERNS (the
// patterns our providers actually emit; SDK-vendor prefixes omitted).
const RETRYABLE_PATTERNS = [
  /408|429|500|502|503|504|524/i,
  /rate increased too quickly|rate limit|rate-limit|rate_limit|too many requests/i,
  /overloaded|service unavailable|service_unavailable|service-unavailable|unavailable|internal error|internal_error|internal server error|server error|server_error|server-error|provider returned error|provider_returned_error|provider-returned-error/i,
  /terminated|fetch failed|failed to fetch|network[-_\s]error|upstream connect|connection error|connection refused|connection lost|socket connection was closed|socket hang up|reset before headers|getaddrinfo|enotfound|eai_again|econnrefused|econnreset|etimedout/i,
  /^timeout$|\b(?:request|response|connection|network|stream|read) (?:timeout|timed out|time out)\b/i,
  /try your request again|retry your request|resource exhausted|resource_exhausted/i,
  /\btry again (?:later|in\b)|\b(?:currently|temporarily) at capacity\b/i,
];

// Terminal even when the status looks retryable: quota gone (retrying
// burns round-trips against a wall), bad credentials, overflowed
// context (retrying the same prompt re-overflows).
const NEVER_PATTERNS = [
  /insufficient_quota|usage_not_included|freeusagelimit|gousagelimit|quota.?exceeded/i,
  /invalid.?api.?key|incorrect api key|authentication|unauthorized|forbidden/i,
  /context.?overflow|maximum context|too long|context length/i,
];

export function isRetryableStatus(status) {
  return RETRYABLE_STATUS.has(status);
}

function matchesAny(text, patterns) {
  return typeof text === "string" && patterns.some((p) => p.test(text));
}

export function isRetryableMessage(text) {
  if (!text || typeof text !== "string") return false;
  if (matchesAny(text, NEVER_PATTERNS)) return false;
  return matchesAny(text, RETRYABLE_PATTERNS);
}

/**
 * True when a failed provider attempt is worth retrying.
 * `err` shape: { status?, headers?, message? }. Auth-shaped and
 * never-pattern failures return false even on retryable statuses.
 */
export function isRetryable(err) {
  if (!err || typeof err !== "object") return isRetryableMessage(String(err || ""));
  const status = err.status;
  if (status === 401 || status === 403) return false;
  const text = `${err.message || ""}\n${err.body || ""}`;
  if (matchesAny(text, NEVER_PATTERNS)) return false;
  // 400 is split: deterministic bad-requests (flavor errors, poisoned
  // history) fail fast; transient-text 400s (overload/unavailable
  // blips) ride the normal backoff.
  if (status === 400) return matchesAny(text, RETRYABLE_PATTERNS);
  if (typeof status === "number" && RETRYABLE_STATUS.has(status)) return true;
  return isRetryableMessage(text);
}

function headerValue(headers, name) {
  if (!headers) return null;
  try {
    if (typeof headers.get === "function") {
      const v = headers.get(name);
      return v === null || v === undefined ? null : String(v);
    }
    const lower = name.toLowerCase();
    for (const k of Object.keys(headers)) {
      if (k.toLowerCase() === lower) return String(headers[k]);
    }
  } catch {
    // Headers unreadable: fall through to exponential backoff.
  }
  return null;
}

/**
 * Backoff for retry number `attempt` (1-based: first retry == 1).
 * Honors `retry-after-ms` / `retry-after` (seconds or HTTP date)
 * when the provider sent them; else exponential + jitter, 30s cap.
 */
export function retryDelayMs(attempt, headers, random = Math.random) {
  const cap = (ms) => Math.min(ms, 2147483647);
  if (headers) {
    const msHint = headerValue(headers, "retry-after-ms");
    if (msHint !== null) {
      const parsed = Number.parseFloat(msHint);
      if (!Number.isNaN(parsed) && parsed >= 0) return cap(parsed);
    }
    const hint = headerValue(headers, "retry-after");
    if (hint !== null) {
      const asSeconds = Number.parseFloat(hint);
      if (!Number.isNaN(asSeconds) && asSeconds >= 0) {
        return cap(Math.ceil(asSeconds * 1000));
      }
      const asDate = Date.parse(hint) - Date.now();
      if (!Number.isNaN(asDate) && asDate > 0) return cap(Math.ceil(asDate));
    }
  }
  const base = RETRY_INITIAL_DELAY_MS * Math.pow(RETRY_BACKOFF_FACTOR, Math.max(0, attempt - 1));
  const capped = Math.min(base, RETRY_MAX_DELAY_NO_HEADERS_MS);
  return Math.ceil(capped + capped * RETRY_JITTER_FACTOR * random());
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/**
 * Run `fn` (one provider attempt, () => Promise) with retries.
 * `fn` throws on failure with an optional { status, headers, message,
 * body } shape (see isRetryable); AbortError always propagates
 * immediately. Resolves with the first success; throws the LAST error
 * when attempts run out. `onRetry({ attempt, waitMs, error })` observes
 * (logging), never controls.
 */
export async function withProviderRetry(fn, opts = {}) {
  const maxRetries = Math.max(0, opts.maxRetries ?? DEFAULT_MAX_RETRIES);
  const signal = opts.signal || null;
  const onRetry = opts.onRetry || null;
  let lastErr = null;
  for (let attempt = 0; attempt <= maxRetries; attempt += 1) {
    if (signal && signal.aborted) {
      throw Object.assign(new Error("aborted"), { code: "aborted" });
    }
    try {
      return await fn(attempt);
    } catch (err) {
      lastErr = err;
      if (err && (err.code === "aborted" || err.name === "AbortError")) throw err;
      if (attempt >= maxRetries || !isRetryable(err)) throw err;
      const waitMs = retryDelayMs(attempt + 1, err && err.headers);
      if (onRetry) {
        try {
          onRetry({ attempt: attempt + 1, waitMs, error: err });
        } catch {}
      }
      if (signal) {
        const aborted = await new Promise((resolve) => {
          if (signal.aborted) return resolve(true);
          const t = setTimeout(() => resolve(false), waitMs);
          signal.addEventListener("abort", () => {
            clearTimeout(t);
            resolve(true);
          }, { once: true });
        });
        if (aborted) {
          throw Object.assign(new Error("aborted"), { code: "aborted" });
        }
      } else {
        await sleep(waitMs);
      }
    }
  }
  throw lastErr;
}

/** Normalize a non-OK fetch response into a retryable-shaped error. */
export function providerHttpError(status, headers, bodyText) {
  const text = String(bodyText || "").slice(0, 500);
  const err = new Error(`provider ${status}: ${text}`);
  err.status = status;
  err.headers = headers || null;
  err.body = text;
  return err;
}
