import test from "node:test"
import assert from "node:assert/strict"
import { collectUserEntryIds } from "../lib/context-entries.mjs"

const ENTRIES = [
  { type: "message", id: "e1", message: { role: "user", content: "hi" } },
  { type: "message", id: "e2", message: { role: "assistant", content: "hello" } },
  { type: "message", id: "e3", message: { role: "user", content: "again" } },
]

test("prefers buildContextEntries when the host exposes it", () => {
  const calls = []
  const sm = {
    buildContextEntries: () => { calls.push("bce"); return ENTRIES },
    getBranch: () => { calls.push("gb"); return ENTRIES },
  }
  assert.deepEqual(collectUserEntryIds(sm), ["e1", "e3"])
  assert.deepEqual(calls, ["bce"])
})

test("falls back to getBranch when buildContextEntries is missing (OMP 18.1.5)", () => {
  const sm = { getBranch: () => ENTRIES }
  assert.deepEqual(collectUserEntryIds(sm), ["e1", "e3"])
})

test("returns [] when neither API exists instead of throwing", () => {
  assert.deepEqual(collectUserEntryIds({}), [])
  assert.deepEqual(collectUserEntryIds(null), [])
  assert.deepEqual(collectUserEntryIds(undefined), [])
})

test("returns [] when the host returns a non-array", () => {
  assert.deepEqual(collectUserEntryIds({ buildContextEntries: () => null }), [])
  assert.deepEqual(collectUserEntryIds({ getBranch: () => ({}) }), [])
})

test("keeps positional placeholders for id-less user entries", () => {
  const entries = [
    { type: "message", id: "e1", message: { role: "user" } },
    { type: "message", message: { role: "user" } }, // no id: must stay aligned as undefined
    { type: "message", id: 42, message: { role: "user" } }, // non-string id: same
  ]
  const sm = { buildContextEntries: () => entries }
  assert.deepEqual(collectUserEntryIds(sm), ["e1", undefined, undefined])
})

test("ignores non-message entries and non-user roles", () => {
  const entries = [
    { type: "tool_call", id: "t1", message: { role: "user" } },
    { type: "message", id: "e1", message: { role: "user" } },
    { type: "message", id: "e2" },
    null,
    "garbage",
  ]
  assert.deepEqual(collectUserEntryIds({ getBranch: () => entries }), ["e1"])
})
