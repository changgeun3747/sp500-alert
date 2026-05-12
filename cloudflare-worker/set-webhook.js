const token = process.env.TELEGRAM_BOT_TOKEN;
const workerUrl = process.env.WORKER_URL;

if (!token || !workerUrl) {
  console.error("TELEGRAM_BOT_TOKEN and WORKER_URL are required.");
  process.exit(1);
}

const webhookUrl = `${workerUrl.replace(/\/$/, "")}/telegram`;
const response = await fetch(`https://api.telegram.org/bot${token}/setWebhook`, {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    url: webhookUrl,
  }),
});

const payload = await response.json();
console.log(payload);
