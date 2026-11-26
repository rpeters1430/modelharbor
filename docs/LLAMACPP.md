# llama.cpp

If the PC runs [llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server` in router mode (`--models-dir <folder>`), the PC's agent can list the models in that folder and download Hugging Face GGUF files into it from Browse.

llama-server offers every `.gguf` file in its models folder under the file name without `.gguf`. A download of `gpt-oss-20b-MXFP4.gguf` is therefore the model `gpt-oss-20b-MXFP4` to any app connected to llama-server.

## Configure the agent

In the agent's `agent.json`:

```json
"llamacpp_models_dir": "/srv/ai/llama-cpp/models",
"llamacpp_restart_command": ["docker", "restart", "llama-cpp"]
```

- `llamacpp_models_dir` is the folder llama-server reads with `--models-dir`, as seen from the PC. If llama-server runs in Docker, use the host folder that is mounted into the container. Leave it out to turn llama.cpp off; on the next sync its models are cleared from the dashboard. If the folder can't be read (an unmounted drive, say), its models are hidden until it is back, rather than cleared.
- `llamacpp_restart_command` runs after a model is downloaded or deleted. llama-server reads its models folder only when it starts, so without a restart a new model doesn't appear. The default restarts a Docker container named `llama-cpp`. The agent's user must be able to run the command (for Docker, be in the `docker` group). Set it to `[]` if you restart llama-server yourself.

Restart the agent's background task after changing the config. On Linux, run `systemctl --user restart modelharbor-worker`.

## Download a model

In Browse, open a Hugging Face GGUF repository, choose **llama.cpp (download GGUF)** under "Save into", and click Download. The option appears only for PCs whose agent has `llamacpp_models_dir` set, and it is chosen by default there.

The agent:

1. downloads the file into the models folder, next to nothing it would overwrite (a file with the same name is refused),
2. checks it against the SHA-256 that Hugging Face publishes, and deletes it if the hash doesn't match,
3. runs `llamacpp_restart_command`, so llama-server lists the new model.

Only single-file GGUF models can be downloaded. Split models (`-00001-of-00003.gguf`) and vision projector files (`mmproj-*.gguf`) are refused, because llama-server needs extra setup for them. If a repository has two files with the same name in different folders, pick a repository without duplicates, since the file name is the model's name.

A pasted link to a language model (a Hugging Face repository whose pipeline is text generation, or one with only GGUF files and nothing marking it as an image or video model) is shown as **Language model**. It is offered only to the LLM servers on the PC, never a ComfyUI folder such as `checkpoints`, and it has no base-model warning. If the PC has no LLM server, the card says so instead of offering a download. The NAS also refuses any Hugging Face or ModelScope GGUF saved into `checkpoints`, because ComfyUI's checkpoint loader can't read GGUF; ComfyUI-GGUF diffusion models go in `diffusion_models`.

## llama.cpp or Ollama?

When **Save into** is an LLM server, each GGUF file shows which server it suits best on the PC:

- **⚡ Optimized for llama.cpp**: I-quants (`IQ4_XS`, `IQ3_M`, `TQ1_0`…), Unsloth Dynamic quants (`UD-Q4_K_XL`), split models and vision projectors (`mmproj`), which Ollama can't import, and mixture-of-experts models (`A3B`, `gpt-oss`, `Mixtral`…) that are bigger than the PC's VRAM, because llama.cpp can keep the experts in system RAM (`--cpu-moe`) and run them much faster than Ollama's layer split.
- **⚡ Optimized for Ollama**: standard quants (`Q4_K_M`, `Q5_K_M`, `Q6_K`, `Q8_0`, `Q4_0`), the kind Ollama's own library ships. They run just as well in llama.cpp.
- **⇄ Runs well in both**: everything else, such as `BF16`, `F16` or `MXFP4`.

When Ollama is the destination and a file is optimized for llama.cpp, the hint suggests saving it into llama.cpp instead, and Ollama's recommended file skips llama.cpp-tuned quants when another one fits. This is advice from the file name, size and the PC's GPU, not a benchmark.

## Delete a model

Open the model on the dashboard and use **Delete from <PC>**. The agent re-checks the file's SHA-256, deletes it, and restarts llama-server so the model disappears from it too.

## Connect apps

Apps talk to llama-server, not to ModelHarbor. Use its OpenAI-compatible address, for example `http://<PC address>:8080/v1`, with the API key llama-server was started with (`--api-key` or `--api-key-file`), and the model name described above.
