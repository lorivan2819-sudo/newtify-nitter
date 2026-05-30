import fs from "node:fs/promises";

const DEFAULT_UPSTREAMS = [];

const REQUEST_TIMEOUT_MS = Number(process.env.UPSTREAM_TIMEOUT_MS || 4500);
const X_TIMEOUT_MS = Number(process.env.X_TIMEOUT_MS || 7000);
const RSS_CACHE_SECONDS = Number(process.env.RSS_CACHE_SECONDS || 30);
const RSS_STALE_SECONDS = Number(process.env.RSS_STALE_SECONDS || 300);
const RSS_ERROR_STALE_SECONDS = Number(process.env.RSS_ERROR_STALE_SECONDS || 900);
const RSS_CACHE_CONTROL =
  `s-maxage=${RSS_CACHE_SECONDS}, stale-while-revalidate=${RSS_STALE_SECONDS}, ` +
  `stale-if-error=${RSS_ERROR_STALE_SECONDS}`;
const FEED_CACHE = globalThis.__cedarFeedCache || new Map();
globalThis.__cedarFeedCache = FEED_CACHE;

const COOKIE_BEARER_TOKEN =
  "Bearer AAAAAAAAAAAAAAAAAAAAAFXzAwAAAAAAMHCxpeSDG1gLNLghVe8d74hl6k4%3DRUMF4xAQLsbeBhTSRrCiQpJtxoGWeyHrDb5te2jpGskWDFW82F";
const GUEST_BEARER_TOKEN =
  "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA";

const GRAPH_USER = "-oaLodhGbbnzJBACb1kk2Q/UserByScreenName";
const GRAPH_USER_TWEETS = "PNd0vlufvrcIwrAnBYKE9g/UserTweets";
const GRAPH_USER_TWEETS_GUEST = "oRJs8SLCRNRbQzuZG93_oA/UserTweets";

const FEATURES = {
  android_graphql_skip_api_media_color_palette: false,
  blue_business_profile_image_shape_enabled: false,
  creator_subscriptions_tweet_preview_api_enabled: true,
  freedom_of_speech_not_reach_fetch_enabled: true,
  graphql_is_translatable_rweb_tweet_is_translatable_enabled: true,
  highlights_tweets_tab_ui_enabled: false,
  interactive_text_enabled: false,
  longform_notetweets_consumption_enabled: true,
  longform_notetweets_inline_media_enabled: true,
  longform_notetweets_rich_text_read_enabled: true,
  responsive_web_edit_tweet_api_enabled: true,
  responsive_web_enhance_cards_enabled: false,
  responsive_web_graphql_exclude_directive_enabled: true,
  responsive_web_graphql_skip_user_profile_image_extensions_enabled: false,
  responsive_web_graphql_timeline_navigation_enabled: true,
  responsive_web_text_conversations_enabled: false,
  responsive_web_twitter_article_tweet_consumption_enabled: true,
  tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled: true,
  unified_cards_destination_url_params_enabled: false,
  verified_phone_label_enabled: false,
  view_counts_everywhere_api_enabled: true
};

const USER_FIELD_TOGGLES = {
  withArticleRichContentState: true,
  withArticlePlainText: false,
  withGrokAnalyze: false,
  withDisallowedReplyControls: false
};

const USER_TWEETS_FIELD_TOGGLES = {
  withArticlePlainText: false
};

function upstreams() {
  const raw = process.env.NITTER_UPSTREAMS || "";
  const configured = raw
    .split(",")
    .map((item) => item.trim().replace(/\/$/, ""))
    .filter(Boolean);
  return configured.length ? configured : DEFAULT_UPSTREAMS;
}

function normalizeUser(value) {
  const user = String(value || "").replace(/^@/, "").trim();
  if (!/^[A-Za-z0-9_]{1,20}$/.test(user)) {
    return null;
  }
  return user;
}

function newestTweetId(feed) {
  const matches = [...feed.matchAll(/\/status\/(\d{8,})/g)];
  let newest = 0n;
  for (const match of matches) {
    const id = BigInt(match[1]);
    if (id > newest) {
      newest = id;
    }
  }
  return newest;
}

function looksLikeRss(feed) {
  return (
    /<(rss|feed)\b/i.test(feed) &&
    /<(item|entry)\b/i.test(feed) &&
    /\/status\/\d{8,}/.test(feed)
  );
}

