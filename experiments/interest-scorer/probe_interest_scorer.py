#!/usr/bin/env python3
"""Probe: can a small local LLM score how interesting a sentence of overheard speech is?

Part of experiments/interest-scorer, a formative experiment that settled how the Scorer works. It
is not part of the Printer (see the README in this folder). By default it sends each line to a
running llama-server on its own and asks for a single digit, 0-9, with the prompt in config/pom.toml
(the same prompt the Printer uses). It reads the probabilities of the digits with the Printer's own
reader (pom.scoring), so the score is continuous, repeatable, cannot be moved by the other lines,
and costs one generated token. With --context the earlier lines are also shown as "what was
said just before". With --batch it instead sends all the lines in one numbered list and reads back
a decimal score per row; that is quicker to write but the scores shift with the order and the
neighbors. Each mode prints the scores and how long the calls took.

Start a server first, for example:
    llama-server -hf unsloth/Qwen3-4B-GGUF:Q4_K_M --port 8091 --ctx-size 8192 --jinja

Usage:
    probe_interest_scorer.py                  # built-in test lines
    probe_interest_scorer.py --file lines.txt # one line per row
    probe_interest_scorer.py --context        # also show each line the lines before it
    probe_interest_scorer.py --batch          # one request for the whole list (order-sensitive)
    probe_interest_scorer.py --config other.toml        # prompts from another config file
    probe_interest_scorer.py --port 8091 --prompt-file my_prompt.txt   # a prompt from a plain file

By default the prompt is read from config/pom.toml each time the script runs, so editing the config
is all it takes to try a change. The script prints which prompt it used. It is the one place in the
experiments that imports the Printer's own scorer code, so what it measures is what the Printer does.
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
from pom.config import Config                                              # noqa: E402
from pom.scoring.scorer_model_client import USER_TEMPLATE, score_from_probabilities   # noqa: E402

# DEFAULT_PROMPT = (
    # "You listen to people talking in a room. Rate how 'interesting' one line of what you overheard "
    # "is, as a starting point for a strange, lyrical remark in reply. Striking means a vivid "
    # "image, an odd or specific detail, a name, a number, a question, a feeling, or an unexpected "
    # "turn. Ordinary talk (greetings, logistics, filler, agreement) is not striking. Answer with "
    # "a single digit from 1 (dull) to 9 (very striking) and nothing else."
# )
BATCH_PROMPT = """
You score how interesting sentences are.

Each message is a numbered list of sentences that people said out loud in a
room. Score every sentence by how *interesting* it is, defined as having one or
more of the following content:

- a vivid or physical image
- an odd or specific detail
- a clear emotion, a confession, or a question
- a surprise, a contradiction, weird, strange, or an absurdity, even a comic one
- death, spirits, bodies, animals, food, money, weather, or machines

A sentence is not interesting when it is ordinary talk: greetings, filler,
agreement, plans and logistics, or vague statements.

Score on a scale, from 1.00 to 9.99:
1.00-1.99  not interesting: no interesting content
2.00-3.99  slightly interesting: little interesting content
4.00-5.99  interesting: some interesting content
6.00-7.99  very interesting: more interesting content
8.00-9.99  extremely interesting: lots of interesting content

Most sentences are ordinary and score below 5. 

