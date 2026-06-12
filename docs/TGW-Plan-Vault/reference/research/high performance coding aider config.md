<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# high performance coding aider config

For **high-performance coding** with Aider, the biggest wins are: use a strong main model, a cheaper weak model for summaries/commit messages, enable repo mapping, turn on architect mode, and wire in lint/test commands so Aider can validate changes automatically.[^1_1][^1_2]

## Recommended config

A solid starting `.aider.conf.yml` for fast, effective coding looks like this:

```yaml
model: your-best-code-model
weak-model: your-cheap-fast-model
editor-model: your-fast-editor-model
architect: true
auto-accept-architect: true
cache-prompts: true
map-tokens: 8000
map-refresh: auto
show-diffs: true
git: true
auto-commits: true
dirty-commits: true
auto-lint: true
auto-test: true
test-cmd:
  - pytest -q
lint-cmd:
  - python: ruff check .
  - python: ruff format --check .
```

Aider supports these key knobs directly in config, including `model`, `weak-model`, `editor-model`, `architect`, `cache-prompts`, `map-tokens`, `map-refresh`, `show-diffs`, `auto-commits`, `auto-lint`, `auto-test`, `test-cmd`, and `lint-cmd`.[^1_2][^1_1]

## Performance choices

For speed, keep `cache-prompts` enabled and use a dedicated weak model for history summarization and commit messages, because Aider explicitly uses the weak model for those lower-cost tasks. If your model supports it, setting `thinking-tokens` or `reasoning-effort` can help, but only when the provider/model actually accepts those settings. `map-tokens` is especially important on larger repos because repo-map quality affects how much context Aider can reason over efficiently.[^1_1][^1_2]

## Fast workflow

For a high-throughput coding loop, I’d use `--architect` for planning, then let Aider apply edits with `--show-diffs` so you can review changes quickly before they land. Pair that with `auto-lint` and `auto-test` so every edit gets checked immediately, which reduces back-and-forth and keeps the agent from drifting. If you work in a Git repo, keep `auto-commits` on so every successful edit becomes a clean commit trail.[^1_2][^1_1]

## Practical tuning

If the repo is small, you can lower `map-tokens`; if it’s large or monorepo-like, raise it so Aider has enough structural context. If you want fewer interruptions, `auto-accept-architect` can stay on, but if you prefer stricter review, disable it and inspect the plan first. For a custom OpenAI-compatible endpoint, Aider supports `openai-api-base` plus key configuration, and the config file can be loaded from home, repo root, or current directory.[^1_1][^1_2]

## Example setup

A fast Python-focused setup would be:

```yaml
model: anthropic/claude-3-7-sonnet-20250219
weak-model: gpt-4o-mini
editor-model: gpt-4o-mini
architect: true
cache-prompts: true
map-tokens: 12000
show-diffs: true
auto-commits: true
auto-lint: true
auto-test: true
test-cmd:
  - pytest -q
lint-cmd:
  - python: ruff check .
```

That combination favors strong code generation plus cheap auxiliary tasks, while keeping validation in the loop.[^1_2][^1_1]

Would you like a config tailored for your Python/PostgreSQL/eBay workflow, including a `ruff` + `pytest` + repo-map setup?
<span style="display:none">[^1_3][^1_4][^1_5][^1_6][^1_7][^1_8]</span>

<div align="center">⁂</div>

[^1_1]: https://aider.chat/docs/config/aider_conf.html

[^1_2]: https://www.reddit.com/r/ChatGPTCoding/comments/1h2fwdp/aider_config/

[^1_3]: https://aider.chat/docs/config/options.html

[^1_4]: http://mitchgordon.me/tools/2025/02/26/aider.html

[^1_5]: https://stackoverflow.com/questions/79517603/use-multiple-api-model-providers-with-aider

[^1_6]: https://slinkp.com/programming-with-aider-20250725.html

[^1_7]: https://registry.coder.com/modules/coder/aider

[^1_8]: https://www.youtube.com/watch?v=1IK69XZZegU


---

# yes using anthropic and openrouter models for now, also recommend best weak model

Yes — if you’re using Anthropic and OpenRouter, I’d make OpenRouter your default path for Claude models because Aider’s own Anthropic docs note Anthropic has low rate limits, while OpenRouter can provide more generous access to those same models.[^2_1][^2_2]

## Recommended stack

For a strong coding setup today, use Claude Sonnet as the main model and a smaller OpenRouter model as the weak model, since Aider uses the weak model for commit messages and conversation summarization rather than core coding edits. Aider also supports separate `model`, `weak-model`, and `editor-model` settings, so you can keep your expensive reasoning model focused on planning and code changes while offloading lightweight tasks to a cheaper model.[^2_3][^2_4][^2_1]

