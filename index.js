const { Client, GatewayIntentBits, EmbedBuilder } = require('discord.js');
const axios = require('axios');
const cheerio = require('cheerio');
const express = require('express');

const TOKEN = 'MTQ5MzQxMTU1MTQ3NzEwODc0Ng.Gz8i5A.4aZByEmllctgAFtZ8QwQ6bn7FIOPz0ZwVl5U6E';
const CHANNEL_ID = '1493399971079393320';

const client = new Client({
  intents: [GatewayIntentBits.Guilds]
});

let sentEvents = new Set();

// 🔴 FETCH NEWS
async function fetchNews() {
  try {
    const { data } = await axios.get('https://www.forexfactory.com/calendar');
    const $ = cheerio.load(data);

    let events = [];

    $('.calendar__row').each((i, el) => {
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
    });

    return events;
  } catch (err) {
    console.error(err);
    return [];
  }
}

// 📢 SEND EMBED
async function sendEmbed(event) {
  const channel = await client.channels.fetch(CHANNEL_ID);

  const embed = new EmbedBuilder()
    .setColor('#ff0000')
    .setTitle('🚨 HIGH IMPACT NEWS')
    .addFields(
      { name: 'Currency', value: event.currency || 'N/A', inline: true },
      { name: 'Time', value: event.time || 'N/A', inline: true },
      { name: 'Event', value: event.title || 'N/A' }
    )
    .setFooter({ text: 'MindForge Market Feed' })
    .setTimestamp();

  await channel.send({ embeds: [embed] });
}

// ⏱️ LOOP (every 3 min)
setInterval(async () => {
  const news = await fetchNews();

  for (let event of news) {
    await sendEmbed(event);
  }
}, 180000);

// 🚀 START BOT
client.once('clientReady', () => {
  console.log(`Running as ${client.user.tag}`);
});

// 🌐 KEEP ALIVE SERVER
const app = express();
app.get('/', (req, res) => res.send('Bot is alive'));
app.listen(3000);

// 🔐 LOGIN (MUST BE OUTSIDE EVERYTHING)
client.login(TOKEN);
