# How AI Switch signs in (and why)

Goal: sign in to each AI **once, ever**, in a way Google and the other sites accept, and let the app
remember it.

## The problems

1. **Google blocks automated browsers.** Sign-in pages (accounts.google.com, and every "Continue with
   Google" button) refuse browsers that show automation signals, especially `navigator.webdriver === true`
   ("This browser or app may not be secure"). Reports from 2025–2026 agree that no launch-flag combination
   reliably hides Playwright/Selenium, and that the robust approach is for the person to sign in in a
   normal, non-automated browser and reuse that session afterwards.
2. **Chrome 136+ won't automate your main profile.** `--remote-debugging-port` is ignored on the default
   user-data directory. It must be paired with a separate `--user-data-dir`.

## What we measured (Chromium 141, October 2026)

| Launch | `navigator.webdriver` |
|---|---|
| Playwright `launch()` | `true` |
| Normal launch, then Playwright `connect_over_cdp` | `true` |
| Normal launch with `--remote-debugging-port` only (raw CDP, nothing attached to the page) | **`true`** |
| **Normal launch, no debugging switch** | **`false`** |

So Chrome flags itself as automated as soon as the debugging port is enabled, even when nothing ever
touches the page. Launching "normally with `--remote-debugging-port`" isn't enough on its own.
`tests/test_browsers.py::test_plain_launch_looks_like_a_normal_browser` keeps this measurement in CI.

## The design

AI Switch uses **one dedicated profile** (`%APPDATA%\AI Switch\browser-profile`,
`~/Library/Application Support/AI Switch/browser-profile`) in your installed Edge or Chrome, in two modes:

| Mode | When | How it's launched | What AI Switch can see |
|---|---|---|---|
| **Sign-in** | you click *Sign in* | exactly like double-clicking Edge/Chrome: no debugging port, no automation | only the profile's cookie **names and expiry dates**, read from disk (never cookie values) |
| **Automation** | export / import / verify | same profile, relaunched with `--remote-debugging-port=0`, attached over CDP | the AI sites, as the signed-in you, in its own tab |

The sign-in flow:

1. *Sign in* opens the AI's login page in the **plain** browser. Google sees an ordinary browser, so
   Google sign-in, passkeys, 2FA and CAPTCHAs work as usual.
2. AI Switch watches the profile's cookie database for the site's session cookie (`login.cookies` in each
   `selectors/<provider>.yaml`, e.g. `sessionKey` for claude.ai). Chromium writes new cookies to disk within
   about 30 seconds. When it appears, AI Switch closes the browser itself and shows **Signed in ✓**.
3. **On Windows** a running browser may lock that database. Closing the window is then the signal: Edge
   and Chrome save cookies on a normal close, and AI Switch checks straight away.
4. Later runs reuse the profile, so you stay signed in until the site expires the session. *Settings →
   Reset sign-ins* deletes the profile.

During automation, a site may still show a "verify you are human" check (Cloudflare and others look at
`navigator.webdriver`). AI Switch doesn't try to hide automation. It detects the check, brings the browser
window to the front, shows *Quick check needed*, and continues by itself once the check is gone.

Trade-off, in one line: you might close one window per AI the first time on Windows, in exchange for a
sign-in that Google treats exactly like your normal browser.

## Sources

- [Chrome for Developers: Changes to remote debugging switches to improve security (Chrome 136)](https://developer.chrome.com/blog/remote-debugging-port)
- [GitHub issue: Google sign-in rejected because Playwright exposes navigator.webdriver](https://github.com/NavadeepDj/ContextPortal/issues/61)
- [GitHub issue: "This browser or app may not be secure", no flag combination guarantees success](https://github.com/skssmd/Ai-Browser-Toolkit/issues/1)
- [GitHub issue: can't log in to Google when using Playwright](https://github.com/executeautomation/mcp-playwright/issues/147)
- [GitHub issue: Google session cookies missing after Playwright-driven login (token binding)](https://github.com/teng-lin/notebooklm-py/issues/1753)
- [Browserless: How to use Chrome remote debugging](https://www.browserless.io/blog/chrome-remote-debugging)
