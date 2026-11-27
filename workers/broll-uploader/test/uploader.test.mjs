import test from "node:test";
import assert from "node:assert/strict";

import {
  extractVideo,
  getTelegramUserId,
  isAllowedUser,
  parseTags,
  validateDescription,
  validateLicense,
  validateSourceUrl,
} from "../src/index.mjs";

test("allowlist matches exact numeric Telegram IDs", () => {
  assert.equal(isAllowedUser(12345, "12345,67890"), true);
  assert.equal(isAllowedUser(123, "12345,67890"), false);
  assert.equal(isAllowedUser(12345, ""), false);
});

test("extracts Telegram video messages as MP4", () => {
  const media = extractVideo({
    video: { file_id: "abc", file_size: 1234, mime_type: "video/mp4" },
  });
  assert.equal(media.fileId, "abc");
  assert.equal(media.extension, ".mp4");
  assert.equal(media.mimeType, "video/mp4");
});

test("accepts supported video documents but rejects arbitrary documents", () => {
  const media = extractVideo({
    document: {
      file_id: "doc1",
      file_name: "city-traffic.webm",
      mime_type: "video/webm",
      file_size: 1000,
    },
  });
  assert.equal(media.extension, ".webm");
  assert.equal(extractVideo({
    document: { file_id: "pdf1", file_name: "licence.pdf", mime_type: "application/pdf" },
  }), null);
});

test("extracts Telegram sender IDs from messages and callback queries", () => {
  assert.equal(getTelegramUserId({ message: { from: { id: 77 } } }), 77);
  assert.equal(getTelegramUserId({ callback_query: { from: { id: 88 } } }), 88);
  assert.equal(getTelegramUserId({}), null);
});

test("normalises a valid visible-content description", () => {
  assert.equal(
    validateDescription("  A   person using a smartphone outdoors.  "),
    "A person using a smartphone outdoors.",
  );
  assert.throws(() => validateDescription("Short"), /12 and 500/);
});

test("parses, lowercases and deduplicates tags", () => {
  assert.deepEqual(
    parseTags("Technology, Smartphone, technology, social media"),
    ["technology", "smartphone", "social media"],
  );
  assert.throws(() => parseTags(""), /between 1 and 12/);
  assert.throws(() => parseTags("bad/tag"), /letters, numbers/);
});

test("source URLs must be absolute HTTP(S) webpages", () => {
  assert.equal(validateSourceUrl("https://example.com/video"), "https://example.com/video");
  assert.throws(() => validateSourceUrl("javascript:alert(1)"), /valid original asset webpage URL/);
  assert.throws(() => validateSourceUrl("not-a-url"), /original asset webpage URL/);
});

test("licence notes are required and bounded", () => {
  assert.equal(validateLicense("Pexels License; commercial use"), "Pexels License; commercial use");
  assert.throws(() => validateLicense("free"), /Licence details/);
});
