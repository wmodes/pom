# corpus/ready/

**Everything in this directory is safe to use as training, few-shot or retrieval data.**

Each file is a list of utterances: one utterance per paragraph, paragraphs separated by one blank line, no trace of the source. Verse keeps its line breaks inside a paragraph (a stanza is a paragraph). Text is plain, typewriter-printable ASCII. Files are laid out as `<form>/<id>.txt`.

Each file was cleared (public domain, CC0, or recorded as owner-cleared in the manifest), trimmed, standardized, and reviewed by a human before `scripts/dev/corpus/corpus.py promote` copied it in.

Do not edit these files by hand, and do not copy anything in from `raw/` or `draft/`. To change a text, change its row in `../sources.csv` or a rule in `scripts/dev/corpus/normalize.py` and re-run the pipeline. See `../README.md`.

Some sources here are `owner-cleared`, not public domain. Run `scripts/dev/corpus/corpus.py publish-check` before sharing or publishing the repository.
