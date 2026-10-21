#!/usr/bin/env python3
"""Probe: can a small local LLM rate how striking a line of overheard speech is?

A throwaway experiment, not part of the Printer. It sends lines to a running llama-server, asks
for a 1-9 rating as a single token, and reads the probabilities of the digits 1-9 instead of
sampling, so the score is continuous and repeatable (expected value of the digit). It prints each
line's score and how long the call took. With --context, the lines are also shown to the model as
"what was said in the room just before", to see whether context changes the ratings.

Start a server first, for example:
    llama-server -hf unsloth/Qwen3-1.7B-GGUF:Q4_K_M --port 8091 --ctx-size 8192 --jinja

Usage:
    probe_rater.py                       # built-in test lines
    probe_rater.py --file lines.txt      # one line per row
    probe_rater.py --context             # also rate each line after the earlier lines as context
    probe_rater.py --port 8091 --prompt-file my_prompt.txt

The prompt is a plain file argument so it can be varied without editing this script. It uses only
the standard library.
"""
import argparse
import json
import math
import sys
import time
import urllib.request

DEFAULT_PROMPT = (
    "You listen to people talking in a room. Rate how striking one line of what you overheard "
    "is, as a starting point for a strange, lyrical remark in reply. Striking means a vivid "
    "image, an odd or specific detail, a name, a number, a question, a feeling, or an unexpected "
    "turn. Ordinary talk (greetings, logistics, filler, agreement) is not striking. Answer with "
    "a single digit from 1 (dull) to 9 (very striking) and nothing else."
)

DEFAULT_LINES = [
    "Yeah, okay, sounds good.",
    "Can you pass the salt?",
    "The dog ate my couch.",
    "The couch ate my dog.",
    "Karen keeps complaining about the parking again.",
    "I think we should leave at about three.",
    "Last night I dreamed the stairs went down further than the house.",
    "My grandmother kept forty-one clocks and none of them agreed.",
    "Uh, you know, I mean, like, whatever.",
    "She stopped talking when the lights flickered and then she just laughed.",
    "What time is the meeting tomorrow?",
    "There is a door in the basement that nobody remembers building.",
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--file", help="lines to rate, one per row (default: built-in set)")
    parser.add_argument("--prompt-file", help="system prompt text (default: built-in)")
    parser.add_argument("--context", action="store_true",
                        help="show the previous lines as context for each one")
    parser.add_argument("--window", type=int, default=6, help="context lines to include")
    args = parser.parse_args()

    lines = DEFAULT_LINES
    if args.file:
        with open(args.file, encoding="utf-8") as handle:
            lines = [row.strip() for row in handle if row.strip()]
    prompt = DEFAULT_PROMPT
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as handle:
            prompt = handle.read().strip()
    url = f"http://127.0.0.1:{args.port}/v1/chat/completions"

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
