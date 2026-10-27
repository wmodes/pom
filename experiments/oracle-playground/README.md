# Oracle playground

Type a hinge, get a portent. It runs the real `PromptBuilder` and `OracleModelClient`, with the prompt from
`oracle.prompt` in `config/pom.toml`, so what you see here is what the Printer will do when it is wired up.
The only thing it supplies is the hinge, typed by hand, which the trigger will supply later.

```
python experiments/oracle-playground/probe_oracle.py --model PATH_TO.gguf
python experiments/oracle-playground/probe_oracle.py --model unsloth/Qwen3-8B-GGUF:Q4_K_M    # downloaded on first use
python experiments/oracle-playground/probe_oracle.py --server                                # a llama-server is already running
```

It starts a llama-server for the model on the port in `oracle.url` and stops it when you quit.

- **The prompt** is read from the config again before every hinge, so edit it and type the hinge again.
- **Same hinge, same exemplars.** The random generator is reseeded from a hash of the hinge, so two models
  given the same hinge see the same exemplars (they are printed above the portent, two to a row, each headed by its id). `--seed N` gives other
  exemplars for the same hinge.
- It prints the portent, its length in words, and the time. If the model stopped at `max_tokens`, it says so.
- Problems the modules recorded (a bad prompt, a server that did not answer) are printed after the portent.

Models for a bake-off: run it once per model with the same hinges and compare by ear. Candidates found by research
(2026-10-24), none of them tested yet: Qwen3-8B, Llama 3.1 8B, Mistral Nemo 12B, Qwen3-14B.
