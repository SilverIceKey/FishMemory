import test from 'node:test';
import assert from 'node:assert/strict';
import { apply, MEMORY_GUIDANCE } from '../guidance.js';

test('guidance is visible only with scoped memory tool and has cleanup', () => {
  let section, cleanup, disposed = false;
  const scope = {};
  const ctx = {
    tools: { get: (name, s) => name === 'mcp__fishmemory__memory_recall' && s === scope },
    systemPrompt: { section: s => { section = s; return () => { disposed = true; }; } },
    effect: f => { cleanup = f(); },
  };
  apply(ctx);
  assert.equal(section.text({ scope }), MEMORY_GUIDANCE);
  assert.equal(section.text({ scope: {} }), '');
  assert.equal(section.interpolate, false);
  cleanup();
  assert.equal(disposed, true);
});

