#!/usr/bin/env python3
"""Probe: can a small local LLM rate how striking a line of overheard speech is?

A throwaway experiment, not part of the Printer. By default it sends each line to a running
llama-server on its own and asks for a single digit, 1-9. It reads the probabilities of the
digits instead of sampling, so the score is continuous, repeatable, cannot be moved by the other
lines, and costs one generated token. With --context the earlier lines are also shown as "what was
said just before". With --batch it instead sends all the lines in one numbered list and reads back
a decimal score per row; that is quicker to write but the scores shift with the order and the
neighbors. Each mode prints the scores and how long the calls took.

Start a server first, for example:
    llama-server -hf unsloth/Qwen3-1.7B-GGUF:Q4_K_M --port 8091 --ctx-size 8192 --jinja

Usage:
    probe_rater.py                       # built-in test lines
    probe_rater.py --file lines.txt      # one line per row
    probe_rater.py --context             # also show each line the lines before it
    probe_rater.py --batch               # one request for the whole list (order-sensitive)
    probe_rater.py --port 8091 --prompt-file my_prompt.txt

The prompt is a plain file argument so it can be varied without editing this script. It uses only
the standard library.
"""
import argparse
import json
import math
import re
import sys
import time
import urllib.request

# DEFAULT_PROMPT = (
    # "You listen to people talking in a room. Rate how 'interesting' one line of what you overheard "
    # "is, as a starting point for a strange, lyrical remark in reply. Striking means a vivid "
    # "image, an odd or specific detail, a name, a number, a question, a feeling, or an unexpected "
    # "turn. Ordinary talk (greetings, logistics, filler, agreement) is not striking. Answer with "
    # "a single digit from 1 (dull) to 9 (very striking) and nothing else."
# )
BATCH_PROMPT = """
You rate how interesting sentences are.

Each message is a numbered list of sentences that people said out loud in a
room. Rate every sentence by how *interesting* it is, defined as having one or
more of the following content:

- a vivid or physical image
- an odd or specific detail
- a clear emotion, a confession, or a question
- a surprise, a contradiction, weird, strange, or an absurdity, even a comic one
- death, spirits, bodies, animals, food, money, weather, or machines

A sentence is not interesting when it is ordinary talk: greetings, filler,
agreement, plans and logistics, or vague statements.

Scale, from 1.0 to 9.0:
1.0-1.9  not interesting: no interesting content
2.0-3.9  slightly interesting: little interesting content
4.0-5.9  interesting: some interesting content
6.0-7.9  very interesting: more interesting content
8.0-9.0  extremely interesting: lots of interesting content

Most sentences are ordinary and score below 5.

Reply with one line for every sentence, in order, through the last number: the
sentence number, a space, and a score from 1.0 to 9.0 with one decimal place.
Do not skip any sentence. Write nothing else.
"""

# One sentence per request, one token back. The score is read from the probabilities of the digits
# 1-9 (see rate()), so it is continuous, repeatable, and cannot be moved by neighboring sentences.
SINGLE_PROMPT = """
You rate how interesting a sentence is.

Each message is one sentence that someone said out loud in a room. Rate how
*interesting* it is, defined as having one or more of the following content:

- a vivid or physical image
- an odd or specific detail
- a clear emotion, a confession, or a question
- a surprise, a contradiction, weird, strange, or an absurdity, even a comic one
- death, spirits, bodies, animals, food, money, weather, or machines

A sentence is not interesting when it is ordinary talk: greetings, filler, 
agreement, plans and logistics, or vague statements.

Scale, from 1.0 to 9.0:
1.0-1.9  not interesting: no interesting content
2.0-3.9  slightly interesting: little interesting content
4.0-5.9  interesting: some interesting content
6.0-7.9  very interesting: more interesting content
8.0-9.0  extremely interesting: lots of interesting content

Most sentences are ordinary and score below 5. 

Reply with a single number with two significant digits from 1.0 to 9.0 and nothing else.
"""

DEFAULT_LINES = [
    "Even in New York's most introductionless circles, people always introduce.",
    "A small group of people who are to sit together anywhere.",
    "Partners at dinner.",
    "The guests at a house party.",
    "Everyone at a small dinner or luncheon.",
    "The four who are at the same bridge table.",
    "Partners or fellow-players in any game.",
    "All right what's going on here?",
    "How are we supposed to experience this year?",
    "Oh this is cool.",
    "What if I beat your face in?",
    "Oh my God, last night the dog ate my couch.",
    "Well, that's better than the couch eating your dog.",
    "Yesterday I was just talking to Neil about this.",
    "Honestly, if this bullshit keeps up I'm going to be exercising my second amendment rights.",
    "Have you read her recent book?",
    "What the fuck?",
    "I think my car is posessed.",
    "I'm fucking pissed about this.",
    "Did you get that website I texted you about?",
    "I'm kind of social out and I need to get the fuck out of here.",
    "I love you",
    "These self-introductions, however, must never presumingly be made.",
    "It would be in very bad taste for Alice to introduce herself to Mrs. Worldly if her sister knew her only slightly.",
]


