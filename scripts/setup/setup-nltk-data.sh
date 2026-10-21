#!/bin/sh
# Fetch the NLTK data the Scorer needs into data/nltk/ (gitignored). Safe to run again.
#
# NLTK ships its code on PyPI but its data separately. We keep the data inside the project, in
# data/nltk/, instead of the home directory, so a machine can be rebuilt from the repo and the
# Mac and the Pi end up with the same files. Point NLTK at it with NLTK_DATA=<repo>/data/nltk
# (the code does this itself).
#
# What is fetched, and why:
#   wordnet                             senses, synonyms and broader/narrower words (license permits
#                                       use and redistribution for any purpose)
#   stopwords                           the small words that carry no interest ("the", "of")
#   averaged_perceptron_tagger_eng      part-of-speech tags, to tell names from other words
#
# Usage:  scripts/setup/setup-nltk-data.sh            (uses python3, or set PYTHON=/path/to/python)

set -eu
PYTHON="${PYTHON:-python3}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DATA="$ROOT/data/nltk"
mkdir -p "$DATA"
for resource in wordnet stopwords averaged_perceptron_tagger_eng; do
    echo "fetching $resource ..."
    "$PYTHON" -m nltk.downloader -q -d "$DATA" "$resource"
done
echo "done: $(du -sh "$DATA" | cut -f1) in $DATA"
