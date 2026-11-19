"""
model_test.py - which free AI models really work for ,assist with YOUR keys?

Run it where the bot runs (or any PC with Python 3, no extra packages):     python model_test.py
It reads the keys from .env, sends the same 5 requests to every candidate model and prints a score table.
The five tests are the ones ,assist got wrong in real chats:
  1. "say X whenever someone mentions Y"   -> must call add_auto_rule with action=reply (not delete)
  2. "send me an image of <real item>"      -> must call find_web_image (not generate_image)
  3. "mute sam for 10 minutes"              -> must call timeout_member, minutes=10
  4. "Hi" (with a .restore card in the chat) -> must NOT call a tool and NOT talk about the restore
  5. "get me a gif of ..."                  -> must call find_gif

Pick the models with 5/5 and the lowest time, then put them in .env, best first, e.g.
    ASK_PROVIDER=nvidia
    ASK_MODEL_NVIDIA=deepseek-ai/deepseek-v4.1-flash,z-ai/glm-5.3-flash
Only test your own candidates:   python model_test.py nvidia:z-ai/glm-5.3-flash gemini:gemini-3.8-flash
"""
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASES = {
    "nvidia": ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}
BASES.update(json.loads(os.getenv("MODEL_TEST_BASES", "{}")))  # (used by the self test)
CANDIDATES = [
    ("nvidia", "deepseek-ai/deepseek-v4.1-flash"), ("nvidia", "z-ai/glm-5.3-flash"), ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
    ("nvidia", "openai/gpt-oss-120b"), ("nvidia", "minimaxai/minimax-m3.1-flash"), ("nvidia", "stepfun-ai/step-5-preview"),
    ("nvidia", "nvidia/nemotron-3.5-lightning-30b-a3b"),
    ("gemini", "gemini-3.8-flash"), ("gemini", "gemini-3.5-flash"), ("gemini", "gemini-3.5-flash-lite"),
    ("mistral", "mistral-large-latest"), ("mistral", "mistral-small-latest"),
    ("groq", "openai/gpt-oss-120b"), ("groq", "qwen/qwen3.8-27b"),
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"), ("openrouter", "openrouter/free"),
]


def load_env():
    try:
        with open(os.path.join(HERE, ".env"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass


def fn(name, desc, props, req):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {"type": "object", "properties": props, "required": req}}}


S = {"type": "string"}
TOOLS = [
    fn("add_auto_rule", "Make a PERMANENT automatic rule: whenever a person says a word, the bot bans / kicks / mutes them, deletes the message, or ANSWERS it with a text (action=reply). "
       "'say Z whenever someone says Y' is action=reply with reply_text=Z. NEVER use delete for a say / reply request.",
       {"user": S, "word": S, "action": {"type": "string", "enum": ["ban", "kick", "timeout", "delete", "reply"]}, "reply_text": S}, ["word", "action"]),
    fn("find_web_image", "Find the REAL picture of something that exists (a Roblox / game item, a character, a person, a logo, a product) on the web and post it.", {"query": S}, ["query"]),
    fn("generate_image", "Make a NEW ORIGINAL picture (banner, poster, art). NEVER for a real thing that exists: use find_web_image.", {"prompt": S}, ["prompt"]),
    fn("timeout_member", "Mute (timeout) a member for some minutes.", {"user": S, "minutes": {"type": "number"}}, ["user", "minutes"]),
    fn("find_gif", "Search a GIF and post it. Use it for any gif / reaction / short meme clip request.", {"query": S}, ["query"]),
    fn("web_search", "Search the web for current facts.", {"query": S}, ["query"]),
    fn("send_message", "Post a message as the bot in a channel.", {"channel": S, "text": S}, ["text"]),
]
SYSTEM = ("You are the assistant of a Discord bot. The person asking runs a server and wants things looked up or done. Use the tools for REAL actions and never claim an action "
          "happened unless a tool result says so. Reply in 1-3 short casual sentences. Answer ONLY what the asker wrote: a greeting or small talk gets a few words back, never "
          "comment on the recent chat. For a picture of something that really exists call find_web_image, never generate_image. 'say Z whenever someone says Y' is add_auto_rule(action='reply').")
CHAT = "[Recent chat, context only]\n12:19 /imyy (bot): [embed: Restored | 26 people's reputation, 115 quest records, 3,004 saved assist messages]\n"


def check1(calls, text):
    c = [x for x in calls if x[0] == "add_auto_rule"]
    return bool(c) and str(c[0][1].get("action")).lower() == "reply" and bool(str(c[0][1].get("reply_text") or "").strip())


