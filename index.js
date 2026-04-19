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
async function sendTrump(post) {
  const bias = getMarketBias(post.text);

  const embed = new EmbedBuilder()
    .setColor('#f1c40f')
    .setTitle('🐦 TRUTH SOCIAL ALERT')
    .setDescription(post.text.slice(0, 2000))
    .addFields({ name: 'Bias', value: bias })
    .setImage('attachment://truth.png')
    .setTimestamp();

  await cachedChannel.send({
    content: "@everyone 🐦 TRUMP POST",
    embeds: [embed],
    files: [{ attachment: post.image, name: "truth.png" }],
    allowedMentions: { parse: ['everyone'] }
  });
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
console.log("🧪 Testing Truth Social scraper...");

const test = await fetchTrumpTruth();

console.log("RAW RESULT:", test);

if (test && test.text) {
  console.log("✅ Browser scraping working");

  // send to Discord
  await sendTrump(test);
} else {
  console.log("⚠️ No post detected (login wall, selector issue, or delay)");
}
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
