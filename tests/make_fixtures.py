"""Generate realistic *fake* export fixtures for every provider (no real data)."""
import json, csv, io, sys
from pathlib import Path

ROOT = Path(sys.argv[1])

def w(path, obj):
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(obj, (dict, list)):
        p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    else:
        p.write_text(obj, encoding="utf-8")

# ---------------- ChatGPT: conversations.json with mapping tree, a regenerated branch, hidden system msg
def gpt_conv(cid, title, t0, turns, branch=False):
    mapping = {"client-created-root": {"id": "client-created-root", "message": None, "parent": None, "children": []}}
    parent = "client-created-root"
    sysid = f"{cid}-sys"
    mapping[sysid] = {"id": sysid, "parent": parent, "children": [], "message": {
        "id": sysid, "author": {"role": "system", "name": None, "metadata": {}}, "create_time": None,
        "content": {"content_type": "text", "parts": [""]}, "status": "finished_successfully",
        "metadata": {"is_visually_hidden_from_conversation": True}}}
    mapping[parent]["children"].append(sysid); parent = sysid
    t = t0
    for i, (role, text) in enumerate(turns):
        nid = f"{cid}-{i}"
        t += 30
        mapping[nid] = {"id": nid, "parent": parent, "children": [], "message": {
            "id": nid, "author": {"role": role, "name": None, "metadata": {}}, "create_time": t,
            "update_time": None, "content": {"content_type": "text", "parts": [text]},
            "status": "finished_successfully", "end_turn": role == "assistant", "weight": 1.0,
            "metadata": {"model_slug": "gpt-5"} if role == "assistant" else {}, "recipient": "all"}}
        mapping[parent]["children"].append(nid)
        if branch and i == 1:  # an abandoned regenerated answer that must NOT be picked
            alt = f"{cid}-alt"
            mapping[alt] = {"id": alt, "parent": parent, "children": [], "message": {
                "id": alt, "author": {"role": "assistant"}, "create_time": t,
                "content": {"content_type": "text", "parts": ["(old regenerated answer)"]}, "metadata": {}}}
            mapping[parent]["children"].append(alt)
        parent = nid
    return {"title": title, "create_time": t0, "update_time": t, "mapping": mapping, "moderation_results": [],
            "current_node": parent, "plugin_ids": None, "conversation_id": cid, "conversation_template_id": None,
            "gizmo_id": None, "is_archived": False, "safe_urls": [], "default_model_slug": "gpt-5", "id": cid}

gpt = [
    gpt_conv("6740a1b2-0001", "Trip planning Lisbon", 1747000000, [
        ("user", "My name is Alex Rivera and I live in Austin. Can you plan 4 days in Lisbon in June?"),
        ("assistant", "Here is a 4-day Lisbon itinerary: Day 1 Alfama and Castelo de São Jorge…"),
        ("user", "I prefer vegetarian restaurants and quiet neighbourhoods."),
        ("assistant", "Noted — here are vegetarian picks in Príncipe Real and Campo de Ourique…"),
    ], branch=True),
    gpt_conv("6740a1b2-0002", "Rust borrow checker help", 1749000000, [
        ("user", "I'm a backend engineer and I'm working on a Rust CLI called tidyfs that cleans duplicate files."),
        ("assistant", "Great project. The borrow error comes from holding `&mut self` across the loop…"),
        ("user", "Thanks! Please keep answers concise with code first."),
        ("assistant", "Sure. ```rust\nfor entry in walker { … }\n```"),
    ]),
]
w("chatgpt/conversations.json", gpt)
w("chatgpt/user.json", {"id": "user-FAKE000000000000", "email": "alex.rivera@example.com", "chatgpt_plus_user": True,
                        "birth_year": None, "phone_number": None})
w("chatgpt/message_feedback.json", [])
w("chatgpt/shared_conversations.json", [])
w("chatgpt/model_comparisons.json", [])
w("chatgpt/chat.html", "<html><body><script>var jsonData = [];</script></body></html>")

