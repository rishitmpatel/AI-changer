# Provider research (October 2026)

What each assistant offers for **viewing/exporting memory**, its **data-export format**, and
how to **import memory** and **chat history**. This drives the method order in each adapter
(`ai_switch/adapters/<id>.py`) and the URLs/selectors/manual steps in `ai_switch/selectors/<id>.yaml`.

**How this was researched.** Web search plus official help pages, October 2026. Several official
help sites (help.openai.com, support.google.com, intercom.help, meta.com, blog.google) could not be
fetched from the build environment (DNS blocked). For those, the details come from search-engine
summaries of the official page and from recent third-party guides. Each row is tagged:

- **✓ official**: read on the provider's own page
- **≈ official (summary)**: the official page as summarised by search
- **3p**: third-party guides only; check against the live settings screen

UI labels change often. When one is wrong, the fix is a one-line edit in `selectors.yaml` (see README).

---

## ChatGPT (OpenAI)

| | Finding | Confidence |
|---|---|---|
| View memory | Settings → Personalization → **Manage memories**. Since June 2026 "Dreaming" curates memory in the background and shows a **memory summary**; users can switch back to saved memories. | 3p |
| Export memory | No documented memory export. Options: copy the Manage-memories list, or ask ChatGPT to print its memory. Sources disagree on whether a March 2026 update added a memory JSON. | 3p (conflicting) |
| Data export | Settings → **Data controls → Export data** → email with a zip (link valid 24 h). Contents: `conversations.json` (each chat is a `mapping` tree of nodes with `author.role`, `content.parts`, `create_time`, plus `current_node`), `chat.html`, `user.json`, `shared_conversations.json`, `message_feedback.json`. Large exports are split into numbered `conversations-NNN.json`. Free/Go/Plus/Pro accounts, not Business/Enterprise. | 3p, consistent |
| Import memory | **No official memory import.** ChatGPT saves memories when you ask in chat ("remember that …"). Custom instructions ("More about you", ~1,500 chars) take free text. | 3p |
| Import chats | None. Workaround: upload files to a Project or a chat. | 3p |

**Adapter:** export 1 → 2 (Manage memories) → 3 → 4 (custom-instruction fields) → 5. Import 3 ("Please remember …" messages) → 4 (More about you) → 5.
*Trade-off:* chat-based import relies on ChatGPT choosing to save each item. Verification catches the ones it skipped.