Reply with one line for every sentence, in order, through the last number: the
sentence number, a space, and a score with two decimal places from 1.00 to 9.99.
Do not skip any sentence. Write nothing else.
"""

# One sentence per request, one token back. The score is read from the probabilities of the digits
# 1-9 (see rate()), so it is continuous, repeatable, and cannot be moved by neighboring sentences.
BENCHMARK = [
    # --- original 34 ---
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
    "I heard from friends, that though he's supposedly 'straight,' he gives the best blow job in the world.",
    "I got off a bus in a random small town in Iowa to get a snack and my dad who haven't seen in 10 years happen to be standing there.",
    "I found $100 bill in an old Radio I was working on.",
    "I need to grade all the work my students have turned in.",
    "I once got fired for having sex with my boss.",
    "I would put a bullet in the heart of mine enemies.",
    "Yesterday I was just talking to Neil about this.",
    "Honestly, if this bullshit keeps up I'm going to be exercising my second amendment rights.",
    "Have you read her recent book?",
    "What the fuck?",
    "I think my car is posessed.",
    "I found a hundred dollar bill folded inside the dead man's radio, and it was still warm.",
    "My mother's dog started barking in her voice about a week after the funeral.",
    "Lightning hit the house in '98 and ever since, the toaster only burns my grandfather's initials.",
    "I ate the whole wedding cake alone in the dark, crying about the nine thousand dollars.",
    "I'm fucking pissed about this.",
    "Did you get that website I texted you about?",
    "I'm kind of social out and I need to get the fuck out of here.",
    "I love you",
    "These self-introductions, however, must never presumingly be made.",
    "It would be in very bad taste for Alice to introduce herself to Mrs. Worldly if her sister knew her only slightly.",

    # --- new: not interesting (logistics, filler, admin) ---
    "Can you pass the salt?",
    "I think it's in the other room.",
    "We should head out by nine.",
    "The parking around here is impossible.",
    "Let me grab my charger.",
    "I'll text you the address.",
    "Is there a bathroom on this floor?",
    "Hang on, my phone's buzzing.",
    "Could somebody get the light?",
    "What time is it?",
    "Did you eat yet?",
    "She's running about ten minutes late.",
    "The wifi password's on the fridge.",
    "We're out of napkins.",

    # --- new: middle (one modest signal, mundane with a spark, flat emotion, mild oddity) ---
    "There's a stain on the ceiling that looks like a face.",
    "The cat won't stop staring at the corner.",
    "I spent forty dollars on a single candle.",
    "He never called me back.",
    "I think the neighbors are fighting again.",
    "The elevator smelled like pennies.",
    "I can't remember if I locked the door.",
    "My dad used to whistle the same three notes.",
    "I found a tooth in the backyard.",
    "The baby pointed at the empty chair and laughed.",
    "There's a wasp nest under the back porch.",
    "I keep smelling smoke but nobody else does.",
    "The dog's been digging the same hole all week.",
    "I think I bombed the interview.",
    "The hallway light flickers when no one's near it.",
    "My mom saved every birthday card she ever got.",
    "I haven't spoken to my brother in two years.",
    "There was a bird loose in the house this morning.",
    "I lied to my therapist again today.",
    "The house number across the street changed overnight.",
    "She wore the same black dress to both funerals.",
    "My grandmother kept her old teeth in a jar by the sink.",
    "He left the party without saying goodbye to anyone.",
    "My sister married a man she met at a funeral.",
    "The candle went out on its own twice tonight.",
    "I've started talking to the house when it's empty.",
    "My father's watch stopped the day he died.",
    "The dog growls at the top of the stairs every night.",
    "I ate cereal for dinner three nights running.",
    "I'm so tired of being the one who always calls.",

    # --- new: probes for the known biases ---
    "This is such bullshit.",                                             # heat, no content
    "I swear to God I am going to lose it.",                              # heat/threat, no content
    "I'm so lonely I could scream.",                                     # universal feeling, no noun
    "I just feel empty most days.",                                      # flat feeling, no image
    "One must never present oneself to a lady without a proper introduction.",  # etiquette prose, names-free
    "Do you think she knew she was dying?",                              # open, charged question

    # --- new: interesting (stacked signals: stakes, secret, death, image) ---
    "I watched my father die and I felt nothing at all.",
    "She's pregnant and she still hasn't told him.",
    "I play his old voicemails when I can't sleep.",
    "The surgeon left something inside me and nobody noticed for a year.",
    "My wife doesn't know I lost the house.",
    "He proposed in the hospital an hour before she died.",
    "The river gave the body back in October.",
    "I found my own name in the obituaries this morning.",
    "We scattered her ashes and the wind blew them back in our faces.",
    "The baby was born with a full set of teeth.",
    "I can hear the people in the walls again.",
    "My brother confessed to the fire on his deathbed.",
    "I spent our entire savings on a woman I never met in person.",
    "My mother called me by my dead sister's name until the day she died.",
    "They buried her with the phone still ringing in the casket.",
    "I keep a running list of everyone who has ever wronged me.",
]


def rate(url: str, prompt: str, line: str, context: list[str]) -> tuple[float, float]:
    """Returns (score 0-9, seconds taken) for one line, with optional prior lines. Scored as the Printer scores."""
    heard = ""
    if context:
        heard = "Overheard just before:\n" + "\n".join(f"- {c}" for c in context) + "\n\n"
    body = {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": heard + USER_TEMPLATE.format(fragment_text=line)},
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
    score = score_from_probabilities(data)
    return (float("nan") if score is None else score), seconds


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
    parser.add_argument("--config", default=str(ROOT / "config" / "pom.toml"),
                        help="the config file whose prompt is used (default: config/pom.toml)")
    parser.add_argument("--prompt-file", help="a system prompt from a plain file instead of the config's")
    parser.add_argument("--batch", action="store_true",
                        help="rate all the lines in one request and read decimal scores back "
                             "(scores then depend on order and neighbors); default is one line "
                             "per request, one digit back")
    parser.add_argument("--context", action="store_true",
                        help="show the previous lines as context for each one")
    parser.add_argument("--window", type=int, default=6, help="context lines to include")
    args = parser.parse_args()

    lines = BENCHMARK
    if args.file:
        with open(args.file, encoding="utf-8") as handle:
            lines = [row.strip() for row in handle if row.strip()]
    if args.batch:
        prompt, where = BATCH_PROMPT, "the batch prompt built into this script"
    else:
        settings = Config.load(args.config).scorer
        prompt, where = settings.prompt, f"{args.config} (prompt_scoring + prompt_output)"
        if not prompt:
            sys.exit(f"{args.config} has no prompt_scoring, so there is nothing to ask the model")
    if args.prompt_file:
        with open(args.prompt_file, encoding="utf-8") as handle:
            prompt, where = handle.read().strip(), args.prompt_file
    print(f"prompt: {where}\n")
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
