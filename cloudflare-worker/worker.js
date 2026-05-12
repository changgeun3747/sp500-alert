const ASSETS = [
  {
    key: "sp500",
    name: "S&P500 지수",
    ticker: "^GSPC",
    timezone: "America/New_York",
    closeLabel: "16:00 ET 장마감 기준",
    suffix: "",
    decimals: 2,
  },
  {
    key: "tiger",
    name: "TIGER 미국S&P500",
    ticker: "360750.KS",
    timezone: "Asia/Seoul",
    closeLabel: "15:30 KST 장마감 기준",
    suffix: "원",
    decimals: 0,
  },
];

const INVESTMENT_RULES = [
  [-12, 5_000_000],
  [-7, 3_000_000],
  [-3, 2_000_000],
];

const BASE_AMOUNT = 1_000_000;
const PAYDAY_DAY = 25;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/telegram") {
      return handleTelegramWebhook(request, env);
    }

    if (url.pathname === "/send-test") {
      await sendInvestmentAlert(env, "수동 테스트 투자 알림");
      return new Response("sent");
    }

    return new Response("SP500 Alert Worker is running.");
  },

  async scheduled(event, env, ctx) {
    ctx.waitUntil(handleScheduledAlert(env));
  },
};

async function handleTelegramWebhook(request, env) {
  if (request.method !== "POST") {
    return new Response("method not allowed", { status: 405 });
  }

  const update = await request.json();
  const message = update.message;
  const chatId = String(message?.chat?.id ?? "");
  const text = String(message?.text ?? "").trim();

  if (chatId !== String(env.TELEGRAM_CHAT_ID)) {
    return new Response("ignored");
  }

  if (text === "1") {
    await sendInvestmentAlert(env, "수시 투자 조회");
  }

  return new Response("ok");
}

async function handleScheduledAlert(env) {
  const now = getKstNow();
  if (!isPayday(now)) {
    return;
  }

  await sendInvestmentAlert(env, "월급날 투자 알림");
}

async function sendInvestmentAlert(env, title) {
  const result = await calculateAlertResult();
  const message = buildAlertMessage(result, title);
  await sendTelegram(env, message);
}

async function calculateAlertResult() {
  const [sp500, tiger] = await Promise.all(ASSETS.map(fetchAssetResult));
  const tigerReturn = tiger.monthlyReturn;

  return {
    sp500,
    tiger,
    investmentAmount: decideInvestmentAmount(tigerReturn),
    payday: await getPayday(getKstNow()),
    generatedAt: getKstNow(),
  };
}

async function fetchAssetResult(asset) {
  const ticker = encodeURIComponent(asset.ticker);
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${ticker}?range=6mo&interval=1d&events=history&includeAdjustedClose=true`;
  const response = await fetch(url, {
    headers: {
      "User-Agent": "sp500-alert-worker/1.0",
    },
  });

  if (!response.ok) {
    throw new Error(`${asset.name} 데이터를 가져오지 못했습니다. HTTP ${response.status}`);
  }

  const payload = await response.json();
  const chart = payload.chart?.result?.[0];
  const timestamps = chart?.timestamp ?? [];
  const closes = chart?.indicators?.quote?.[0]?.close ?? [];
  const points = timestamps
    .map((timestamp, index) => ({
      date: formatDateInTimezone(new Date(timestamp * 1000), asset.timezone),
      close: closes[index],
    }))
    .filter((point) => point.close !== null && point.close !== undefined);

  if (points.length < 2) {
    throw new Error(`${asset.name} 종가 데이터가 부족합니다.`);
  }

  const latest = points.at(-1);
  const latestMonth = latest.date.slice(0, 7);
  const previousMonthPoints = points.filter((point) => point.date.slice(0, 7) < latestMonth);

  if (!previousMonthPoints.length) {
    throw new Error(`${asset.name} 전월 마지막 거래일 종가를 찾지 못했습니다.`);
  }

  const previous = previousMonthPoints.at(-1);
  const monthlyReturn = (latest.close / previous.close - 1) * 100;

  return {
    asset,
    previous,
    latest,
    monthlyReturn,
  };
}

function decideInvestmentAmount(monthlyReturn) {
  for (const [triggerRate, amount] of INVESTMENT_RULES) {
    if (monthlyReturn <= triggerRate) {
      return amount;
    }
  }
  return BASE_AMOUNT;
}

async function getPayday(today) {
  const scheduled = `${today.year}-${pad(today.month)}-${PAYDAY_DAY}`;
  if (await isBusinessDay(scheduled)) {
    return scheduled;
  }

  let candidate = previousFriday(scheduled);
  while (await isKoreanHoliday(candidate)) {
    candidate = previousFriday(candidate);
  }

  return candidate;
}

async function isPayday(today) {
  const todayText = `${today.year}-${pad(today.month)}-${pad(today.day)}`;
  return todayText === await getPayday(today);
}

async function isBusinessDay(dateText) {
  const date = new Date(`${dateText}T00:00:00+09:00`);
  const day = date.getUTCDay();
  const isWeekend = day === 0 || day === 6;
  return !isWeekend && !(await isKoreanHoliday(dateText));
}

async function isKoreanHoliday(dateText) {
  const year = dateText.slice(0, 4);
  const response = await fetch(`https://date.nager.at/api/v3/PublicHolidays/${year}/KR`);

  if (!response.ok) {
    throw new Error(`한국 공휴일 데이터를 가져오지 못했습니다. HTTP ${response.status}`);
  }

  const holidays = await response.json();
  return holidays.some((holiday) => holiday.date === dateText);
}