function rememberFeed(user, text, details = {}) {
  if (!looksLikeRss(text)) {
    return;
  }
  FEED_CACHE.set(user.toLowerCase(), {
    text,
    details,
    cachedAt: Date.now()
  });
}

function cachedFeed(user) {
  return FEED_CACHE.get(user.toLowerCase()) || null;
}

function sendFeed(res, text, mode, headers = {}) {
  res.setHeader("Content-Type", "application/rss+xml; charset=utf-8");
  res.setHeader("Cache-Control", RSS_CACHE_CONTROL);
  res.setHeader("X-Cedar-Mode", mode);
  for (const [key, value] of Object.entries(headers)) {
    if (value !== undefined && value !== null && value !== "") {
      res.setHeader(key, String(value));
    }
  }
  res.status(200).send(text);
}

function shortError(error) {
  return String(error?.message || error || "").replace(/\s+/g, " ").slice(0, 180);
}

function isRateLimitError(error) {
  return /429|rate limit/i.test(shortError(error));
}

function emptyRss(username) {
  const now = new Date().toUTCString();
  return `<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
<channel>
<title>@${xml(username)}</title>
<link>https://x.com/${xml(username)}</link>
<description>Cedar fallback feed for @${xml(username)}</description>
<language>en-us</language>
<lastBuildDate>${xml(now)}</lastBuildDate>
</channel>
</rss>`;
}

async function fetchFeed(baseUrl, user) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  const url = `${baseUrl}/${encodeURIComponent(user)}/rss`;

  try {
    const response = await fetch(url, {
      signal: controller.signal,
      headers: {
        Accept: "application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
      }
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const text = await response.text();
    if (!looksLikeRss(text)) {
      throw new Error("No RSS items");
    }

    return {
      baseUrl,
      text,
      newestId: newestTweetId(text)
    };
  } finally {
    clearTimeout(timeout);
  }
}

async function loadSessions() {
  const raw = process.env.X_SESSIONS_B64
    ? Buffer.from(process.env.X_SESSIONS_B64, "base64").toString("utf8")
    : process.env.X_SESSIONS || (await readLocalSessions());

  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => JSON.parse(line))
    .filter((session) => session.auth_token && session.ct0);
}

async function readLocalSessions() {
  try {
    return await fs.readFile("sessions.jsonl", "utf8");
  } catch {
    return "";
  }
}

function xHeaders(session) {
  return {
    accept: "*/*",
    "accept-language": "en-US,en;q=0.9",
    authorization: COOKIE_BEARER_TOKEN,
    cookie: `auth_token=${session.auth_token}; ct0=${session.ct0}`,
    "content-type": "application/json",
    origin: "https://x.com",
    referer: "https://x.com/",
    "user-agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "x-csrf-token": session.ct0,
    "x-twitter-active-user": "yes",
    "x-twitter-auth-type": "OAuth2Session",
    "x-twitter-client-language": "en"
  };
}

async function guestToken() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), X_TIMEOUT_MS);

  try {
    const response = await fetch("https://api.twitter.com/1.1/guest/activate.json", {
      method: "POST",
      signal: controller.signal,
      headers: {
        authorization: GUEST_BEARER_TOKEN,
        "user-agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
          "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
      }
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`Guest HTTP ${response.status}: ${text.slice(0, 80)}`);
    }
    const data = JSON.parse(text);
    if (!data.guest_token) {
      throw new Error("No guest token");
    }
    return data.guest_token;
  } finally {
    clearTimeout(timeout);
  }
}

function guestHeaders(token) {
  return {
    accept: "*/*",
    "accept-language": "en-US,en;q=0.9",
    authorization: GUEST_BEARER_TOKEN,
    "content-type": "application/json",
    origin: "https://x.com",
    referer: "https://x.com/",
    "user-agent":
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 " +
      "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "x-guest-token": token,
    "x-twitter-active-user": "yes",
    "x-twitter-client-language": "en"
  };
}