# ChatGPT variant with a memories file (newer/hand-added export)
w("chatgpt_with_memory/conversations.json", gpt[:1])
w("chatgpt_with_memory/user.json", {"id": "user-FAKE000000000000"})
w("chatgpt_with_memory/memories.json", [
    {"id": "mem_01", "content": "Is a backend engineer who mostly writes Rust and Go.", "created_at": "2026-02-14T09:12:00Z"},
    {"id": "mem_02", "content": "Prefers concise answers with code first.", "created_at": "2026-03-02T18:40:00Z"},
    {"id": "mem_03", "content": "Partner is named Sam; they have a dog called Miso.", "created_at": "2026-03-20T07:05:00Z"},
    {"id": "mem_04", "content": "Is building tidyfs, a Rust CLI that removes duplicate files.", "created_at": "2026-04-11T21:30:00Z"},
    {"id": "mem_05", "content": "Interested in bouldering and Portuguese cooking.", "created_at": "2026-05-01T12:00:00Z"},
    {"id": "mem_06", "content": "Phone number is +1 415 555 0134 for SMS reminders.", "created_at": "2026-05-03T12:00:00Z"},
])

# ---------------- Claude: conversations.json, users.json, projects.json, memories.json
w("claude/users.json", [{"uuid": "8f1e0000-0000-4000-8000-000000000001", "full_name": "Alex Rivera",
                         "email_address": "alex.rivera@example.com", "verified_phone_number": None}])
w("claude/conversations.json", [
    {"uuid": "c0a1-0001", "name": "Quarterly OKR draft", "created_at": "2026-04-02T10:00:00.000000Z",
     "updated_at": "2026-04-02T10:20:00.000000Z", "account": {"uuid": "8f1e0000-0000-4000-8000-000000000001"},
     "chat_messages": [
        {"uuid": "m1", "text": "Help me draft OKRs for the platform team I manage at Northwind.", "sender": "human",
         "created_at": "2026-04-02T10:00:00.000000Z", "updated_at": "2026-04-02T10:00:00.000000Z",
         "content": [{"type": "text", "text": "Help me draft OKRs for the platform team I manage at Northwind."}],
         "attachments": [], "files": []},
        {"uuid": "m2", "text": "", "sender": "assistant", "created_at": "2026-04-02T10:00:20.000000Z",
         "content": [{"type": "thinking", "thinking": "internal"},
                     {"type": "text", "text": "Here is a draft with three objectives: reliability, developer velocity…"}],
         "attachments": [], "files": []},
     ]},
    {"uuid": "c0a1-0002", "name": "Empty chat", "created_at": "2026-04-05T10:00:00Z", "chat_messages": []},
])
w("claude/projects.json", [{"uuid": "p-01", "name": "tidyfs", "description": "Rust CLI", "docs": []}])
w("claude/memories.json", [{
    "conversations_memory": (
        "**Work context**\n"
        "Alex is a staff backend engineer at Northwind Labs who manages the platform team. "
        "They write mostly Rust and Go.\n\n"
        "**Personal context**\n"
        "Alex lives in Austin, Texas with their partner Sam and a dog named Miso. "
        "They enjoy bouldering and Portuguese cooking.\n\n"
        "**Top of mind**\n"
        "Alex is building tidyfs, an open-source Rust CLI that removes duplicate files, aiming for a 1.0 release in July.\n\n"
        "**Preferences**\n"
        "- Prefers concise answers with code first\n"
        "- Uses metric units\n"),
    "project_memories": {"p-01": "tidyfs uses the walkdir crate and targets Linux and macOS."},
    "account_uuid": "8f1e0000-0000-4000-8000-000000000001"}])

# ---------------- Gemini: Google Takeout My Activity (JSON)
w("gemini/Takeout/archive_browser.html", "<html><body>Takeout archive</body></html>")
w("gemini/Takeout/My Activity/Gemini Apps/MyActivity.json", [
    {"header": "Gemini Apps", "title": "Prompted Suggest a 3-day bouldering training plan for an intermediate climber",
     "time": "2026-05-10T08:15:22.123Z", "products": ["Gemini Apps"], "activityControls": ["Gemini Apps Activity"],
     "safeHtmlItem": [{"html": "<p>Here is a 3-day plan:</p><ul><li>Day 1: limit bouldering</li><li>Day 2: rest</li></ul>"}]},
    {"header": "Gemini Apps", "title": "Prompted I live in Austin and work as a backend engineer. What are good weekend hikes nearby?",
     "time": "2026-05-10T08:30:00.000Z", "products": ["Gemini Apps"],
     "safeHtmlItem": [{"html": "<p>Try the Barton Creek Greenbelt &amp; Hamilton Pool…</p>"}]},
    {"header": "Gemini Apps", "title": "Used Gemini Apps", "time": "2026-05-11T09:00:00.000Z", "products": ["Gemini Apps"]},
    {"header": "Gemini Apps", "title": "Prompted My partner Sam loves ramen. Recipes?", "time": "2026-05-12T19:00:00.000Z",
     "products": ["Gemini Apps"], "safeHtmlItem": [{"html": "<p>Shoyu ramen: …</p>"}]},
    {"header": "Search", "title": "Searched for weather austin", "time": "2026-05-12T19:05:00.000Z", "products": ["Search"]},
])

