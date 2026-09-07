import assert from "node:assert/strict";
import test from "node:test";

import {
  providerModelControl,
  providerModelPlaceholder,
} from "../src/provider-model-state.ts";

test("OpenAI, Anthropic, and authenticated Codex providers start with a model dropdown", () => {
  assert.equal(providerModelControl("openai", false), "select");
  assert.equal(providerModelControl("anthropic", false), "select");
  assert.equal(providerModelControl("codex", false), "select");
});

test("manual entry and local providers without discovery use a model input", () => {
  assert.equal(providerModelControl("openai", true), "input");
  assert.equal(providerModelControl("codex", false, "ollama"), "input");
  assert.equal(providerModelControl("openai_compatible", false), "input");
});

test("an empty dropdown tells the user how to populate it", () => {
  assert.equal(
    providerModelPlaceholder({ loading: false, hasCredential: false, optionCount: 0 }),
    "Enter an API key, then load models",
  );
  assert.equal(
    providerModelPlaceholder({ loading: true, hasCredential: true, optionCount: 0, providerType: "openai" }),
    "Loading available models…",
  );
  assert.equal(
    providerModelPlaceholder({ loading: false, hasCredential: false, optionCount: 0, providerType: "codex" }),
    "Connect Codex CLI, then load models",
  );
});