async function xRequest(endpoint, variables, session, fieldToggles) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), X_TIMEOUT_MS);
  const params = new URLSearchParams({
    variables: JSON.stringify(variables),
    features: JSON.stringify(FEATURES)
  });

  if (fieldToggles) {
    params.set("fieldToggles", JSON.stringify(fieldToggles));
  }

  try {
    const response = await fetch(`https://x.com/i/api/graphql/${endpoint}?${params}`, {
      signal: controller.signal,
      headers: xHeaders(session)
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`X HTTP ${response.status}: ${text.slice(0, 80)}`);
    }
    return JSON.parse(text);
  } finally {
    clearTimeout(timeout);
  }
}

async function guestRequest(endpoint, variables, token, fieldToggles) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), X_TIMEOUT_MS);
  const params = new URLSearchParams({
    variables: JSON.stringify(variables),
    features: JSON.stringify(FEATURES)
  });

  if (fieldToggles) {
    params.set("fieldToggles", JSON.stringify(fieldToggles));
  }

  try {
    const response = await fetch(`https://x.com/i/api/graphql/${endpoint}?${params}`, {
      signal: controller.signal,
      headers: guestHeaders(token)
    });
    const text = await response.text();
    if (!response.ok) {
      throw new Error(`Guest X HTTP ${response.status}: ${text.slice(0, 80)}`);
    }
    return JSON.parse(text);
  } finally {
    clearTimeout(timeout);
  }
}

function userRestId(userJson) {
  return userJson?.data?.user?.result?.rest_id || null;
}

function unwrapTweet(result) {
  if (!result) {
    return null;
  }
  if (result.tweet) {
    return unwrapTweet(result.tweet);
  }
  if (result.__typename === "TweetWithVisibilityResults") {
    return unwrapTweet(result.tweet);
  }
  return result.legacy && result.rest_id ? result : null;
}

function timelineTweetResults(timelineJson) {
  const instructions =
    timelineJson?.data?.user?.result?.timeline_v2?.timeline?.instructions ||
    timelineJson?.data?.user?.result?.timeline?.timeline?.instructions ||
    [];
  const tweets = [];

  for (const instruction of instructions) {
    const entries = instruction.entries || instruction.addEntries?.entries || [];
    for (const entry of entries) {
      const content = entry.content || {};
      const direct = unwrapTweet(content.itemContent?.tweet_results?.result);
      if (direct) {
        tweets.push(direct);
      }
      for (const moduleItem of content.items || []) {
        const moduleTweet = unwrapTweet(
          moduleItem.item?.itemContent?.tweet_results?.result
        );
        if (moduleTweet) {
          tweets.push(moduleTweet);
        }
      }
    }
  }

  const seen = new Set();
  return tweets.filter((tweet) => {
    if (seen.has(tweet.rest_id)) {
      return false;
    }
    seen.add(tweet.rest_id);
    return true;
  });
}

function xml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function cdata(value) {
  return String(value || "").replaceAll("]]>", "]]]]><![CDATA[>");
}

function retweetedTweet(tweet) {
  return unwrapTweet(tweet?.legacy?.retweeted_status_result?.result);
}