# ---------------- Grok: account data zip (undocumented layout; Mongo-style dates)
w("grok/prod-grok-backend.json", {"conversations": [
    {"conversation": {"id": "grok-conv-01", "title": "Startup name ideas", "create_time": "2026-06-01T12:00:00Z",
                      "modify_time": "2026-06-01T12:10:00Z"},
     "responses": [
        {"response": {"_id": "r1", "message": "I'm a backend engineer building a dev-tools startup. Name ideas?",
                      "sender": "human", "create_time": {"$date": {"$numberLong": "1748779200000"}}}},
        {"response": {"_id": "r2", "message": "Here are 10 ideas: Forgeline, Patchwork, …", "sender": "ASSISTANT",
                      "create_time": {"$date": {"$numberLong": "1748779260000"}}}},
     ]},
], "projects": [], "tasks": []})
w("grok/prod-mc-auth-mgmt-api.json", {"user": {"email": "alex.rivera@example.com", "givenName": "Alex"}})

# ---------------- Copilot: privacy dashboard CSV
buf = io.StringIO()
cw = csv.writer(buf)
cw.writerow(["Conversation", "Time", "Author", "Message"])
cw.writerow(["Kitchen renovation", "2026-03-04T17:02:11Z", "User", "I live in Austin and I'm renovating my kitchen. Budget is $15k."])
cw.writerow(["Kitchen renovation", "2026-03-04T17:02:30Z", "AI", "Here's how to split a $15k kitchen budget…"])
cw.writerow(["Kitchen renovation", "2026-03-04T17:05:00Z", "User", "I prefer open shelving over upper cabinets."])
cw.writerow(["Kitchen renovation", "2026-03-04T17:05:20Z", "AI", "Open shelving works well if…"])
cw.writerow(["Email draft", "2026-03-06T08:00:00Z", "User", "Remember that my manager is Priya and I report weekly on Fridays."])
cw.writerow(["Email draft", "2026-03-06T08:00:10Z", "AI", "Got it. I'll remember that."])
w("copilot/Copilot-Activity-History.csv", buf.getvalue())

# ---------------- Perplexity: data request (generic JSON threads + memory list)
w("perplexity/threads.json", {"threads": [
    {"uuid": "pplx-th-01", "title": "Best e-bikes under $2000", "created_at": "2026-02-01T10:00:00Z",
     "entries": [
        {"role": "user", "content": "Best e-bikes under $2000 for a 10 km commute?", "created_at": "2026-02-01T10:00:00Z"},
        {"role": "assistant", "content": "Top picks: Aventon Level.2, Ride1Up 700…", "created_at": "2026-02-01T10:00:09Z"},
     ]}]})
w("perplexity/memories.json", {"memories": [
    {"text": "Commutes 10 km by bike to work", "created_at": "2026-02-01T10:00:00Z"},
    {"text": "Prefers sources from peer-reviewed journals", "created_at": "2026-02-03T10:00:00Z"},
]})

# ---------------- Meta AI: Download Your Information (mojibake UTF-8 as latin-1)
def moj(s):
    return s.encode("utf-8").decode("latin-1")
w("meta_ai/your_facebook_activity/meta_ai/ai_conversations.json", {"conversations_v2": [
    {"title": moj("Café recommendations"), "id": "meta-01", "timestamp": 1746100000,
     "messages": [
        {"sender_name": "Alex Rivera", "author": "user", "timestamp_ms": 1746100000000,
         "content": moj("Find me a quiet café near Zilker Park — I work remotely as a backend engineer.")},
        {"sender_name": "Meta AI", "author": "Meta AI", "timestamp_ms": 1746100005000,
         "content": moj("Try Café Medici on South Lamar…")},
     ]}]})
