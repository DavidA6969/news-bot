const { Client, GatewayIntentBits, EmbedBuilder } = require('discord.js');
const axios = require('axios');
const cheerio = require('cheerio');
const express = require('express');

// 🔐 ENV (for hosting)
const TOKEN = process.env.TOKEN;
const CHANNEL_ID = process.env.CHANNEL_ID;

const client = new Client({
  intents: [GatewayIntentBits.Guilds]
});

let sentEvents = new Set();

// 💥 CRASH PROTECTION
process.on('unhandledRejection', (err) => {
  console.log('Unhandled Rejection:', err);
});

process.on('uncaughtException', (err) => {
  console.log('Uncaught Exception:', err);
});

// 🔴 FETCH NEWS (SAFE VERSION)
async function fetchNews() {
  try {
    const { data } = await axios.get('https://www.forexfactory.com/calendar', {
      timeout: 10000
    });

    if (!data) return [];

    const $ = cheerio.load(data);
    let events = [];

    $('.calendar__row').each((i, el) => {
      try {
        const impact = $(el).find('.impact').attr('title');
        const currency = $(el).find('.calendar__currency').text().trim();

        if (impact && impact.includes('High') && currency === 'USD') {
          const time = $(el).find('.calendar__time').text().trim();
          const title = $(el).find('.calendar__event').text().trim();

          const id = `${time}-${currency}-${title}`;

          if (!sentEvents.has(id)) {
            sentEvents.add(id);
            events.push({ time, currency, title });
          }
        }
      } catch (e) {}
    });

    return events;
  } catch (err) {
    console.log('Fetch error:', err.message);
    return [];
  }
}

// 📢 SEND EMBED (SAFE)
async function sendEmbed(event) {
  try {
    if (!client.isReady()) return;

    const channel = await client.channels.fetch(CHANNEL_ID);
    if (!channel) return;

    const embed = new EmbedBuilder()
      .setColor('#ff0000')
      .setTitle('🚨 HIGH IMPACT NEWS')
      .addFields(
        { name: 'Currency', value: event.currency || 'N/A', inline: true },
        { name: 'Time', value: event.time || 'N/A', inline: true },
        { name: 'Event', value: event.title || 'N/A' }
      )
      .setFooter({ text: 'TX_Trades Market Feed' })
      .setTimestamp();

    await channel.send({
      content: "@everyone 🚨 HIGH IMPACT NEWS!",
      embeds: [embed],
      allowedMentions: { parse: ['everyone'] }
    });

  } catch (err) {
    console.log('Send error:', err.message);
  }
}

// ⏱️ LOOP (SAFE)
setInterval(async () => {
  try {
    const news = await fetchNews();

    for (const event of news) {
      await sendEmbed(event);
    }
  } catch (err) {
    console.log('Loop error:', err.message);
  }
}, 180000);

// 🚀 BOT READY
client.once('ready', () => {
  console.log(`Logged in as ${client.user.tag}`);
});

// 🌐 KEEP ALIVE SERVER (Railway fix)
const app = express();
const PORT = process.env.PORT || 3000;

app.get('/', (req, res) => {
  res.send('Bot is alive');
});

app.listen(PORT, () => {
  console.log('Web server running on port', PORT);
});

// 🔐 LOGIN
client.login(TOKEN);