function previousFriday(dateText) {
  const date = new Date(`${dateText}T00:00:00+09:00`);
  const day = date.getUTCDay();
  const distance = day >= 5 ? day - 5 : day + 2;
  date.setUTCDate(date.getUTCDate() - distance);
  return formatDateInTimezone(date, "Asia/Seoul");
}

function getKstNow() {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date());

  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return {
    year: Number(value.year),
    month: Number(value.month),
    day: Number(value.day),
    hour: Number(value.hour),
    minute: Number(value.minute),
    text: `${value.year}-${value.month}-${value.day} ${value.hour}:${value.minute} KST`,
  };
}

function formatDateInTimezone(date, timezone) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(date);

  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}

function formatPrice(result, price) {
  return `${price.toLocaleString("en-US", {
    minimumFractionDigits: result.asset.decimals,
    maximumFractionDigits: result.asset.decimals,
  })}${result.asset.suffix}`;
}

function formatWon(amount) {
  return `${amount.toLocaleString("en-US")}원`;
}

function buildAssetSection(result) {
  return [
    `[${result.asset.name}]`,
    `이전 월 종가: ${formatPrice(result, result.previous.close)}`,
    `이전 월 기준: ${result.previous.date} ${result.asset.closeLabel}`,
    `최근 종가: ${formatPrice(result, result.latest.close)}`,
    `최근 기준: ${result.latest.date} ${result.asset.closeLabel}`,
    `월간 변화율: ${result.monthlyReturn.toFixed(2)}%`,
  ].join("\n");
}

function buildAlertMessage(result, title) {
  return [
    title,
    "",
    `생성 시각: ${result.generatedAt.text}`,
    `월급 지급일: ${result.payday} 09:00 / 09:05 KST`,
    "",
    buildAssetSection(result.sp500),
    "",
    buildAssetSection(result.tiger),
    "",
    "투자금 판단 기준: TIGER 미국S&P500 월간 변화율",
    "투자 기준:",
    "- 평상시: 1,000,000원",
    "- 월간 -3% 이하: 2,000,000원",
    "- 월간 -7% 이하: 3,000,000원",
    "- 월간 -12% 이하: 5,000,000원",
    "",
    `이번 달 매수금액: ${formatWon(result.investmentAmount)}`,
  ].join("\n");
}

async function sendTelegram(env, text) {
  const response = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      chat_id: env.TELEGRAM_CHAT_ID,
      text,
    }),
  });

  if (!response.ok) {
    throw new Error(`텔레그램 전송 실패. HTTP ${response.status}`);
  }
}

function pad(value) {
  return String(value).padStart(2, "0");
}