w("meta_ai/your_facebook_activity/meta_ai/ai_memories.json", {"memories": [
    {"value": moj("Works remotely as a backend engineer"), "timestamp": 1746100000},
    {"value": moj("Loves café culture and quiet places to work"), "timestamp": 1746100100},
]})

# ---------------- DeepSeek: conversations.json (mapping + fragments)
def ds_conv(cid, title, msgs):
    mapping = {"root": {"id": "root", "parent": None, "children": ["1"], "message": None}}
    for i, (kind, text) in enumerate(msgs, 1):
        mapping[str(i)] = {"id": str(i), "parent": "root" if i == 1 else str(i - 1),
                           "children": [str(i + 1)] if i < len(msgs) else [],
                           "message": {"files": [], "model": "deepseek-chat", "inserted_at": "2026-07-01T09:00:00.000000+08:00",
                                       "fragments": [{"type": kind, "content": text}] + (
                                           [{"type": "THINK", "content": "internal reasoning"}] if kind == "RESPONSE" else [])}}
    return {"id": cid, "title": title, "inserted_at": "2026-07-01T09:00:00.000000+08:00",
            "updated_at": "2026-07-01T09:10:00.000000+08:00", "mapping": mapping}
w("deepseek/conversations.json", [
    ds_conv("ds-01", "SQL window functions", [
        ("REQUEST", "I'm a data analyst at Contoso. Explain ROW_NUMBER vs RANK."),
        ("RESPONSE", "ROW_NUMBER gives unique sequential numbers; RANK leaves gaps on ties…"),
        ("REQUEST", "I prefer examples in PostgreSQL."),
        ("RESPONSE", "Sure, here is a PostgreSQL example…"),
    ])])
w("deepseek/user.json", {"user_id": "ds-user-0001", "email": "alex.rivera@example.com", "mobile": None})

# ---------------- Mistral Le Chat: generic chats + memories
w("mistral/chats.json", [
    {"id": "lc-01", "title": "French phrases", "createdAt": "2026-08-01T10:00:00Z",
     "messages": [
        {"role": "user", "content": "I'm learning French for a move to Lyon next year.", "createdAt": "2026-08-01T10:00:00Z"},
        {"role": "assistant", "content": "Bonne idée ! Here are 20 phrases for daily life…", "createdAt": "2026-08-01T10:00:05Z"},
     ]}])
w("mistral/memories.json", [
    {"content": "Is learning French ahead of a move to Lyon in 2027", "createdAt": "2026-08-01T10:00:00Z"},
    {"content": "Prefers replies in English with French examples", "createdAt": "2026-08-02T10:00:00Z"},
])

# ---------------- "ask the AI" replies as captured from each assistant
w("replies/chatgpt_memory_reply.txt", """Sure — here is everything I have saved about you:

```
BEGIN-MEMORY
## Profile
- [2026-02-14] Is a staff backend engineer at Northwind Labs who mostly writes Rust and Go.
- [2026-02-20] Lives in Austin, Texas.
## Preferences
- [2026-03-02] Prefers concise answers with code first.
- Uses metric units.
## People
- [2026-03-20] Partner is named Sam; they have a dog called Miso.
## Projects
- [2026-04-11] Is building tidyfs, an open-source Rust CLI that removes duplicate files, targeting a 1.0 release in July.
## Topics
- [2026-05-01] Interested in bouldering and Portuguese cooking.
- [2026-05-03] Contact email is alex.rivera@example.com and phone +1 415 555 0134.
END-MEMORY
```
Let me know if you'd like me to forget anything.""")
w("replies/claude_verify_answer.txt", """Here's what I know about you:
- You're a staff backend engineer at Northwind Labs, mostly writing Rust and Go.
- You live in Austin, Texas.
- You like concise answers with code first, and you use metric units.
- You're building tidyfs, a Rust CLI for removing duplicate files, aiming for 1.0 in July.
- You enjoy bouldering.""")
print("fixtures written to", ROOT)
