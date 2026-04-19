const { Client, GatewayIntentBits, EmbedBuilder } = require('discord.js');
const { chromium } = require('playwright');
const express = require('express');

// 🔐 ENV
const TOKEN = process.env.TOKEN;
const CHANNEL_ID = process.env.CHANNEL_ID;

// 🌐 SOURCE (STABLE)
const X_URL = "https://x.com/TruthTrumpPost";

const client = new Client({
  intents: [GatewayIntentBits.Guilds]
});

let cachedChannel;
let browser;
let page;
let lastPost = "";

/* =========================
   ERROR HANDLING
========================= */
process.on('unhandledRejection', err => console.log('Unhandled:', err));
process.on('uncaughtException', err => console.log('Uncaught:', err));

/* =========================
   BIAS ENGINE
========================= */
function getMarketBias(text) {
  const t = text.toLowerCase();

  const bullish = ["growth", "cut", "stimulus", "deal", "strong", "record"];
  const bearish = ["war", "recession", "inflation", "crisis", "ban", "tariff"];

  let bull = 0;
  let bear = 0;

  bullish.forEach(k => { if (t.includes(k)) bull += 2; });
  bearish.forEach(k => { if (t.includes(k)) bear += 2; });

  if (bull > bear) return "BULLISH 📈";
  if (bear > bull) return "BEARISH 📉";
  return "NEUTRAL ⚖️";
}

/* =========================
   INIT BROWSER
========================= */
async function initBrowser() {
  browser = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });

  page = await browser.newPage();

  console.log("🌐 Browser started");
}

/* =========================
   FETCH LATEST POST (X)
========================= */
async function fetchLatestPost() {
  try {
    if (!page) await initBrowser();

    await page.goto(X_URL, {
      waitUntil: "domcontentloaded",
      timeout: 60000
    });

    await page.waitForTimeout(8000);

    // X posts are inside article + lang div
    const posts = await page.$$eval("article div[lang]", els =>
      els.map(e => e.innerText.trim()).filter(Boolean)
    );

    const latest = posts[0];

    if (!latest || latest === lastPost) return null;

    lastPost = latest;

    const screenshot = await page.locator("article").first().screenshot();

    return {
      text: latest,
      image: screenshot
    };

  } catch (err) {
    console.log("Scraper error:", err.message);
    return null;
  }
}

/* =========================
   SEND TO DISCORD
========================= */
async function sendPost(post) {
  const bias = getMarketBias(post.text);

  const embed = new EmbedBuilder()
    .setColor('#1DA1F2')
    .setTitle('🐦 TRUMP RELATED POST ALERT')
    .addFields(
      { name: 'Post', value: post.text.slice(0, 1000) },
      { name: 'Bias', value: bias }
    )
    .setTimestamp();

  await cachedChannel.send({
    content: "@everyone 🐦 NEW POLITICAL POST DETECTED",
    embeds: [embed],
    allowedMentions: { parse: ['everyone'] }
  });
}

/* =========================
   LOOP (24/7)
========================= */
setInterval(async () => {
  const post = await fetchLatestPost();

  if (post) {
    console.log("NEW POST:", post.text);
    await sendPost(post);
  }
}, 60000);

/* =========================
   READY
========================= */
client.once('ready', async () => {
  console.log(`Logged in as ${client.user.tag}`);

  cachedChannel = await client.channels.fetch(CHANNEL_ID);

  await cachedChannel.send("🚀 TRUMP POST BOT ONLINE (X TRACKING)");

  await initBrowser();

  const test = await fetchLatestPost();

  if (test) {
    console.log("✅ Working");
    await sendPost(test);
  } else {
    console.log("⚠️ No post detected yet");
  }
});

/* =========================
   KEEP ALIVE
========================= */
const app = express();
app.get('/', (req, res) => res.send('Bot running'));
app.listen(process.env.PORT || 3000);

/* =========================
   LOGIN
========================= */
client.login(TOKEN);