My practical recommendation:

- Main model: `openrouter/anthropic/claude-3.7-sonnet`
- Editor model: `openrouter/anthropic/claude-3.7-sonnet`
- Weak model: `openrouter/openai/gpt-4o-mini`[^2_5][^2_2]

I’d pick `openrouter/openai/gpt-4o-mini` as the **best weak model** right now because it is explicitly used as a workable OpenRouter weak-model example with Aider, and it fits the weak-model role well: fast, cheap, and good enough for summarization/commit text without wasting Sonnet tokens.[^2_3][^2_5]

## Config example

A good `.aider.conf.yml` for your setup is:

```yaml
model: openrouter/anthropic/claude-3.7-sonnet
editor-model: openrouter/anthropic/claude-3.7-sonnet
weak-model: openrouter/openai/gpt-4o-mini

architect: true
auto-accept-architect: false

cache-prompts: true
map-tokens: 12000
map-refresh: auto
show-diffs: true

git: true
auto-commits: true
dirty-commits: true

auto-lint: true
auto-test: true

lint-cmd:
  - python: ruff check .
  - python: ruff format --check .

test-cmd:
  - pytest -q
```

Those settings align with Aider’s documented config options for model selection, architect mode, prompt caching, repo-map sizing, diff display, auto-commits, linting, and test commands.[^2_6][^2_1]

## OpenRouter tuning

If you want predictable provider behavior, Aider’s OpenRouter docs recommend putting provider routing in `.aider.model.settings.yml`, where you can set provider order, disable fallbacks, deny data collection, and require full parameter support. That matters when one OpenRouter backend is flaky or when you want to force Anthropic-first routing for Claude requests.[^2_2]

Example:

```yaml
- name: openrouter/anthropic/claude-3.7-sonnet
  extra_params:
    extra_body:
      provider:
        order: ["Anthropic", "Together"]
        allow_fallbacks: false
        data_collection: "deny"
        require_parameters: true
```


## Alternative weak models

If you want to stay all-Anthropic, Aider’s Anthropic example model settings pair Sonnet 3.7 with `anthropic/claude-3-5-haiku-20241022` as the weak model, so Haiku is the most canonical Anthropic-native weak-model choice in Aider’s own docs. I’d still favor `openrouter/openai/gpt-4o-mini` for cost and availability, but `openrouter/anthropic/claude-3.5-haiku` is the cleanest “same family” option when you want stylistic consistency.[^2_1][^2_2]

## Linux env vars

For this setup, export your OpenRouter key and optionally your Anthropic key, since Aider supports both environment-variable flows directly.[^2_2][^2_1]

```bash
export OPENROUTER_API_KEY=your_openrouter_key
export ANTHROPIC_API_KEY=your_anthropic_key
```

If you want one clean recommendation: use Sonnet on OpenRouter for main/editor, `gpt-4o-mini` on OpenRouter for weak, and only switch the weak model to Haiku if you want all-Anthropic behavior.[^2_5][^2_1][^2_2]
<span style="display:none">[^2_10][^2_11][^2_12][^2_13][^2_14][^2_15][^2_16][^2_17][^2_7][^2_8][^2_9]</span>

<div align="center">⁂</div>

[^2_1]: https://aider.chat/docs/config/options.html

[^2_2]: http://mitchgordon.me/tools/2025/02/26/aider.html

[^2_3]: https://github.com/Aider-AI/aider/issues/541

[^2_4]: https://github.com/Aider-AI/aider/issues/3383

[^2_5]: https://stackoverflow.com/questions/79517603/use-multiple-api-model-providers-with-aider

[^2_6]: https://aider.chat/docs/config/aider_conf.html

[^2_7]: https://aider.chat/docs/config/adv-model-settings.html

[^2_8]: https://aider.chat/docs/llms/anthropic.html

[^2_9]: https://www.reddit.com/r/ChatGPTCoding/comments/1jns43q/aider_v0800_is_out_with_easy_openrouter_onboarding/

[^2_10]: https://aider.chat/docs/llms/openrouter.html

[^2_11]: https://github.com/Aider-AI/aider/issues/3095

[^2_12]: https://aider.chat/docs/troubleshooting/models-and-keys.html

[^2_13]: https://www.fingon.iki.fi/blog/aider-0.8.1-and-me/

[^2_14]: https://www.youtube.com/watch?v=nyDsdlHgEmQ

[^2_15]: https://openrouter.ai/works-with-openrouter/aider

[^2_16]: https://stackoverflow.com/questions/79517581/configure-aider-with-custom-models

[^2_17]: https://meta.discourse.org/t/configuring-openrouter-language-models/341526?tl=en

