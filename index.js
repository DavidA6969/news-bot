const { Client, GatewayIntentBits, EmbedBuilder } = require('discord.js');
const { chromium } = require('playwright');
const axios = require('axios');
const cheerio = require('cheerio');
const express = require('express');

// 🔐 ENV
const TOKEN = process.env.TOKEN;
const CHANNEL_ID = process.env.CHANNEL_ID;

// 🌐 SOURCES
const NEWS_URL = "https://www.reuters.com/markets/";

const client = new Client({
  intents: [GatewayIntentBits.Guilds]
});

let cachedChannel;
let browser;
let page;
let lastTrumpPost = "";

/* =========================
   💥 CRASH PROTECTION
========================= */
process.on('unhandledRejection', err => console.log('Unhandled:', err));
process.on('uncaughtException', err => console.log('Uncaught:', err));

/* =========================
   🧠 MARKET BIAS
========================= */
function getMarketBias(text) {
  const t = text.toLowerCase();

  const bullish = ["rate cut", "stimulus", "growth", "cooling inflation", "soft landing"];
  const bearish = ["rate hike", "inflation", "war", "recession", "oil spike", "china"];

  let bull = 0;
  let bear = 0;

  bullish.forEach(k => { if (t.includes(k)) bull += 2; });
  bearish.forEach(k => { if (t.includes(k)) bear += 2; });

  if (bull > bear) return "BULLISH 📈";
  if (bear > bull) return "BEARISH 📉";
  return "NEUTRAL ⚖️";
}

/* =========================
   🐦 INIT BROWSER (24/7 CORE)
========================= */
async function initBrowser() {
  browser = await chromium.launch({
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox'
    ]
  });

  page = await browser.newPage();

  await page.goto("https://truthsocial.com/@realDonaldTrump", {
    waitUntil: "networkidle",
    timeout: 60000
  });

  console.log("🌐 Truth Social browser started");
}

/* =========================
   🐦 FETCH TRUMP (LIVE PAGE)
========================= */
async function fetchTrumpTruth() {
  try {
    if (!page) await initBrowser();

    await page.reload({ waitUntil: "networkidle" });
    await page.waitForTimeout(5000);

    const postText = await page.locator("article").first().innerText();

    const screenshot = await page.locator("article").first().screenshot();

    if (!postText || postText === lastTrumpPost) return null;

    lastTrumpPost = postText;

    return {
      text: postText,
      image: screenshot
    };

  } catch (err) {
    console.log("Browser error → restarting:", err.message);

    try {
      await browser.close();
    } catch {}

    browser = null;
    page = null;

    return null;
  }
}

/* =========================
   📰 NEWS (REUTERS LIGHT SCRAPE)
========================= */
async function fetchNews() {
  try {
    const { data } = await axios.get(NEWS_URL, { timeout: 10000 });
    const $ = cheerio.load(data);

    let news = [];

    $("a[data-testid='Heading']").each((i, el) => {
      const title = $(el).text().trim();

      const keywords = ["fed","inflation","war","oil","china","recession","economy"];

      if (keywords.some(k => title.toLowerCase().includes(k))) {
        news.push(title);
      }
    });

    return news;

  } catch (err) {
    console.log("News error:", err.message);
    return [];
  }
}

/* =========================
   📢 DISCORD SEND
========================= */
async function fetchTrumpTruth() {
  try {
    if (!page) await initBrowser();

    await page.goto("https://truthsocial.com/@realDonaldTrump", {
      waitUntil: "domcontentloaded",
      timeout: 60000
    });

    // wait for posts to actually render
    await page.waitForTimeout(8000);

    // grab all visible text from main feed
    const posts = await page.$$eval("article", els =>
      els.map(el => el.innerText.trim()).filter(Boolean)
    );

    const latest = posts[0];

    if (!latest) return null;

    if (latest === lastTrumpPost) return null;

    lastTrumpPost = latest;

    const screenshot = await page.locator("article").first().screenshot();

    return {
      text: latest,
      image: screenshot
    };

  } catch (err) {
    console.log("Truth error:", err.message);
    return null;
  }
}

/* =========================
   🔁 LOOPS (24/7 SAFE)
========================= */
setInterval(async () => {
  const post = await fetchTrumpTruth();
  if (post) await sendTrump(post);
}, 60000);

/* =========================
   🚀 READY
========================= */
client.once('ready', async () => {
  console.log(`Logged in as ${client.user.tag}`);

  cachedChannel = await client.channels.fetch(CHANNEL_ID);

  await cachedChannel.send("🚀 24/7 TRUTH SOCIAL BOT ONLINE");

  await initBrowser();

  console.log("🧪 Testing Truth Social scraper...");

  const test = await fetchTrumpTruth();

  console.log("RAW RESULT:", test);

  if (test && test.text) {
    console.log("✅ Browser scraping working");
    await sendTrump(test);
  } else {
    console.log("⚠️ No post detected (login wall, selector issue, or delay)");
  }
});

/* =========================
   🌐 KEEP ALIVE
========================= */
const app = express();
app.get('/', (req, res) => res.send('Bot running'));
app.listen(process.env.PORT || 3000);

/* =========================
   🔐 LOGIN
========================= */
client.login(TOKEN);
