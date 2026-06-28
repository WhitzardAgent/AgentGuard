# jailbreak_check

`jailbreak_check` is a built-in LLM-input protection plugin that detects prompt-injection and system-prompt leak attempts before the prompt reaches the model.

Unlike `rule_based_plugin`, which is server-only, `jailbreak_check` is available on both the client and the server. The detection logic is the same on both sides; the main difference is where it runs and whether you place it under the `client` or `server` list in plugin config.

## Client-side configuration

When configured as a client plugin, `jailbreak_check` runs locally inside the agent process during the `llm_before` phase:

```json
{
  "phases": {
    "llm_before": {
      "client": [{"name": "jailbreak_check", "env": {}}],
      "server": []
    }
  }
}
```

Use the client-side form when you want low-latency local blocking before the request is sent to the model or forwarded to the server. On a match, the local plugin returns a final `DENY` decision immediately.

## Server-side configuration

When configured as a server plugin, the same detector runs on the AgentGuard server during the `llm_before` phase:

```json
{
  "phases": {
    "llm_before": {
      "client": [],
      "server": [{"name": "jailbreak_check", "env": {}}]
    }
  }
}
```

Use the server-side form when you want centralized management, centralized audit visibility, or a remote-only deployment model. On a match, the server plugin returns a `DENY` decision in the server-side decision pipeline.

## What it checks

`jailbreak_check` inspects `llm_input` events and looks for built-in prompt templates such as:

- instruction-override attempts like “ignore previous instructions”
- role-hijack patterns like “you are now admin” or “act as developer”
- common jailbreak names such as `DAN` or “developer mode”
- system-prompt exfiltration requests
- delimiter injection markers such as `[SYSTEM]`, `<<SYS>>`, or `assistant:`
- suspicious tool-abuse or data-exfiltration instructions
- simple social-engineering patterns

When a template matches, the plugin adds risk signals and stores the matched regex templates in metadata under `matched_prompt_templates`.

## Decision behavior

By default, `jailbreak_check` blocks suspicious prompts.

- On the client side, it returns a final local `DENY`.
- On the server side, it returns a server-side `DENY` in the remote plugin chain.

In both cases, the goal is to stop obviously malicious or policy-evasive prompts before they can influence downstream behavior.

## When to choose client vs. server

- Choose **client-side** deployment when you want the earliest possible interception in the agent process.
- Choose **server-side** deployment when you want centralized policy operation and remote audit visibility.
- Choose one side by default unless you intentionally want both local prefiltering and remote re-checking.
