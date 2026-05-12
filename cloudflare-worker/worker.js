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
const INVESTMENT_DAY = 25;

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

  try {
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
  } catch (error) {
    console.error("Telegram webhook error", error);
    await sendTelegram(env, buildErrorMessage(error));
    return new Response("error", { status: 500 });
  }
}

async function handleScheduledAlert(env) {
  try {
    const now = getKstNow();
    if (!(await isPayday(now))) {
      return;
    }

    await sendInvestmentAlert(env, "월급날 투자 알림");
  } catch (error) {
    console.error("Scheduled alert error", error);
    await sendTelegram(env, buildErrorMessage(error));
  }
}

async function sendInvestmentAlert(env, title) {
  const result = await calculateAlertResult();
  const message = buildAlertMessage(result, title);
  await sendTelegram(env, message);
}

async function calculateAlertResult() {
  const today = getKstNow();
  const [sp500, tiger] = await Promise.all(
    ASSETS.map((asset) => fetchAssetResult(asset, today, INVESTMENT_DAY)),
  );

  return {
    sp500,
    tiger,
    investmentAmount: decideInvestmentAmount(tiger.returnPct),
    payday: await getPayday(today),
    investmentDay: INVESTMENT_DAY,
    targetBaseDate: getPreviousInvestmentDate(today, INVESTMENT_DAY),
    generatedAt: today,
  };
}

async function fetchAssetResult(asset, currentDate, investmentDay) {
  const ticker = encodeURIComponent(asset.ticker);
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${ticker}?range=1y&interval=1d&events=history&includeAdjustedClose=true`;
  const response = await fetch(url, {
    headers: {
      "User-Agent": "sp500-alert-worker/1.0",
    },
  });

  if (!response.ok) {
    throw new Error(`${asset.name} 데이터 조회 실패. HTTP ${response.status}`);
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
    throw new Error(`${asset.name} 데이터 조회 실패: 종가 데이터가 부족합니다.`);
  }

  const currentDateText = formatKstDateObject(currentDate);
  const targetBaseDate = getPreviousInvestmentDate(currentDate, investmentDay);
  const base = getNearestPreviousTradingClose(points, targetBaseDate, `${asset.name} 이전 투자 기준 종가`);
  const latest = getNearestPreviousTradingClose(points, currentDateText, `${asset.name} 최근 종가`);
  const returnPct = calculateReturn(base.close, latest.close);

  return {
    asset,
    base,
    latest,
    returnPct,
  };
}

function getPreviousInvestmentDate(currentDate, investmentDay) {
  const currentMonthInvestmentDate = `${currentDate.year}-${pad(currentDate.month)}-${pad(investmentDay)}`;
  const currentDateText = formatKstDateObject(currentDate);

  if (currentDateText > currentMonthInvestmentDate) {
    return currentMonthInvestmentDate;
  }

  const previousMonth = addMonths(currentDate.year, currentDate.month, -1);
  return `${previousMonth.year}-${pad(previousMonth.month)}-${pad(investmentDay)}`;
}

function getNearestPreviousTradingClose(points, targetDate, label) {
  const candidates = points.filter((point) => point.date <= targetDate);
  if (!candidates.length) {
    throw new Error(`${label} 가격 데이터를 찾지 못했습니다. 기준일: ${targetDate}`);
  }
  return candidates.at(-1);
}

function calculateReturn(basePrice, currentPrice) {
  if (basePrice <= 0) {
    throw new Error("기준 종가가 0 이하라 수익률을 계산할 수 없습니다.");
  }
  return (currentPrice / basePrice - 1) * 100;
}

function decideInvestmentAmount(returnPct) {
  for (const [triggerRate, amount] of INVESTMENT_RULES) {
    if (returnPct <= triggerRate) {
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
  return formatKstDateObject(today) === await getPayday(today);
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
    throw new Error(`한국 공휴일 데이터 조회 실패. HTTP ${response.status}`);
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

function addMonths(year, month, monthsToAdd) {
  const date = new Date(Date.UTC(year, month - 1 + monthsToAdd, 1));
  return {
    year: date.getUTCFullYear(),
    month: date.getUTCMonth() + 1,
  };
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

function formatKstDateObject(dateObject) {
  return `${dateObject.year}-${pad(dateObject.month)}-${pad(dateObject.day)}`;
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
    `이전 투자 기준 종가: ${formatPrice(result, result.base.close)}`,
    `이전 투자 기준: ${result.base.date} ${result.asset.closeLabel}`,
    `최근 종가: ${formatPrice(result, result.latest.close)}`,
    `최근 기준: ${result.latest.date} ${result.asset.closeLabel}`,
    `투자 기준일 대비 변화율: ${result.returnPct.toFixed(2)}%`,
  ].join("\n");
}

function buildAlertMessage(result, title) {
  return [
    title,
    "",
    `생성 시각: ${result.generatedAt.text}`,
    `투자 기준일: 매월 ${result.investmentDay}일`,
    `월급 지급일: ${result.payday} 09:00 / 09:05 KST`,
    `실제 비교 기준일: ${result.tiger.base.date} ${result.tiger.asset.closeLabel}`,
    `최근 기준일: ${result.tiger.latest.date} ${result.tiger.asset.closeLabel}`,
    "",
    buildAssetSection(result.sp500),
    "",
    buildAssetSection(result.tiger),
    "",
    "투자금 판단 기준: TIGER 미국S&P500 투자 기준일 대비 변화율",
    "",
    "투자 기준:",
    "- 평상시: 1,000,000원",
    "- -3% 이하: 2,000,000원",
    "- -7% 이하: 3,000,000원",
    "- -12% 이하: 5,000,000원",
    "",
    `이번 달 매수금액: ${formatWon(result.investmentAmount)}`,
  ].join("\n");
}

function buildErrorMessage(error) {
  return ["투자 알림 오류", "", `오류 내용: ${error.message ?? error}`].join("\n");
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