Sources: [OpenAI help: export](https://help.openai.com/en/articles/7260999-how-do-i-export-my-chatgpt-history-and-data) (not reachable from build env) ·
[OpenAI Memory FAQ](https://help.openai.com/en/articles/8590148-memory-faq) (not reachable) ·
[Bluehost: export ChatGPT conversations](https://www.bluehost.com/blog/how-to-export-chatgpt-conversations/) ·
[AI Toolbox: export ChatGPT to JSON (2026)](https://www.ai-toolbox.co/ai-toolbox-chatgpt-features/export-chatgpt-to-json-complete-guide) ·
[ChatGPT export format explained](https://ai-chat-importer.com/guides/chatgpt-export-format-explained) ·
[PCWorld: ChatGPT "dreaming"](https://www.pcworld.com/article/3158111/chatgpt-new-dreaming-feature-makes-it-way-better-at-remembering-you.html) ·
[EdTech Innovation Hub: new memory rollout](https://www.edtechinnovationhub.com/news/openai-rolls-out-new-chatgpt-memory-system-to-keep-personalization-current) ·
[Calmara: migrate ChatGPT memory](https://calmara.app/blog/migrate-chatgpt-memory-before-it-rewrites-itself)

## Claude (Anthropic)

| | Finding | Confidence |
|---|---|---|
| View memory | New UI: **Settings → Memory**. Legacy UI: Settings → Capabilities → "View and edit your memory". | ✓ official |
| Export memory | Copy it from that screen, or ask Claude in chat to "write out your memories verbatim". | ✓ official |
| Data export | Settings → **Privacy → Export data** → email with a zip (link valid 24 h): `conversations.json` (`uuid`, `name`, `chat_messages[]` with `sender` = human/assistant, `text`, `content[]`), `users.json`, `projects.json`, `memories.json` (`conversations_memory` markdown + `project_memories`). | 3p |
| Import memory | **Official.** Settings → Memory → **"Start import"** → paste → **"Add to memory"**. Legacy: Settings → Capabilities, or the "Import memory to Claude" card on the home screen. Anthropic publishes an extraction prompt at claude.com/import-memory. Free, Pro, Max, Team and Enterprise, on web and desktop. Experimental. Focuses on work topics. Can take up to 24 h to apply. | ✓ official |
| Import chats | No chat import. Upload exported conversations as Project knowledge (applies to that Project only). | 3p |

**Adapter:** export 1 (`memories.json`) → 2 → 3 → 4 → 5. Import 2 (Start import) → 3 → 4 ("Tell Claude what to change" box) → 5. Chats: upload `chat_history.md` to a new Project, or a manual step.
*Trade-off:* Claude may drop personal details that aren't about work, and imports land within 24 h, so verifying right away can under-report.

Sources: [Claude Help Center: importing and exporting your memory](https://support.claude.com/en/articles/12123587) ✓ ·
[claude.com/import-memory](https://claude.com/import-memory) ·
[Tom's Guide: move ChatGPT memory to Claude](https://www.tomsguide.com/ai/you-can-move-your-chatgpt-memory-to-claude-in-60-seconds-heres-how) ·
[SaveAI: export Claude conversations](https://saveai.net/blog/how-to-export-claude-conversations) ·
[Amit Kothari: export Claude projects data](https://amitkoth.com/export-claude-projects-data/) ·
[Techloy: switch ChatGPT → Claude](https://www.techloy.com/how-to-switch-from-chatgpt-to-claude-export-and-import-memory-step-by-step/)

## Gemini (Google)

| | Finding | Confidence |
|---|---|---|
| View memory | **Saved info** page (gemini.google.com/saved-info) plus personal context from past chats. | ≈ official |
| Export memory | Saved info has no export button. Copy the page, or ask Gemini. | ≈ official |
| Data export | **Google Takeout → My Activity → Gemini Apps** (choose JSON) → `Takeout/My Activity/Gemini Apps/MyActivity.json`, a list of `{header, title: "Prompted …", time, safeHtmlItem[].html}`. Picking the separate "Gemini" product exports only Gems. Without Gemini Apps Activity, nothing is saved. | 3p |
| Import memory | **Official (launched 27 March 2026).** Settings → **Import memory**: copy Gemini's suggested prompt, run it in your old AI, paste the answer back. Personal accounts only, 18+, not in the EEA, Switzerland or the UK, needs personalization on. | ≈ official |
| Import chats | **Official.** Upload a **.zip** chat export (ChatGPT/Claude layout documented), up to 5 GB, text only (no images/files), can take up to a day. Imported chats appear under Chats with an import icon. | ≈ official |

**Adapter:** export 1 (Takeout) → 2 (Saved info) → 3 → 5. Import 2 (Import memory) → 3 → 4 (Saved info, one line per item) → 5. Chats: 2 (we rewrite any source's chats into ChatGPT's `conversations.json` zip layout and upload it) → 5.
*Trade-off:* the import screen is region-locked. Outside the eligible regions the flow falls back to Saved info, which is slower because it adds one item at a time.

Sources: [Google Help: Import from other AI platforms to Gemini Apps](https://support.google.com/gemini/answer/16868299) (via search summary) ·
[gemini.google/import-memory](https://gemini.google/import-memory/) ·
[Google blog (MENA): bring your AI memories and chat history to Gemini](https://blog.google/intl/en-mena/product-updates/explore-get-answers/ai-memories-chat-history-to-gemini/) ·
[MacRumors: Gemini import tool](https://macrumors.com/2026/03/26/gemini-import-tool) ·
[PCWorld: Gemini can import ChatGPT/Claude history](https://www.pcworld.com/article/3100804/google-gemini-can-now-import-your-chatgpt-or-claude-chat-history.html) ·
[AlternativeTo news](https://alternativeto.net/news/2026/3/google-gemini-now-lets-users-import-memory-context-and-chat-history-from-other-ai-apps) ·
[xtrace: export Gemini conversations (Takeout)](https://xtrace.ai/blog/how-to-export-gemini-conversations)

## Grok (xAI)

| | Finding | Confidence |
|---|---|---|
| View memory | Memory is on by default on grok.com and the apps (not in parts of the EU/UK). Settings → **Data controls → Memory** toggle. Individual memories can be deleted from the chat UI. No list view. | 3p (+ TechCrunch launch) |
| Export memory | No memory export. Ask Grok. | 3p |
| Data export | grok.com → Settings → Data controls → **Export account data** (or accounts.x.ai/data) → zip of JSON covering all xAI products. Some guides say it downloads directly, others say a link is emailed. Layout is undocumented. | 3p |
| Import memory | "Import memory from other AI providers" was seen in testing but is not public. Today: chat ("remember …") and **Customize Grok** custom instructions. | 3p |
| Import chats | None. Attach a file to a chat or a Project. | 3p |

**Adapter:** export 1 (generic tolerant JSON parser) → 3 → 4 (custom instructions) → 5. Import 2 (activates automatically once an `import_memory` flow is added to `grok.yaml`) → 3 → 4 → 5.
*Trade-off:* the export layout is undocumented, so the parser searches for any message-like lists instead of relying on fixed file names.

Sources: [TechCrunch: xAI adds memory to Grok](https://techcrunch.com/2025/04/16/xai-adds-a-memory-feature-to-grok) ·
[Bluehost: Grok memory](https://www.bluehost.com/blog/?p=280653) ·
[MemoryPlugin: Grok → Claude](https://www.memoryplugin.com/switch/grok-to-claude) ·
[AI Toolbox: back up Grok conversations (2026)](https://www.ai-toolbox.co/grok-management-and-productivity/how-to-back-up-grok-conversations-2026) ·
[Univik: export Grok chats from accounts.x.ai](https://univik.com/help/export-grok-chats.html)

## Microsoft Copilot (consumer)

| | Finding | Confidence |
|---|---|---|
| View memory | Settings → **Personalization** → memory list (delete one or all). Turning memory off does not delete saved items. | ≈ official |
| Export memory | No memory export. Ask Copilot. | ≈ official |
| Data export | Microsoft's privacy statement says signed-in users can **view, export and delete Copilot activity history** from the privacy dashboard (account.microsoft.com/privacy → Copilot → export) → **CSV** of prompts and responses. | ✓ official (privacy statement) + 3p steps |
| Import memory | No import. Copilot remembers when told "Remember that …". | ≈ official |
| Import chats | None. | 3p |
| Work accounts | Microsoft 365 Copilot memory lives in a hidden Exchange folder and is exported by admins through eDiscovery. Out of scope. | ✓ official (Learn) |

**Adapter:** export 1 (CSV, chats only, so facts are derived from chats if 2/3 fail) → 2 → 3 → 5. Import 3 → 5.

Sources: [Microsoft Support: Copilot privacy controls](https://support.microsoft.com/en-US/Privacy/microsoft-copilot/privacy-controls) ·
[Microsoft Privacy Statement](https://www.microsoft.com/en-us/privacy/privacystatement) ·
[Microsoft Learn: Copilot personalization & memory](https://learn.microsoft.com/en-us/microsoft-365/copilot/copilot-personalization-memory) ·
[ElevenForum: export all Copilot activity history](https://www.elevenforum.com/t/export-all-copilot-activity-history-for-microsoft-account.50080/) ·
[Univik: export Copilot chats from the privacy dashboard](https://univik.com/help/export-copilot-chats.html)

## Perplexity

| | Finding | Confidence |
|---|---|---|
| View memory | Settings → **Personalize → Memory** (Manage: delete one or clear all). Incognito turns memory off. | ≈ official |
| Export memory | No export. Copy the list, or ask. | 3p |
| Data export | No documented self-serve account export. | 3p |
| Import memory | No import. Profile / "Introduce yourself" box (free text) and chat. | 3p |
| Import chats | None. Spaces can hold uploaded files. | 3p |

**Adapter:** export 1 (generic, for a data-request file) → 2 (memory page) → 3 → 4 (profile box) → 5. Import 3 → 4 → 5.

Sources: [Perplexity Help: Memory](https://intercom.help/perplexity-ai/en/articles/10968016-memory) (via search) ·
[MemoryPlugin: how Perplexity memory works](https://blog.memoryplugin.com/how-perplexity-memory-works/) ·
[DeleteMe: Perplexity privacy settings](https://joindeleteme.com/ai-privacy-settings/perplexity-privacy-settings-guide/) ·
[createwith: Perplexity memory across threads](https://www.createwith.com/tool/perplexity/updates/perplexity-adds-memory-across-threads-models-and-search-modes)

## Meta AI

| | Finding | Confidence |
|---|---|---|
| View memory | Meta AI app / meta.ai: profile picture → **Memory** (swipe to delete, or Manage). Messenger: Information → Memory. You can also ask it to forget something. | ≈ official |
| Export memory | No memory export. Ask, or copy the list. | ≈ official |
| Data export | Accounts Center → **Download your information** (JSON). Meta's JSON stores UTF-8 as latin-1 escapes ("mojibake"). Meta AI specifics are not documented. | 3p |
| Import memory | No import. Meta AI remembers details you tell it in chat. | ≈ official |
| Import chats | None. | — |

**Adapter:** export 1 (generic parser + mojibake repair) → 2 → 3 → 5. Import 3 → 5.

Sources: [Meta Help: delete remembered details on Meta AI](https://www.meta.com/help/artificial-intelligence/1152856459900216/) (via search) ·
[Android Authority: Meta AI memory in WhatsApp](https://androidauthority.com/meta-ai-whatsapp-memory-3492224) ·
[Croma: Meta AI memory explained](https://croma.com/unboxed/meta-ai-can-now-record-and-remember-details-about-you)

## DeepSeek

| | Finding | Confidence |
|---|---|---|
| View memory | **No cross-chat memory.** Each chat starts fresh. | 3p, consistent |
| Export memory | Not applicable. We derive facts from your own chat messages. | — |
| Data export | Sources conflict: a Settings → Data → **Export data** option (JSON) vs "no bulk export". Official fallback: email service@deepseek.com. The parser supports the ChatGPT-like `mapping` tree with typed `fragments` (REQUEST/RESPONSE/THINK) and generic JSON. | 3p (conflicting) |
| Import memory | No memory, so we send a **context primer** as the first message of a new chat. It applies to that chat only. | — |
| Import chats | None. Attach a file. | 3p |

**Adapter:** export 1 (derived from chats) → 4 (scrape recent chats, then derive) → 5. Import 3 (primer) → 5.
*Trade-off:* without memory, the imported context lasts only for the chat that holds the primer.

Sources: [AI Memory: export DeepSeek](https://aimemory.pro/blog/export-deepseek) ·
[AI Chat Importer: export DeepSeek conversations](https://ai-chat-importer.com/blog/how-to-export-deepseek-conversations) ·
[Univik: export DeepSeek chats](https://univik.com/help/export-deepseek-chats.html) ·
[llmnesia: export DeepSeek history](https://www.llmnesia.com/blog/how-to-export-deepseek-conversation-history) ·
[AI Memory: DeepSeek memory](https://aimemory.pro/blog/deepseek-memory)

## Mistral Le Chat (being renamed "Mistral Vibe")

| | Finding | Confidence |
|---|---|---|
| View memory | **Memories** (beta) under the Intelligence section: add, edit, delete. Available on the Free plan. | ✓ official (launch post) |
| Export memory | No export button. Read the list, or ask. | 3p |
| Data export | Conflicting: one guide cites an export page at admin.mistral.ai/account/export, others say there is no consumer chat export. | 3p (conflicting) |
| Import memory | **Official:** "fast import of your memories from ChatGPT" (paste). Adding single memories also works. | ✓ official (launch post) |
| Import chats | None. | 3p |
| Naming | One source says Le Chat was rebranded **Mistral Vibe** in May 2026. Adapter aliases: `mistral`, `lechat`, `vibe`. | 3p (single source) |

**Adapter:** export 1 (generic) → 2 (Memories page) → 3 → 4 → 5. Import 2 (Import) → 3 → 4 (add one by one) → 5.

Sources: [Mistral: Le Chat custom MCP connectors & Memories](https://mistral.ai/news/le-chat-mcp-connectors-memories/) ✓ ·
[The Decoder: Mistral memory in Le Chat](https://the-decoder.com/mistral-integrates-mcp-support-and-memory-in-le-chat/) ·
[Onoma: Mistral memory guide](https://askonoma.com/article/mistral-ai-memory-guide) ·
[llmnesia: export Mistral history](https://www.llmnesia.com/blog/how-to-export-mistral-conversation-history) ·
[Univik: export Le Chat conversations](https://univik.com/help/export-mistral-chats.html) ·
[Wikipedia: Mistral Vibe](https://en.wikipedia.org/wiki/Mistral_Vibe)

---

## Summary: which import route each target gets

| Target | Best memory route | Chat-history route |
|---|---|---|
| Claude | Official import box (Settings → Memory → Start import) | Project file upload |
| Gemini | Official Import memory, else Saved info | **Official .zip import** |
| Mistral | Official Memories import, else add one by one | file attachment |
| ChatGPT | "Please remember …" chat messages, else "More about you" | Project / file |
| Grok | chat messages, else Customize instructions | file |
| Copilot | "Remember that …" chat messages | file |
| Perplexity | chat, else profile box | Space files |
| Meta AI | "Remember that …" chat messages | paste |
| DeepSeek | context primer (no memory) | file |
