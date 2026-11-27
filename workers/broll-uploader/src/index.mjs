const CATALOG_KEY = "broll/catalog.json";
const PENDING_PREFIX = "broll/_pending/";
const UPLOAD_PREFIX = "broll/uploads/";
const MAX_UPLOAD_BYTES = 20_000_000;
const PENDING_TTL_MS = 24 * 60 * 60 * 1000;
const ALLOWED_EXTENSIONS = new Set([".mp4", ".mov", ".mkv", ".webm"]);

export function getTelegramUserId(update) {
  return update?.message?.from?.id ?? update?.callback_query?.from?.id ?? null;
}

export function isAllowedUser(userId, allowlist) {
  if (userId === null || userId === undefined) return false;
  const allowed = String(allowlist || "")
    .split(",")
    .map((value) => value.trim())
    .filter(Boolean);
  return allowed.includes(String(userId));
}

export function extractVideo(message) {
  if (!message || typeof message !== "object") return null;

  if (message.video?.file_id) {
    return {
      fileId: message.video.file_id,
      fileSize: Number(message.video.file_size || 0),
      fileName: "telegram-video.mp4",
      mimeType: message.video.mime_type || "video/mp4",
      extension: ".mp4",
    };
  }

  const document = message.document;
  if (!document?.file_id) return null;

  const fileName = String(document.file_name || "upload").trim();
  const extension = (fileName.match(/\.[a-z0-9]{2,5}$/i)?.[0] || "").toLowerCase();
  const mimeType = String(document.mime_type || "").toLowerCase();
  if (!ALLOWED_EXTENSIONS.has(extension)) return null;
  if (mimeType && !mimeType.startsWith("video/") && mimeType !== "application/octet-stream") return null;

  return {
    fileId: document.file_id,
    fileSize: Number(document.file_size || 0),
    fileName,
    mimeType,
    extension,
  };
}

export function validateDescription(value) {
  const text = String(value || "").trim().replace(/\s+/g, " ");
  if (text.length < 12 || text.length > 500) {
    throw new Error("Description must be between 12 and 500 characters.");
  }
  return text;
}

export function parseTags(value) {
  const tags = [...new Set(
    String(value || "")
      .split(",")
      .map((tag) => tag.trim().toLowerCase())
      .filter(Boolean),
  )];
  if (tags.length < 1 || tags.length > 12) {
    throw new Error("Send between 1 and 12 comma-separated tags.");
  }
  if (tags.some((tag) => tag.length > 32 || !/^[a-z0-9][a-z0-9 -]*$/.test(tag))) {
    throw new Error("Tags may contain letters, numbers, spaces and hyphens only (32 characters max).");
  }
  return tags;
}

export function validateSourceUrl(value) {
  const text = String(value || "").trim();
  let url;
  try {
    url = new URL(text);
  } catch {
    throw new Error("Send the original asset webpage URL, starting with https:// or http://.");
  }
  if (!["http:", "https:"].includes(url.protocol) || !url.hostname.includes(".")) {
    throw new Error("Send a valid original asset webpage URL.");
  }
  return url.toString();
}

