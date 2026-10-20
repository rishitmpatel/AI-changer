# AI Switch

Move your **memory**, and optionally your **chat history**, from one AI assistant to another, in either direction.

**ChatGPT · Claude · Gemini · Grok · Microsoft Copilot · Perplexity · Meta AI · DeepSeek · Le Chat (Mistral)**

## Download → extract → double-click

1. Open **[Releases](https://github.com/rishitmpatel/AI-changer/releases/latest)** and download
   **`AI-Switch-Windows.zip`** (Windows 10/11) or **`AI-Switch-macOS.zip`** (macOS 11+).
2. **Extract** the zip (right-click → *Extract All…* on Windows).
3. **Double-click `AI Switch.exe`** in the extracted `AI Switch` folder. On a Mac, drag *AI Switch* to
   Applications, then right-click it → **Open**.

That's it: no Python, no terminal, no installer. AI Switch uses the **Microsoft Edge** (built into Windows)
or **Google Chrome** you already have. If neither exists, it offers to download Chromium for you.

> **"Windows protected your PC"?** The app isn't code-signed yet, so Microsoft SmartScreen shows this the
> first time. Click **More info → Run anyway**.
> **macOS "can't be opened"?** Right-click the app → **Open** → **Open**. If macOS says the app is
> damaged, run `xattr -cr "/Applications/AI Switch.app"` once in Terminal.

### Using it

1. **Pick From and To.** Click the AI your memory is in now and the AI it should go to (⇄ swaps them).
   Turn on *Also move chat history* if the target supports it.
2. **Click Start.** If you're not signed in to one of them, a *Sign in* button appears. It opens that AI in
   AI Switch's own browser window, where you sign in normally (Google sign-in, passkeys and 2FA all work).
   AI Switch notices when you're signed in, or you can just close that window. You only do this once per AI.
3. **Check before sending.** AI Switch lists any phone numbers, emails, addresses or ID numbers it found.
   Ticked items are removed. Use the 👁 icon to see one in full, and *Preview* to edit or delete any fact.
4. **Done.** You see "Moved X of Y facts". Anything the target didn't recall has already been re-sent once,
   and *Retry missing* tries again. *Open output folder* shows `memory.json`, `memory.md` and the reports.

The app stops for you only at the privacy check, a sign-in, a "verify you are human" check, or if every
automatic method failed. In that last case a card tells you exactly where to paste, and the app continues
by itself once it sees you did.

**Privacy:** your memory goes only to the two AI websites you picked. No analytics, no other servers.
"Update fixes" (Settings) downloads page-selector files from this repository and sends nothing about you.

### Where AI Switch keeps things

| What | Windows | macOS |
|---|---|---|
| Settings, history, sign-ins (browser profile), logs | `%APPDATA%\AI Switch` | `~/Library/Application Support/AI Switch` |
| Your exported memory and reports | `Documents\AI Switch` (change in Settings) | `~/Documents/AI Switch` |

*Settings → Reset sign-ins* deletes the AI Switch browser profile. Your everyday browser is never touched.

---

# Command line and development

The same engine is available as the `ai-switch` command for scripting.

```
ai-switch move --from chatgpt --to claude --export ~/Downloads/chatgpt-export.zip --chats
```

1. **Export** from the source. Writes `memory.json` (common format) and a readable `memory.md`.
2. **Privacy review.** Lists phone numbers, emails, addresses and ID numbers (masked on screen). You choose which to remove before anything is sent.
3. **Import** into the target, split to fit its input box and rewritten in the form it accepts best.
4. **Verify.** Asks the target "What do you know about me?", reports which facts are missing, and offers to re-send them.

## Install

Python 3.11 or newer.

```bash
git clone <this repo> ai-switch && cd ai-switch
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[all]"                                 # PyYAML + Playwright + pyperclip
```

The CLI drives your installed Edge/Chrome over CDP (no browser download needed). Without one, add
`--playwright-browser` and run `python -m playwright install chromium` once.

Without Playwright the tool still works using export files and the clipboard:
`pip install -e .` and add `--offline` to any command.

To use your own Chrome or Chromium binary, set `AI_SWITCH_BROWSER_PATH=/path/to/chrome`.

## One-time login

AI Switch keeps its own browser profile (see the table above for where), separate from your everyday
browser. Sign in once per provider. Passkeys, 2FA and Google/Microsoft/Apple sign-in all work.

```bash
ai-switch login chatgpt
ai-switch login claude
```

Your Edge/Chrome opens normally, with no automation attached, so Google doesn't block the sign-in. Sign in
and AI Switch detects it from the login cookie, or you can just close the window. Later runs reuse the
session. How this works and why: [`docs/sign-in.md`](docs/sign-in.md).

## Commands

| Command | What it does |
|---|---|
| `ai-switch move --from A --to B [--export FILE] [--chats]` | the full 4-step migration |
| `ai-switch export --from A [--export FILE] [--chats]` | backup only: `memory.json`, `memory.md` (+ `chats.json`, `chats.md`) |
| `ai-switch import --to B --memory memory.json [--chats-file chats.json]` | import a backup you already have |
| `ai-switch verify --to B --memory memory.json [--retry]` | re-run verification later (Claude can take up to 24 h to absorb memory) |
| `ai-switch login A` | sign in once in the ai-switch browser |
| `ai-switch providers` | show each provider's fallback order |
| `ai-switch gui` | local web GUI on `http://127.0.0.1` (token-protected) |

Useful flags: `--offline` (never open a browser), `--redact all|none|email,phone,…` (skip the privacy
prompt), `--yes` (non-interactive), `--method ask_ai` (force a fallback method, repeatable),
`--headless`, `--no-strict-network`, `--out DIR`, `--max-chats N`.

Exit codes: `0` ok · `1` a step failed (the message names the step and what to do) · `2` verification found missing facts · `130` cancelled.

## How each provider is handled

Each adapter tries these methods in order and falls back automatically:

1. **Official export file**: parses the zip/json/csv you downloaded (no network)
2. **Official in-app flow**: e.g. Claude *Settings → Memory → Start import*, Gemini *Import memory / Import chats*, Mistral *Memories → Import*
3. **Ask the AI**: sends a prompt asking the source to print everything it remembers between `BEGIN-MEMORY`/`END-MEMORY` markers, then captures the reply. On the target side it sends "Please remember …" messages.
4. **Browser automation** (Playwright, persistent profile): reads or fills settings fields such as custom instructions, Saved info or profile boxes
5. **Clipboard + pause**: copies the text and tells you exactly where to paste and which button to click

Full research, with sources, is in [`docs/providers.md`](docs/providers.md).

### Getting each provider's export file (optional but best)

| Source | Where to get the file | Example |
|---|---|---|
| ChatGPT | Settings → Data controls → **Export data** (emailed zip) | `ai-switch move --from chatgpt --to claude --export chatgpt-export.zip` |
| Claude | Settings → Privacy → **Export data** (emailed zip; includes `memories.json`) | `ai-switch move --from claude --to chatgpt --export claude-data.zip` |
| Gemini | takeout.google.com → My Activity → only *Gemini Apps* → JSON | `ai-switch move --from gemini --to claude --export takeout.zip` |
| Grok | grok.com → Settings → Data controls → **Export account data** | `ai-switch move --from grok --to gemini --export grok-data.zip` |
| Copilot | account.microsoft.com/privacy → Copilot → **Export activity history** (CSV) | `ai-switch move --from copilot --to mistral --export Copilot-Activity-History.csv` |
| Perplexity | no self-serve export: skip `--export`, ask-the-AI is used | `ai-switch move --from perplexity --to claude` |
| Meta AI | Accounts Center → **Download your information** (JSON) | `ai-switch move --from meta --to gemini --export facebook-info.zip` |
| DeepSeek | Settings → Data → **Export data** (or email service@deepseek.com) | `ai-switch move --from deepseek --to claude --export deepseek.zip` |
| Mistral | no documented export: ask-the-AI or the Memories page | `ai-switch move --from mistral --to chatgpt` |

Without `--export`, the tool goes straight to methods 2–5, using your login.

### Examples for each direction

Any pair works: `--from` and `--to` each accept every provider (aliases: `openai`, `anthropic`,
`google`, `xai`, `microsoft`, `pplx`, `meta`, `lechat`/`vibe`). Some representative runs:

```bash
# ChatGPT ⇄ Claude
ai-switch move --from chatgpt --to claude --export ~/Downloads/chatgpt.zip
ai-switch move --from claude  --to chatgpt --export ~/Downloads/claude.zip

# Claude ⇄ Gemini, with chat history (Gemini gets an official .zip chat import)
ai-switch move --from claude --to gemini --chats --export ~/Downloads/claude.zip
ai-switch move --from gemini --to claude --export ~/Downloads/takeout.zip

# Grok / Copilot / Perplexity / Meta AI / Mistral → anything (ask-the-AI, no file needed)
ai-switch move --from grok --to mistral
ai-switch move --from copilot --to gemini
ai-switch move --from perplexity --to chatgpt
ai-switch move --from meta --to claude
ai-switch move --from mistral --to grok

# DeepSeek has no memory: facts are derived from your chats; as a target it gets a context primer
ai-switch move --from deepseek --to claude --export ~/Downloads/deepseek.zip
ai-switch move --from claude --to deepseek

# Fully offline (export file in, clipboard out, you paste)
ai-switch move --from chatgpt --to perplexity --export chatgpt.zip --offline
```

## Output files

Each run creates `ai-switch-output/<timestamp>-<from>-to-<to>/`. The folder is readable only by you (0700/0600):

| File | Contents |
|---|---|
| `memory.json` | common-format memory (schema: `ai_switch/schema/memory.schema.json`) |
| `memory.md` | the same memory, readable |
| `chats.json`, `chats.md` | chat history (with `--chats`) |
| `privacy_review.md` | every identifier found, with full values, and whether it was removed |
| `import_parts/<target>-<method>/part-NN.txt` | exactly what was (or should be) pasted into the target |
| `chats_for_<target>/` | chat history rewritten for the target (`conversations_chatgpt_format.zip`, `chat_history.md`, one `.md` per chat) |
| `verify_answer.txt`, `verify_report.md` | the target's answer and the recalled/missing comparison |
| `run.log` | step names, methods and counts only, never memory content |

### Common memory format

```json
{
  "schema_version": "1.0",
  "generated_at": "2026-10-20T10:00:00+00:00",
  "source_provider": "chatgpt",
  "categories": {
    "profile":     [{"id": "f-01cfb7fd", "text": "Is a backend engineer who writes Rust.", "source_provider": "chatgpt", "date": "2026-02-14", "origin": "export_file"}],
    "preferences": [],
    "people":      [],
    "projects":    [],
    "topics":      []
  }
}
```

## Privacy and network rules

- Memory and chat content goes only to the local output folder and to the target you chose. The terminal, the GUI and `run.log` show steps, counts and **masked** identifiers (`a***@e***.com`, `+1 *********134`). A test checks this.
- During `move`, `export`, `import` and `verify`, the browser runs in **strict network mode**. Requests to any host outside the provider's `allowed_domains` in `selectors.yaml` are blocked. That covers analytics, ad pixels and other third-party scripts. Blocked hosts are counted in `run.log`. Some providers embed Cloudflare's bot check (`challenges.cloudflare.com`), so it is allowed for those sites. Remove it from `allowed_domains` if you prefer, but the site may break. `--no-strict-network` turns the filter off.
- The Python code makes no network requests of its own. The only network traffic is the browser loading the provider sites.
- Non-interactive runs (`--yes`) remove **all** detected identifiers unless you pass `--redact none` or a list of types.

## Fixing a broken selector

Websites change. When a step fails, the message says which **selector key** failed and on which site:

```
! ChatGPT: method 2 official in-app flow → could not find 'manage_memories' on ChatGPT (tried 3 selector(s))
      hint: The site layout probably changed. Click 'Update fixes', or edit selectors.manage_memories in …/AI Switch/selectors/chatgpt.yaml
```

Before giving up on a selector, AI Switch tries to **self-heal**: it looks for the element by role, label,
placeholder, aria-label or visible text (for example a button named "Add to memory"). If that fails, it
falls back to the next method on its own, so the run usually still finishes.

**Easiest fix: Settings → Update fixes.** This downloads the latest `selectors/*.yaml` from this repository's
`main` branch into `<app data>/fixes/`. Downloaded files can change selectors and flows but never the
allowed domains, and every URL must stay on the provider's own site. If the repository is private, paste a
read-only GitHub token under *Private repository?*.

To fix a selector yourself:

1. Open the page in Chrome, right-click the button or box → **Inspect**, and find a stable attribute (`data-testid`, `aria-label`, `placeholder`, or visible text).
2. Create an override file holding only the key that changed. It is merged over the built-in file and survives upgrades:

   ```yaml
   # %APPDATA%\AI Switch\selectors\chatgpt.yaml   (macOS: ~/Library/Application Support/AI Switch/selectors/, Linux: ~/.ai-switch/selectors/)
   selectors:
     manage_memories: 'button:has-text("Manage memory")'
   ```

   A value can be a list of alternatives, and the first visible match wins:
   `send_button: ['button[data-testid="send-button"]', 'button[aria-label="Send"]']`.
   Supported syntax: CSS, `text=…`, `button:has-text("…")`, `role=button[name="…"]`.
3. Re-run with `--method official_flow` (or whichever method failed) to test only that path.

URLs (`urls:`), size limits (`limits:`, `import:`), click-by-click flows (`flows:`) and the manual
instructions (`manual_steps:`) live in the same file, so when a site moves a button, the fix is a YAML edit.

## Adding a provider

Add **one file**, `ai_switch/adapters/<name>.py`. It is discovered automatically:

```python
from .base import BaseAdapter, Method

class NovaAdapter(BaseAdapter):
    id = "nova"
    name = "Nova"
    import_memory_methods = (Method.ASK_AI, Method.CLIPBOARD)
    DEFAULT_CONFIG = {            # or put the same keys in ai_switch/selectors/nova.yaml
        "urls": {"home": "https://nova.example/"},
        "selectors": {"logged_in": "#avatar", "chat_input": "textarea", "send_button": "#send",
                      "assistant_message": ".answer"},
        "manual_steps": {"import_memory": "Open nova.example → Settings → Memory → paste."},
    }
```

Override `parse_export(self, bundle)` if the provider has a documented export layout. Otherwise the
tolerant generic parser finds conversations and memory lists in any JSON/CSV. Add a fake export to
`tests/fixtures/<name>/` and a row in `tests/test_exports.py`.

## Desktop app internals

`ai_switch/desktop/` is the app: a local HTTP API on `127.0.0.1` (per-run token) plus the UI in
`ai_switch/web/app/`, shown in a native window by **pywebview** (WebView2 on Windows, WKWebView on macOS).
If no WebView is available, it opens in Edge/Chrome "app mode" instead. Run it from source with
`pip install -e ".[app]"` then `ai-switch-app`. Build it with `pip install -e ".[build]"` then
`pyinstaller packaging/ai-switch.spec`.

`AI Switch.exe --self-test report.json --with-browser` checks a build: adapters, selectors, UI assets, the
local server, the bundled Playwright driver, and a CDP round trip with the system browser. CI runs it, and
also opens the real window (`--smoke-seconds 15`) on every push. Pushing a tag such as `v1.0.0` publishes
both zips as a GitHub Release (`.github/workflows/build.yml`).

The older `ai-switch gui` (a minimal browser page) still works.

## Development

```bash
pip install -e ".[dev]"
pytest -q                 # every provider's sample export, privacy, chunking, fallbacks, the move flow,
                          # CLI, desktop API + UI (real Chromium), sign-in detection, self-healing, packaging
python tests/make_fixtures.py tests/fixtures   # regenerate the fake sample exports
```

The browser test (`tests/test_browser_e2e.py`) drives real Chromium against `tests/fake_site/`. It checks
the persistent profile, strict-network blocking, selector alternatives, streaming-reply detection, and
YAML flows. It is skipped when Playwright or Chromium is missing.

## Automation level by provider pair

**A** = fully automatic after one-time login · **S** = semi-automatic (automated, but the target chooses what to keep, or the source has no real memory) · **M** = needs a manual step

| from ↓ / to → | ChatGPT | Claude | Gemini | Grok | Copilot | Perplexity | Meta AI | DeepSeek | Mistral |
|---|---|---|---|---|---|---|---|---|---|
| **ChatGPT** | – | A | A | S | S | S | S | M | A |
| **Claude** | S | – | A | S | S | S | S | M | A |
| **Gemini** | S | A | – | S | S | S | S | M | A |
| **Grok** | S | A | A | – | S | S | S | M | A |
| **Copilot** | S | A | A | S | – | S | S | M | A |
| **Perplexity** | S | A | A | S | S | – | S | M | A |
| **Meta AI** | S | A | A | S | S | S | – | M | A |
| **DeepSeek** | S | S | S | S | S | S | S | – | S |
| **Mistral** | S | A | A | S | S | S | S | M | – |

Why:

- **A**: every source except DeepSeek can be read automatically (export file, memory page, or ask-the-AI), and **Claude, Gemini and Mistral have an official memory-import box** that the tool fills in.
- **S, target ChatGPT/Grok/Copilot/Perplexity/Meta AI**: none has an import box. The tool sends "Please remember …" messages and fills custom-instruction or profile fields, but the assistant decides what to store. Verify and retry closes the gaps.
- **S, source DeepSeek**: DeepSeek has no memory, so facts are *derived* from your own chat messages (export file or scraped chats). Expect fewer, rougher facts.
- **M, target DeepSeek**: no memory to write to. The tool opens a new chat with a context primer, which applies only in that chat, so you have to keep using it.
- **Chat history**: automatic into **Gemini** (official .zip import, from any source). Semi-automatic into **Claude** (uploaded to a new Project). Every other target is **manual**: the files are prepared and you attach them.
- **Gemini as target** works only for personal accounts outside the EEA, Switzerland and the UK. Elsewhere it falls back to Saved info (S), then the clipboard (M).
- The browser automation was built against current UI conventions and tested against a local fake site. It has **not** been run against the live sites, because that needs your accounts. If a selector is off on first run, the tool falls back to the next method and tells you which line to fix.

## License

MIT
