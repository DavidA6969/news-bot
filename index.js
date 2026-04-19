const { Client, GatewayIntentBits, EmbedBuilder } = require('discord.js');
const { chromium } = require('playwright');
const express = require('express');

// 🔐 ENV
const TOKEN = process.env.TOKEN;
const CHANNEL_ID = process.env.CHANNEL_ID;

// 🌐 CLIENT
const client = new Client({
  intents: [GatewayIntentBits.Guilds]
});

let cachedChannel;
let browser;
let page;
let lastTrumpPost = "";

/* =========================
   💥 ERROR HANDLING
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
   🐦 INIT BROWSER
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
   🐦 FETCH TRUMP POST
========================= */
async function fetchTrumpTruth() {
  try {
    if (!page) await initBrowser();

    await page.goto("https://truthsocial.com/@realDonaldTrump", {
      waitUntil: "domcontentloaded",
      timeout: 60000
    });

    await page.waitForTimeout(8000);

    const posts = await page.$$eval("article", els =>
      els.map(el => el.innerText.trim()).filter(Boolean)
    );

    const latest = posts[0];

    if (!latest || latest === lastTrumpPost) return null;

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
   📢 SEND DISCORD MESSAGE
========================= */
async function sendTrump(post) {
  const bias = getMarketBias(post.text);

  const embed = new EmbedBuilder()
    .setColor('#f1c40f')
    .setTitle('🐦 TRUMP POST ALERT')
    .addFields(
      { name: 'Post', value: post.text.slice(0, 1000) },
      { name: 'Bias', value: bias }
    )
    .setTimestamp();

  await cachedChannel.send({
    content: "@everyone 🐦 NEW TRUMP POST",
    embeds: [embed],
    allowedMentions: { parse: ['everyone'] }
  });
}

/* =========================
   🔁 LOOP (24/7)
========================= */
setInterval(async () => {
  const post = await fetchTrumpTruth();

  if (post) {
    console.log("NEW POST:", post.text);
    await sendTrump(post);
  }
}, 60000);

/* =========================
   🚀 READY
========================= */
client.once('ready', async () => {
  console.log(`Logged in as ${client.user.tag}`);

  cachedChannel = await client.channels.fetch(CHANNEL_ID);

  await cachedChannel.send("🚀 24/7 TRUMP BOT ONLINE");

  await initBrowser();

  const test = await fetchTrumpTruth();

  if (test) {
    console.log("✅ Scraper working");
    await sendTrump(test);
  } else {
    console.log("⚠️ No post detected yet");
  }
});

/* =========================
   🌐 KEEP ALIVE (RAILWAY)
========================= */
const app = express();
app.get('/', (req, res) => res.send('Bot running'));
app.listen(process.env.PORT || 3000);

/* =========================
   🔐 LOGIN
========================= */
client.login(TOKEN);