export function validateLicense(value) {
  const text = String(value || "").trim().replace(/\s+/g, " ");
  if (text.length < 8 || text.length > 300) {
    throw new Error("Licence details must be between 8 and 300 characters.");
  }
  return text;
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function pendingKey(userId) {
  return PENDING_PREFIX + userId + ".json";
}

async function readPending(bucket, userId) {
  const object = await bucket.get(pendingKey(userId));
  if (!object) return null;
  try {
    const pending = await object.json();
    if (Date.now() - Number(pending.created_at || 0) > PENDING_TTL_MS) {
      await bucket.delete(pending.key);
      await bucket.delete(pendingKey(userId));
      return null;
    }
    return pending;
  } catch {
    await bucket.delete(pendingKey(userId));
    return null;
  }
}

async function writePending(bucket, userId, pending) {
  await bucket.put(pendingKey(userId), JSON.stringify(pending), {
    httpMetadata: { contentType: "application/json; charset=utf-8" },
  });
}

async function telegramApi(env, method, payload) {
  const response = await fetch("https://api.telegram.org/bot" + env.TELEGRAM_BOT_TOKEN + "/" + method, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const result = await response.json();
  if (!response.ok || !result.ok) {
    throw new Error("Telegram " + method + " failed: " + (result.description || response.status));
  }
  return result.result;
}

async function sendMessage(env, chatId, text, replyMarkup) {
  const payload = { chat_id: chatId, text, disable_web_page_preview: true };
  if (replyMarkup) payload.reply_markup = replyMarkup;
  return telegramApi(env, "sendMessage", payload);
}

async function answerCallback(env, callbackId, text) {
  return telegramApi(env, "answerCallbackQuery", {
    callback_query_id: callbackId,
    text: text.slice(0, 180),
    show_alert: false,
  });
}

function uploadButtons(assetId) {
  return {
    inline_keyboard: [[
      { text: "I have checked the rights — publish", callback_data: "broll:confirm:" + assetId },
      { text: "Cancel upload", callback_data: "broll:cancel:" + assetId },
    ]],
  };
}

async function uploadFromTelegram(env, message, userId, chatId) {
  const media = extractVideo(message);
  if (!media) {
    await sendMessage(
      env,
      chatId,
      "Please send a video as a Telegram video or as a video document (.mp4, .mov, .mkv or .webm).",
    );
    return;
  }
  if (media.fileSize > MAX_UPLOAD_BYTES) {
    await sendMessage(
      env,
      chatId,
      "This file is larger than the Telegram bot download limit (20 MB). Please send a smaller/compressed clip for now.",
    );
    return;
  }

  const current = await readPending(env.BROLL_BUCKET, userId);
  if (current) {
    await sendMessage(env, chatId, "You already have an upload being described. Finish it or send /cancel before uploading another.");
    return;
  }

  await sendMessage(env, chatId, "Receiving your B-roll clip from Telegram and saving it to R2…");
  const fileInfo = await telegramApi(env, "getFile", { file_id: media.fileId });
  if (!fileInfo.file_path) throw new Error("Telegram did not return a downloadable file path.");
  if (Number(fileInfo.file_size || media.fileSize) > MAX_UPLOAD_BYTES) {
    await sendMessage(env, chatId, "Telegram reports that this file is larger than 20 MB. Please send a smaller clip.");
    return;
  }

  const fileResponse = await fetch(
    "https://api.telegram.org/file/bot" + env.TELEGRAM_BOT_TOKEN + "/" + fileInfo.file_path,
  );
  if (!fileResponse.ok || !fileResponse.body) {
    throw new Error("Could not download the Telegram file (HTTP " + fileResponse.status + ").");
  }
  const bytes = await fileResponse.arrayBuffer();
  if (bytes.byteLength === 0 || bytes.byteLength > MAX_UPLOAD_BYTES) {
    throw new Error("Downloaded file is empty or exceeds the 20 MB limit.");
  }

  const id = "br-" + crypto.randomUUID().replaceAll("-", "").slice(0, 16);
  const extension = media.extension || ".mp4";
  const key = UPLOAD_PREFIX + id + extension;
  const mimeByExtension = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
  };
  const contentType = media.mimeType.startsWith("video/")
    ? media.mimeType
    : mimeByExtension[extension] || "video/mp4";

  await env.BROLL_BUCKET.put(key, bytes, {
    httpMetadata: { contentType },
    customMetadata: {
      original_filename: media.fileName.slice(0, 180),
      telegram_user_id: String(userId),
      rights_verified: "false",
    },
  });

  const pending = {
    id,
    key,
    original_filename: media.fileName,
    content_type: contentType,
    file_size: bytes.byteLength,
    created_at: Date.now(),
    step: "description",
  };
  await writePending(env.BROLL_BUCKET, userId, pending);
  await sendMessage(
    env,
    chatId,
    "Uploaded to R2 staging: " + media.fileName +
      "\n\nNow describe what is visibly happening in the footage (12–500 characters). Do not guess the story or add claims. Send /cancel to discard it.",
  );
}

async function handleMetadataMessage(env, message, pending, userId, chatId) {
  const text = String(message.text || "").trim();
  try {
    if (pending.step === "description") {
      pending.description = validateDescription(text);
      pending.step = "tags";
      await writePending(env.BROLL_BUCKET, userId, pending);
      await sendMessage(env, chatId, "Good. Now send 1–12 comma-separated tags.\nExample: technology, smartphone, social media, scrolling");
      return;
    }
    if (pending.step === "tags") {
      pending.tags = parseTags(text);
      pending.step = "source_url";
      await writePending(env.BROLL_BUCKET, userId, pending);
      await sendMessage(env, chatId, "Send the original source webpage URL for this footage (not the R2 URL).");
      return;
    }
    if (pending.step === "source_url") {
      pending.source_url = validateSourceUrl(text);
      pending.step = "license";
      await writePending(env.BROLL_BUCKET, userId, pending);
      await sendMessage(
        env,
        chatId,
        "Enter the exact licence or permission that allows your intended use (for example, Pexels License or your own recorded footage). Do not claim rights you have not checked.",
      );
      return;
    }
    if (pending.step === "license") {
      pending.license = validateLicense(text);
      pending.step = "confirm";
      await writePending(env.BROLL_BUCKET, userId, pending);
      const summary =
        "Review this B-roll asset:\n\n" +
        "Description: " + pending.description + "\n" +
        "Tags: " + pending.tags.join(", ") + "\n" +
        "Source: " + pending.source_url + "\n" +
        "Licence: " + pending.license + "\n" +
        "R2 key: " + pending.key + "\n\n" +
        "Publishing marks rights_verified=true. Confirm only after checking that the licence/permission covers the intended use.";
      await sendMessage(env, chatId, summary, uploadButtons(pending.id));
      return;
    }
    if (pending.step === "confirm") {
      await sendMessage(env, chatId, "Use the buttons on the review message to publish or cancel this asset.");
      return;
    }
  } catch (error) {
    await sendMessage(env, chatId, error.message + "\n\nPlease try again.");
  }
}

export async function mergeAssetIntoCatalog(bucket, asset) {
  for (let attempt = 0; attempt < 5; attempt += 1) {
    const current = await bucket.get(CATALOG_KEY);
    let catalog = { assets: [] };
    if (current) {
      try {
        catalog = await current.json();
      } catch {
        throw new Error("broll/catalog.json contains invalid JSON. Fix it before publishing another asset.");
      }
      if (!catalog || !Array.isArray(catalog.assets)) {
        throw new Error("broll/catalog.json must contain an assets array.");
      }
    }

    const duplicate = catalog.assets.find((row) => row?.id === asset.id || row?.key === asset.key);
    if (duplicate) {
      if (duplicate.id === asset.id && duplicate.key === asset.key) return catalog.assets.length;
      throw new Error("This asset ID or R2 key already exists in the B-roll catalogue.");
    }

    const next = { assets: [...catalog.assets, asset] };
    const options = {
      httpMetadata: { contentType: "application/json; charset=utf-8" },
    };
    if (current) {
      options.onlyIf = { etagMatches: current.httpEtag };
    } else {
      options.onlyIf = { etagDoesNotMatch: "*" };
    }
    const saved = await bucket.put(
      CATALOG_KEY,
      JSON.stringify(next, null, 2) + "\n",
      options,
    );
    if (saved) return next.assets.length;
  }
  throw new Error("The B-roll catalogue changed repeatedly. Please press publish again.");
}

async function handleCallback(env, callback) {
  const userId = callback.from?.id;
  const chatId = callback.message?.chat?.id;
  await answerCallback(env, callback.id, "Processing…");
  if (!isAllowedUser(userId, env.ALLOWED_TELEGRAM_USER_IDS)) {
    if (chatId) await sendMessage(env, chatId, "You are not authorised to manage this B-roll library.");
    return;
  }
  if (!chatId || callback.message.chat.type !== "private") return;

  const match = String(callback.data || "").match(/^broll:(confirm|cancel):(br-[a-f0-9]{16})$/);
  if (!match) return;
  const action = match[1];
  const assetId = match[2];
  const pending = await readPending(env.BROLL_BUCKET, userId);
  if (!pending || pending.id !== assetId || pending.step !== "confirm") {
    await sendMessage(env, chatId, "This upload is no longer pending. Please upload it again if needed.");
    return;
  }

  if (action === "cancel") {
    await env.BROLL_BUCKET.delete(pending.key);
    await env.BROLL_BUCKET.delete(pendingKey(userId));
    await sendMessage(env, chatId, "Upload cancelled. The staged R2 video has been deleted.");
    return;
  }

  const asset = {
    id: pending.id,
    key: pending.key,
    description: pending.description,
    tags: pending.tags,
    source_url: pending.source_url,
    license: pending.license,
    rights_verified: true,
  };
  const count = await mergeAssetIntoCatalog(env.BROLL_BUCKET, asset);
  await env.BROLL_BUCKET.delete(pendingKey(userId));
  await sendMessage(
    env,
    chatId,
    "Published successfully. The B-roll library now contains " + count + " assets.\n\n" +
      "Asset ID: " + asset.id + "\nR2 key: " + asset.key +
      "\n\nThe editor can now select this footage when its description and tags match a clip.",
  );
}

async function handleMessage(env, message) {
  const chatId = message.chat?.id;
  const userId = message.from?.id;
  if (!chatId || message.chat?.type !== "private" || !userId) return;

  const text = String(message.text || "").trim();
  if (text === "/whoami") {
    await sendMessage(env, chatId, "Your Telegram user ID is: " + userId +
      "\n\nSend this ID to the library owner so it can be added to the uploader allowlist.");
    return;
  }
  if (!isAllowedUser(userId, env.ALLOWED_TELEGRAM_USER_IDS)) {
    await sendMessage(env, chatId, "This uploader is private.\nYour Telegram ID: " + userId +
      "\nAsk the owner to add this ID to ALLOWED_TELEGRAM_USER_IDS.");
    return;
  }

  if (text === "/start" || text === "/help") {
    await sendMessage(
      env,
      chatId,
      "B-roll Uploader\n\nSend a video (up to 20 MB) to upload it to your Cloudflare R2 bucket. I'll then collect its description, tags, source page and licence, and ask you to confirm before publishing it to broll/catalog.json.\n\nCommands:\n/cancel — discard your unfinished upload\n/status — show the number of published B-roll assets\n/whoami — show your Telegram ID\n/help — show this message\n\nOnly authorised Telegram accounts can use this uploader.",
    );
    return;
  }
  if (text === "/cancel") {
    const pending = await readPending(env.BROLL_BUCKET, userId);
    if (!pending) {
      await sendMessage(env, chatId, "There is no unfinished upload.");
      return;
    }
    await env.BROLL_BUCKET.delete(pending.key);
    await env.BROLL_BUCKET.delete(pendingKey(userId));
    await sendMessage(env, chatId, "Cancelled. The staged video has been deleted from R2.");
    return;
  }
  if (text === "/status") {
    const object = await env.BROLL_BUCKET.get(CATALOG_KEY);
    if (!object) {
      await sendMessage(env, chatId, "The B-roll catalogue is empty. Send your first video to begin.");
      return;
    }
    const catalog = await object.json();
    const count = Array.isArray(catalog?.assets) ? catalog.assets.length : 0;
    const pending = await readPending(env.BROLL_BUCKET, userId);
    await sendMessage(env, chatId, "Published B-roll assets: " + count +
      "\nUnfinished upload: " + (pending ? "yes" : "no"));
    return;
  }

  const media = extractVideo(message);
  if (media) {
    await uploadFromTelegram(env, message, userId, chatId);
    return;
  }
  if (message.document) {
    await sendMessage(env, chatId, "That document is not a supported video. Send MP4, MOV, MKV or WEBM video.");
    return;
  }

  const pending = await readPending(env.BROLL_BUCKET, userId);
  if (pending) {
    await handleMetadataMessage(env, message, pending, userId, chatId);
  } else {
    await sendMessage(env, chatId, "Send a video to upload it. Use /help for instructions.");
  }
}

async function handleUpdate(env, update) {
  if (update.callback_query) {
    await handleCallback(env, update.callback_query);
    return;
  }
  if (update.message) {
    await handleMessage(env, update.message);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "GET" && url.pathname === "/health") {
      return jsonResponse({ ok: true, service: "telegram-broll-uploader" });
    }
    if (request.method !== "POST" || url.pathname !== "/telegram/webhook") {
      return new Response("Not found", { status: 404 });
    }
    if (
      !env.TELEGRAM_BOT_TOKEN ||
      !env.TELEGRAM_WEBHOOK_SECRET ||
      request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.TELEGRAM_WEBHOOK_SECRET
    ) {
      return new Response("Unauthorized", { status: 401 });
    }

    let update;
    try {
      const length = Number(request.headers.get("content-length") || 0);
      if (length > 1_000_000) return new Response("Payload too large", { status: 413 });
      update = await request.json();
      await handleUpdate(env, update);
      return jsonResponse({ ok: true });
    } catch (error) {
      console.error("B-roll uploader webhook error", error);
      const message = update?.message;
      const callback = update?.callback_query;
      const chatId = message?.chat?.id ?? callback?.message?.chat?.id;
      if (chatId) {
        try {
          await sendMessage(env, chatId, "I couldn't complete that action: " + error.message);
        } catch (sendError) {
          console.error("Could not report uploader error to Telegram", sendError);
        }
      }
      return jsonResponse({ ok: false, error: "update_failed" });
    }
  },
};
