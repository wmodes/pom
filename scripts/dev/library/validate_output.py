#!/usr/bin/env python3
"""Validation hook for the oracle's output. STUB: it checks nothing yet, on purpose.

The control script will call `check_output(text, hinge)` on every reply before printing
it, so there is one named place to add checks. Today it returns no findings.

INTENTION
    The Printer's replies should be "not obvious source, not obvious form, not obvious
    input": lyrically connected to the room, composed in the shelves' flavor, never a
    plain quote and never a plain answer. We worried about two ways the oracle could miss that.
      - Track A (stock instruct model, 3 to 5 library passages in the prompt) may copy its
        examples.
      - Track B (base model, LoRA fine-tuned on about 70,000 utterances) may memorize and
        recite them, most likely from the small, heavily drawn shelves.
    The design notes list "checking both oracle tracks for verbatim copying" as an open item. This
    file is where such a check would live.

WHY IT IS A STUB (decided with Wes, October 2026)
    The obvious check is verbatim overlap: the longest run of words a reply shares with any
    passage. We decided it measures the wrong thing.
      1. False positives. A quote that lands (a Shakespeare line placed exactly right) is
         something we may want. Overlap counts it as a failure.
      2. False negatives. A reply that is one word-swap away from a Scotch proverb still
         reads as that proverb, yet shares no long run of words with it.
      3. It is blind to "not obvious input." A reply that merely echoes the hinge ("you said
         dog, so: dog") fails the goal and never touches the library.
      4. It stands in for the real question, "does this read as an obvious source?", which
         is a matter of taste. The bake-off compares both tracks' outputs side by side, and
         that reading is the real test. We add a check here only if the reading shows a
         problem worth catching.

COST (not the reason for deferring, but recorded because it was asked)
    Scanning the whole library per reply would be m output words by c passages and far too slow.
    It does not have to be done that way. Build a lookup of the library once, at startup, like
    the draw weights: hash every 4-word window of the roughly 2.6 million library words (about
    20 MB as a sorted array of 64-bit hashes; an estimate, not measured). Checking a reply of
    m words is then about m lookups, independent of library size. A run of consecutive hits
    gives the length of a copied passage, which is confirmed against the passage text so a hash
    collision cannot raise a false alarm, and which names the passage it came from.

CANDIDATES IF NEEDED LATER
    Each should label a reply with a finding, not silently reject it, so a person decides.
      - quotation:  a long run of words shared with a passage, naming the holding (the index
                    above).
      - hinge echo: how much of the reply repeats the hinge's own words (cheap).
      - near-copy:  high similarity to one passage without being identical. The hardest of
                    these, and left alone unless it actually shows up.
"""


def check_output(text, hinge=None):
    """Returns a list of findings (strings) about a reply; empty means nothing to report.

    Args:
        text: The reply the oracle produced.
        hinge: The room clip (or typed input) the reply was written from, if known.

    Returns:
        A list of short human-readable findings. This stub always returns [].
    """
    return []


if __name__ == "__main__":
    print("validate_output.py is a stub: check_output() reports nothing yet.")