def rate(url: str, prompt: str, line: str, context: list[str]) -> tuple[float, float]:
    """Returns (expected rating 1-9, seconds taken) for one line, with optional prior lines."""
    heard = ""
    if context:
        heard = "Overheard just before:\n" + "\n".join(f"- {c}" for c in context) + "\n\n"
    body = {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"{heard}Rate this line: {line}"},
        ],
        "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": 20,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(url, json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.load(response)
    seconds = time.perf_counter() - start
    top = data["choices"][0]["logprobs"]["content"][0]["top_logprobs"]
    weights = {}
    for entry in top:
        token = entry["token"].strip()
        if len(token) == 1 and token in "123456789":
            weights[int(token)] = weights.get(int(token), 0.0) + math.exp(entry["logprob"])
    total = sum(weights.values())
    if total == 0:
        return float("nan"), seconds
    return sum(d * w for d, w in weights.items()) / total, seconds


def rate_batch(url: str, prompt: str, lines: list[str]) -> tuple[list[float], float, str]:
    """Rates every line in one request, as the numbered-segment prompt asks.

    The model replies with one "number score" row per segment (for example "3 6.4"). Segments the
    reply skips or garbles score nan, so a bad reply never stops the run.

    Returns:
        (scores in line order, seconds taken, the raw reply text for inspection)
    """
    transcript = "\n".join(f"{number}. {line}" for number, line in enumerate(lines, start=1))
    body = {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": transcript},
        ],
        "max_tokens": 12 * len(lines) + 20, "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    request = urllib.request.Request(url, json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=300) as response:
        data = json.load(response)
    seconds = time.perf_counter() - start
    reply = data["choices"][0]["message"]["content"]
    scores = [float("nan")] * len(lines)
    for row in reply.splitlines():
        found = re.match(r"\s*(\d+)\D+?(\d+(?:\.\d+)?)\s*$", row)
        if found and 1 <= int(found.group(1)) <= len(lines):
            scores[int(found.group(1)) - 1] = float(found.group(2))
    return scores, seconds, reply


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--file", help="lines to rate, one per row (default: built-in set)")
    parser.add_argument("--prompt-file", help="system prompt text (default: built-in)")
    parser.add_argument("--batch", action="store_true",
                        help="rate all the lines in one request and read decimal scores back "
                             "(scores then depend on order and neighbors); default is one line "
                             "per request, one digit back")
    parser.add_argument("--context", action="store_true",
                        help="show the previous lines as context for each one")
    parser.add_argument("--window", type=int, default=6, help="context lines to include")
    args = parser.parse_args()

    lines = DEFAULT_LINES
    if args.file:
        with open(args.file, encoding="utf-8") as handle:
            lines = [row.strip() for row in handle if row.strip()]
    prompt = BATCH_PROMPT if args.batch else SINGLE_PROMPT
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as handle:
            prompt = handle.read().strip()
    url = f"http://127.0.0.1:{args.port}/v1/chat/completions"

    if args.batch:
        try:
            scores, seconds, reply = rate_batch(url, prompt, lines)
        except OSError as error:
            sys.exit(f"cannot reach the server on port {args.port}: {error}")
        for number, (score, line) in enumerate(zip(scores, lines), start=1):
            print(f"{score:5.1f}  {number:3d}  {line}")
        print(f"\n{seconds * 1000:.0f} ms for {len(lines)} segments")
        print("\nbest first:")
        ranked = sorted(zip(scores, lines), key=lambda r: -r[0] if r[0] == r[0] else 0)
        for score, line in ranked:
            print(f"{score:5.1f}  {line}")
        if any(score != score for score in scores):
            print(f"\nSome segments got no score. The raw reply was:\n{reply}")
        return

    results, times = [], []
    for index, line in enumerate(lines):
        context = lines[max(0, index - args.window):index] if args.context else []
        try:
            score, seconds = rate(url, prompt, line, context)
        except OSError as error:
            sys.exit(f"cannot reach the server on port {args.port}: {error}")
        results.append((score, line))
        times.append(seconds)
        print(f"{score:5.2f}  {seconds * 1000:6.0f} ms  {line}")
    print(f"\nmean {sum(times) / len(times) * 1000:.0f} ms, slowest {max(times) * 1000:.0f} ms")
    print("\nbest first:")
    for score, line in sorted(results, key=lambda r: -r[0] if r[0] == r[0] else 0):
        print(f"{score:5.2f}  {line}")


if __name__ == "__main__":
    main()