function cleanTweetText(value) {
  return String(value || "")
    .replace(/https:\/\/t\.co\/[A-Za-z0-9_]+/g, "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/[ \t]{2,}/g, " ")
    .trim();
}

function tweetText(tweet) {
  const retweet = retweetedTweet(tweet);
  if (retweet) {
    const author = tweetAuthor(retweet, "unknown");
    return cleanTweetText(`RT @${author.screenName}: ${tweetText(retweet)}`);
  }

  return cleanTweetText(
    tweet.note_tweet?.note_tweet_results?.result?.text ||
    tweet.legacy?.full_text ||
    ""
  );
}

function mediaContentType(url, fallback = "") {
  if (fallback) {
    return fallback;
  }
  const path = String(url || "").split("?")[0].toLowerCase();
  if (path.endsWith(".png")) {
    return "image/png";
  }
  if (path.endsWith(".webp")) {
    return "image/webp";
  }
  if (path.endsWith(".mp4")) {
    return "video/mp4";
  }
  return "image/jpeg";
}

function bestVideoVariant(media) {
  const variants = media.video_info?.variants || [];
  return variants
    .filter((variant) => variant.content_type === "video/mp4" && variant.url)
    .sort((a, b) => (b.bitrate || 0) - (a.bitrate || 0))[0];
}

function mediaFromItem(item) {
  const video = bestVideoVariant(item);
  if (video?.url) {
    return {
      url: video.url,
      type: "video",
      contentType: video.content_type || "video/mp4"
    };
  }
  const imageUrl = item.media_url_https || item.media_url;
  if (!imageUrl) {
    return null;
  }
  return {
    url: imageUrl,
    type: item.type === "photo" ? "photo" : "image",
    contentType: mediaContentType(imageUrl)
  };
}

function isMediaUrl(value) {
  try {
    const url = new URL(String(value));
    const path = url.pathname.toLowerCase();
    return (
      ["pbs.twimg.com", "video.twimg.com", "ton.twimg.com"].includes(url.hostname) &&
      !path.includes("/profile_images/") &&
      !path.includes("/profile_banners/") &&
      !path.endsWith(".m3u8")
    );
  } catch {
    return false;
  }
}

function collectMedia(value, output = []) {
  if (!value || typeof value !== "object") {
    return output;
  }

  const direct = mediaFromItem(value);
  if (direct) {
    output.push(direct);
  }

  const imageValue = value.image_value?.url || value.thumbnail_image_original?.url;
  if (imageValue && isMediaUrl(imageValue)) {
    output.push({
      url: imageValue,
      type: "image",
      contentType: mediaContentType(imageValue)
    });
  }

  for (const [key, nested] of Object.entries(value)) {
    if (
      key === "profile_image_url_https" ||
      key === "profile_image_url" ||
      key === "profile_banner_url" ||
      key === "variants"
    ) {
      continue;
    }
    if (typeof nested === "string" && isMediaUrl(nested)) {
      output.push({
        url: nested,
        type: nested.includes("video.twimg.com") ? "video" : "image",
        contentType: mediaContentType(nested)
      });
    } else if (nested && typeof nested === "object") {
      collectMedia(nested, output);
    }
  }

  return output;
}

function tweetMedia(tweet) {
  const retweet = retweetedTweet(tweet);
  if (retweet) {
    return tweetMedia(retweet);
  }

  const media = collectMedia({
    legacy: tweet.legacy,
    note_tweet: tweet.note_tweet,
    card: tweet.card
  });
  const seen = new Set();

  return media
    .filter(Boolean)
    .filter((item) => {
      if (seen.has(item.url)) {
        return false;
      }
      seen.add(item.url);
      return true;
    });
}

function tweetAuthor(tweet, fallbackUser) {
  const result = tweet.core?.user_results?.result || {};
  const user = result.core || result.legacy || {};
  return {
    name: user?.name || fallbackUser,
    screenName: user?.screen_name || fallbackUser
  };
}

function directRss(username, tweets) {
  const now = new Date().toUTCString();
  const items = tweets
    .map((tweet) => {
      const author = tweetAuthor(tweet, username);
      const text = tweetText(tweet);
      const url = `https://x.com/${author.screenName}/status/${tweet.rest_id}`;
      const media = tweetMedia(tweet);
      const imageHtml = media
        .filter((item) => item.contentType.startsWith("image/"))
        .map((item) => `<br><img src="${xml(item.url)}">`)
        .join("");
      const enclosures = media
        .map((item) => `<enclosure url="${xml(item.url)}" type="${xml(item.contentType)}" />`)
        .join("");
      const description = `${xml(text).replaceAll("\n", "<br>")}${imageHtml}`;
      const created = tweet.legacy?.created_at
        ? new Date(tweet.legacy.created_at).toUTCString()
        : now;

      return [
        "<item>",
        `<title>${xml(text || url)}</title>`,
        `<dc:creator>@${xml(author.screenName)}</dc:creator>`,
        `<description><![CDATA[${cdata(description)}]]></description>`,
        `<pubDate>${xml(created)}</pubDate>`,
        `<guid>${xml(url)}</guid>`,
        `<link>${xml(url)}</link>`,
        enclosures,
        "</item>"
      ].join("");
    })
    .join("");

  return `<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:atom="http://www.w3.org/2005/Atom" xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">
<channel>
<title>@${xml(username)}</title>
<link>https://x.com/${xml(username)}</link>
<description>Newtify X RSS feed for @${xml(username)}</description>
<language>en-us</language>
<lastBuildDate>${xml(now)}</lastBuildDate>
${items}
</channel>
</rss>`;
}

async function directFeed(user) {
  const sessions = await loadSessions();
  let lastError = null;

  for (const session of sessions) {
    try {
      const userJson = await xRequest(
        GRAPH_USER,
        { screen_name: user, withGrokTranslatedBio: false },
        session,
        USER_FIELD_TOGGLES
      );
      const restId = userRestId(userJson);
      if (!restId) {
        throw new Error("No user id");
      }

      const timelineJson = await xRequest(
        GRAPH_USER_TWEETS,
        {
          userId: restId,
          count: 20,
          includePromotedContent: true,
          withQuickPromoteEligibilityTweetFields: true,
          withVoice: true
        },
        session,
        USER_TWEETS_FIELD_TOGGLES
      );
      const tweets = timelineTweetResults(timelineJson);
      if (!tweets.length) {
        throw new Error("No tweets");
      }
      return directRss(user, tweets);
    } catch (error) {
      lastError = error;
    }
  }

  try {
    return await guestFeed(user);
  } catch (error) {
    if (lastError) {
      throw new Error(`${lastError.message}; guest: ${error.message}`);
    }
    throw error;
  }
}

async function guestFeed(user) {
  const token = await guestToken();
  const userJson = await guestRequest(
    GRAPH_USER,
    { screen_name: user, withGrokTranslatedBio: false },
    token,
    USER_FIELD_TOGGLES
  );
  const restId = userRestId(userJson);
  if (!restId) {
    throw new Error("No guest user id");
  }

  const timelineJson = await guestRequest(
    GRAPH_USER_TWEETS_GUEST,
    {
      userId: restId,
      count: 20,
      includePromotedContent: false,
      withQuickPromoteEligibilityTweetFields: true,
      withVoice: true
    },
    token,
    USER_TWEETS_FIELD_TOGGLES
  );
  const tweets = timelineTweetResults(timelineJson);
  if (!tweets.length) {
    throw new Error("No guest tweets");
  }
  return directRss(user, tweets);
}

async function upstreamFeed(user) {
  const configuredUpstreams = upstreams();
  if (!configuredUpstreams.length) {
    throw new Error("No upstream RSS mirrors configured");
  }

  const results = await Promise.allSettled(
    configuredUpstreams.map((baseUrl) => fetchFeed(baseUrl, user))
  );

  const feeds = results
    .filter((result) => result.status === "fulfilled")
    .map((result) => result.value)
    .sort((a, b) => (a.newestId > b.newestId ? -1 : a.newestId < b.newestId ? 1 : 0));

  if (!feeds.length) {
    throw new Error("No upstream RSS feed available");
  }

  return feeds[0];
}

export default async function handler(req, res) {
  const user = normalizeUser(req.query.user);
  if (!user) {
    res.status(400).send("Invalid user");
    return;
  }

  try {
    const rss = await directFeed(user);
    rememberFeed(user, rss, { mode: "direct" });
    sendFeed(res, rss, "direct");
    return;
  } catch (directError) {
    try {
      const best = await upstreamFeed(user);
      rememberFeed(user, best.text, {
        mode: "upstream",
        upstream: best.baseUrl,
        newestId: best.newestId.toString()
      });
      sendFeed(res, best.text, "upstream", {
        "X-Cedar-Upstream": best.baseUrl,
        "X-Cedar-Newest-Id": best.newestId.toString()
      });
    } catch (upstreamError) {
      const cached = cachedFeed(user);
      if (cached) {
        sendFeed(res, cached.text, "stale-cache", {
          "X-Cedar-Cache-Age": Math.floor((Date.now() - cached.cachedAt) / 1000),
          "X-Cedar-Source-Error": shortError(directError),
          "X-Cedar-Upstream-Error": shortError(upstreamError),
          "X-Cedar-Previous-Mode": cached.details?.mode
        });
        return;
      }
      if (isRateLimitError(directError)) {
        sendFeed(res, emptyRss(user), "rate-limited-empty", {
          "X-Cedar-Source-Error": shortError(directError),
          "X-Cedar-Upstream-Error": shortError(upstreamError)
        });
        return;
      }
      res.setHeader("Cache-Control", "no-store");
      res.status(502).send(`No RSS feed available: ${directError.message}`);
    }
  }
}