def check2(calls, text):
    return any(x[0] == "find_web_image" for x in calls) and not any(x[0] == "generate_image" for x in calls)


def check3(calls, text):
    c = [x for x in calls if x[0] == "timeout_member"]
    try:
        return bool(c) and float(c[0][1].get("minutes")) == 10
    except (TypeError, ValueError):
        return False


def check4(calls, text):
    return not calls and bool(text.strip()) and len(text) < 160 and not re.search(r"restor|reputation|quest|record", text, re.I)


def check5(calls, text):
    return any(x[0] == "find_gif" for x in calls)


TESTS = [
    ("rule: say X when Y", "say aria is obsese whenever someone mentions her name", check1),
    ("real image", "send me an image of oozing oscar Roblox item", check2),
    ("mute", "mute sam for 10 minutes", check3),
    ("just 'hi'", CHAT + "\nHi", check4),
    ("gif", "get me a gif of Ronaldo cheeky boy", check5),
]


def call(prov, model, question):
    base, key_env = BASES[prov]
    body = {"model": model, "temperature": 0.2, "max_tokens": 800, "tools": TOOLS,
            "messages": [{"role": "system", "content": SYSTEM + " Asker: tester. Server: test."}, {"role": "user", "content": question}]}
    if prov == "nvidia" and "nemotron" in model:
        body["chat_template_kwargs"] = {"thinking": False}
    for attempt in range(2):
        req = urllib.request.Request(base + "/chat/completions", data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": "Bearer " + os.getenv(key_env, ""), "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 0:
                time.sleep(8)
                continue
            if e.code in (400, 422) and "chat_template_kwargs" in body:  # model does not know the no-thinking switch
                body.pop("chat_template_kwargs")
                continue
            detail = ""
            try:
                detail = str(json.load(e))[:90]
            except Exception:
                pass
            return None, None, f"HTTP {e.code} {detail}"
        except Exception as e:
            return None, None, f"{type(e).__name__}: {str(e)[:70]}"
    else:
        return None, None, "failed"
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        return None, None, "unreadable answer"
    calls = []
    for c in msg.get("tool_calls") or []:
        f = c.get("function") or {}
        try:
            args = json.loads(f.get("arguments") or "{}")
        except ValueError:
            args = {}
        calls.append((f.get("name"), args if isinstance(args, dict) else {}))
    text = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S)
    return calls, text, None


def run_model(prov, model, out):
    score, secs, notes = 0, 0.0, []
    for name, q, check in TESTS:
        t0 = time.time()
        calls, text, err = call(prov, model, q)
        secs += time.time() - t0
        if err:
            notes.append(f"{name}: {err}")
            if err.startswith(("HTTP 401", "HTTP 403", "HTTP 404", "HTTP 410")):
                break  # no key / not allowed / model gone: the other tests will fail the same way
            continue
        ok = False
        try:
            ok = check(calls, text)
        except Exception:
            pass
        score += ok
        if not ok:
            notes.append(f"{name}: " + (f"called {[c[0] for c in calls]}" if calls else f"no tool, said {text.strip()[:50]!r}"))
    out.append((prov, model, score, secs, notes))


def main():
    load_env()
    args = [a for a in sys.argv[1:] if ":" in a]
    cands = [tuple(a.split(":", 1)) for a in args] if args else CANDIDATES
    cands = [(p, m) for p, m in cands if p in BASES and os.getenv(BASES[p][1])]
    if not cands:
        print("No candidate has a key in .env (NVIDIA_API_KEY, GEMINI_API_KEY, MISTRAL_API_KEY, GROQ_API_KEY, OPENROUTER_API_KEY).")
        return 1
    print(f"Testing {len(cands)} models x {len(TESTS)} requests. This takes a few minutes...\n")
    out, threads = [], []
    by_prov = {}
    for p, m in cands:
        by_prov.setdefault(p, []).append(m)
    for p, models in by_prov.items():  # providers in parallel, the models of one provider one after the other (their rate limits)
        t = threading.Thread(target=lambda p=p, models=models: [run_model(p, m, out) for m in models])
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    out.sort(key=lambda r: (-r[2], r[3]))
    print(f"{'score':<7}{'time':<8}{'provider/model'}")
    for prov, model, score, secs, notes in out:
        print(f"{score}/{len(TESTS):<5}{secs:>5.0f}s  {prov}/{model}")
        for n in notes[:3]:
            print(f"          - {n}")
    best = [f"{p}/{m}" for p, m, s, _, _ in out if s == len(TESTS)]
    print("\nBest (5/5, fastest first): " + (", ".join(best[:4]) if best else "none got 5/5; take the highest score above"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
