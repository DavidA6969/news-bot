const { Client, GatewayIntentBits, EmbedBuilder } = require('discord.js');
const axios = require('axios');
const cheerio = require('cheerio');
const express = require('express');

// 🔐 ENV
const TOKEN = process.env.TOKEN;
const CHANNEL_ID = process.env.CHANNEL_ID;

// 🌐 SOURCES
const NEWS_URL = "https://www.reuters.com/markets/";
const TRUMP_X_RSS = "https://nitter.net/realDonaldTrump/rss";

const client = new Client({
  intents: [GatewayIntentBits.Guilds]
});

let sentEvents = new Set();
let seenHeadlines = new Set();
let lastTrumpPost = "";
let cachedChannel;

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

  const bullish = ["rate cut", "stimulus", "growth", "soft landing", "cooling inflation", "lower inflation"];
  const bearish = ["rate hike", "inflation rises", "war", "conflict", "recession", "crisis", "hawkish", "oil spike", "tariffs", "china tensions"];

  let bull = 0;
  let bear = 0;

  bullish.forEach(k => { if (t.includes(k)) bull += 2; });
  bearish.forEach(k => { if (t.includes(k)) bear += 2; });

  if (bull > bear) return "BULLISH 📈";
  if (bear > bull) return "BEARISH 📉";
  return "NEUTRAL ⚖️";
}

/* =========================
   🕒 NY SESSION FILTER
========================= */
function isNYSession() {
  const now = new Date();
  const ny = new Date(now.toLocaleString("en-US", { timeZone: "America/New_York" }));
  const minutes = ny.getHours() * 60 + ny.getMinutes();
  return minutes >= (9 * 60 + 30) && minutes <= (17 * 60);
}

/* =========================
   🔴 FOREX FACTORY
========================= */
async function fetchNews() {
  try {
    const { data } = await axios.get('https://www.forexfactory.com/calendar', { timeout: 10000 });
    const $ = cheerio.load(data);
    let events = [];

    $('.calendar__row').each((i, el) => {
      const impact = $(el).find('.impact').attr('title');
      const currency = $(el).find('.calendar__currency').text().trim();

      if (impact?.includes('High') && currency === 'USD') {
        const time = $(el).find('.calendar__time').text().trim();
        const title = $(el).find('.calendar__event').text().trim();

        const id = `${time}-${title}`;

        if (!sentEvents.has(id)) {
          sentEvents.add(id);
          events.push({ time, title });
        }
      }
    });

    return events;

  } catch (err) {
    console.log('FF error:', err.message);
    return [];
  }
}

/* =========================
   📰 REUTERS
========================= */
async function fetchBreakingNews() {
  try {
    const { data } = await axios.get(NEWS_URL, { timeout: 10000 });
    const $ = cheerio.load(data);

    let news = [];

    $("a[data-testid='Heading']").each((i, el) => {
      const title = $(el).text().trim();
      if (!title) return;

      const keywords = ["fed","inflation","cpi","powell","war","oil","china","recession","economy","jobs","bank","crisis"];

      const important = keywords.some(k => title.toLowerCase().includes(k));

      if (important && !seenHeadlines.has(title)) {
        seenHeadlines.add(title);
        news.push(title);
      }
    });

    return news;

  } catch (err) {
    console.log("Reuters error:", err.message);
    return [];
  }
}

/* =========================
   🐦 TRUMP (FIXED RSS)
========================= */
async function fetchTrumpX() {
  try {
    const { data } = await axios.get(TRUMP_X_RSS, { timeout: 10000 });
    const $ = cheerio.load(data, { xmlMode: true });

    const tweet = $("item").first().find("title").text().trim();

    if (tweet && tweet !== lastTrumpPost) {
      lastTrumpPost = tweet;
      return tweet;
    }

    return null;

  } catch (err) {
    console.log("Trump RSS error:", err.message);
    return null;
  }
}

/* =========================
   📢 SEND FUNCTIONS
========================= */
async function sendFF(event) {
  const bias = getMarketBias(event.title);

  const embed = new EmbedBuilder()
    .setColor('#ff0000')
    .setTitle('🚨 HIGH IMPACT NEWS')
    .addFields(
      { name: 'Event', value: event.title },
      { name: 'Time', value: event.time, inline: true },
      { name: 'Bias', value: bias, inline: true }
    )
    .setTimestamp();

  await cachedChannel.send({
    content: "@everyone 🚨 HIGH IMPACT NEWS",
    embeds: [embed],
    allowedMentions: { parse: ['everyone'] }
  });
}

async function sendBreaking(title) {
  const bias = getMarketBias(title);

  const embed = new EmbedBuilder()
    .setColor('#00bcd4')
    .setTitle('📰 BREAKING NEWS')
    .addFields(
      { name: 'Headline', value: title },
      { name: 'Bias', value: bias }
    )
    .setTimestamp();

  await cachedChannel.send({
    content: "@everyone 📰 MARKET NEWS",
    embeds: [embed],
    allowedMentions: { parse: ['everyone'] }
  });
}

async function sendTrump(content) {
  const bias = getMarketBias(content);

  const embed = new EmbedBuilder()
    .setColor('#f1c40f')
    .setTitle('🐦 TRUMP ALERT')
    .addFields(
      { name: 'Post', value: content.slice(0, 1000) },
      { name: 'Bias', value: bias }
    )
    .setTimestamp();

  await cachedChannel.send({
    content: "@everyone 🐦 TRUMP POST",
    embeds: [embed],
    allowedMentions: { parse: ['everyone'] }
  });
}

/* =========================
   ⏱️ LOOPS
========================= */

// ForexFactory
setInterval(async () => {
  const news = await fetchNews();
  for (const e of news) await sendFF(e);
}, 180000);

// Reuters
setInterval(async () => {
  const news = await fetchBreakingNews();
  for (const n of news) await sendBreaking(n);
}, 60000);

// Trump X
setInterval(async () => {
  if (!isNYSession()) return;

  const tweet = await fetchTrumpX();
  if (tweet) await sendTrump(tweet);

}, 60000);

/* =========================
   🚀 READY TEST
========================= */
client.once('ready', async () => {
  console.log(`Logged in as ${client.user.tag}`);
  cachedChannel = await client.channels.fetch(CHANNEL_ID);

  await cachedChannel.send("🚀 BOT ONLINE TEST");

  const testTweet = await fetchTrumpX();

  if (testTweet) {
    console.log("✅ TRUMP RSS WORKING");
    await sendTrump(testTweet);
  } else {
    console.log("❌ TRUMP RSS FAILED");
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
