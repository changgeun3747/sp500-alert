import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays
import requests
import yfinance as yf


BASE_AMOUNT = 1_000_000
PAYDAY_DAY = 25
KST = ZoneInfo("Asia/Seoul")

# 큰 하락률부터 판단해야 -12% 이하일 때 500만원이 정확히 선택된다.
INVESTMENT_RULES = [
    (-12.0, 5_000_000),
    (-7.0, 3_000_000),
    (-3.0, 2_000_000),
]


@dataclass(frozen=True)
class Asset:
    name: str
    ticker: str
    close_time: str
    price_suffix: str
    decimals: int


@dataclass(frozen=True)
class PricePoint:
    close_date: date
    close_price: float


@dataclass(frozen=True)
class AssetResult:
    asset: Asset
    previous: PricePoint
    latest: PricePoint
    monthly_return: float


@dataclass(frozen=True)
class AlertResult:
    sp500: AssetResult
    tiger: AssetResult
    investment_amount: int
    payday: date
    generated_at: datetime


ASSETS = [
    Asset("S&P500 지수", "^GSPC", "16:00 ET 장마감 기준", "", 2),
    Asset("TIGER 미국S&P500", "360750.KS", "15:30 KST 장마감 기준", "원", 0),
]


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"환경변수 {name}가 설정되어 있지 않습니다.")
    return value


def is_korean_holiday(day: date) -> bool:
    korean_holidays = holidays.country_holidays("KR", years=[day.year])
    return day in korean_holidays


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and not is_korean_holiday(day)


def previous_non_holiday_friday(day: date) -> date:
    friday = day - timedelta(days=(day.weekday() - 4) % 7)
    if friday >= day:
        friday -= timedelta(days=7)

    while is_korean_holiday(friday):
        friday -= timedelta(days=7)

    return friday


def get_payday(today: date) -> date:
    scheduled_day = date(today.year, today.month, PAYDAY_DAY)
    if is_business_day(scheduled_day):
        return scheduled_day

    return previous_non_holiday_friday(scheduled_day)


def should_send_today(today: date) -> bool:
    return today == get_payday(today)


def get_close_series(data, asset: Asset):
    if data.empty or "Close" not in data:
        raise RuntimeError(f"{asset.name}({asset.ticker}) 종가 데이터를 가져오지 못했습니다.")

    close_data = data["Close"]
    if hasattr(close_data, "ndim") and close_data.ndim == 2:
        close_data = close_data.iloc[:, 0]

    closes = close_data.dropna()
    if len(closes) < 2:
        raise RuntimeError(f"{asset.name} 수익률 계산에 필요한 종가 데이터가 부족합니다.")

    return closes


def fetch_asset_result(asset: Asset) -> AssetResult:
    data = yf.download(
        asset.ticker,
        period="6mo",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    closes = get_close_series(data, asset)

    latest_timestamp = closes.index[-1]
    latest_date = latest_timestamp.date()
    latest = PricePoint(latest_date, float(closes.iloc[-1]))

    first_day_of_latest_month = latest_date.replace(day=1)
    previous_month_closes = closes[closes.index.date < first_day_of_latest_month]
    if previous_month_closes.empty:
        raise RuntimeError(f"{asset.name} 전월 마지막 거래일 종가를 찾지 못했습니다.")

    previous_timestamp = previous_month_closes.index[-1]
    previous = PricePoint(previous_timestamp.date(), float(previous_month_closes.iloc[-1]))
    monthly_return = (latest.close_price / previous.close_price - 1) * 100

    return AssetResult(
        asset=asset,
        previous=previous,
        latest=latest,
        monthly_return=monthly_return,
    )


def decide_investment_amount(monthly_return: float) -> int:
    for trigger_rate, amount in INVESTMENT_RULES:
        if monthly_return <= trigger_rate:
            return amount
    return BASE_AMOUNT


def calculate_alert_result(today: date) -> AlertResult:
    sp500 = fetch_asset_result(ASSETS[0])
    tiger = fetch_asset_result(ASSETS[1])

    return AlertResult(
        sp500=sp500,
        tiger=tiger,
        investment_amount=decide_investment_amount(tiger.monthly_return),
        payday=get_payday(today),
        generated_at=datetime.now(KST),
    )


def format_won(amount: int) -> str:
    return f"{amount:,}원"


def format_price(result: AssetResult, price: float) -> str:
    number = f"{price:,.{result.asset.decimals}f}"
    return f"{number}{result.asset.price_suffix}"


def format_close_time(result: AssetResult, close_date: date) -> str:
    return f"{close_date:%Y-%m-%d} {result.asset.close_time}"


def build_asset_section(result: AssetResult) -> str:
    return (
        f"[{result.asset.name}]\n"
        f"이전 월 종가: {format_price(result, result.previous.close_price)}\n"
        f"이전 월 기준: {format_close_time(result, result.previous.close_date)}\n"
        f"최근 종가: {format_price(result, result.latest.close_price)}\n"
        f"최근 기준: {format_close_time(result, result.latest.close_date)}\n"
        f"월간 변화율: {result.monthly_return:.2f}%"
    )


def build_alert_message(result: AlertResult, title: str = "월급날 투자 알림") -> str:
    return (
        f"{title}\n\n"
        f"생성 시각: {result.generated_at:%Y-%m-%d %H:%M KST}\n"
        f"월급 지급일: {result.payday:%Y-%m-%d} 09:00 / 09:05 KST\n\n"
        f"{build_asset_section(result.sp500)}\n\n"
        f"{build_asset_section(result.tiger)}\n\n"
        "투자금 판단 기준: TIGER 미국S&P500 월간 변화율\n"
        "투자 기준:\n"
        "- 평상시: 1,000,000원\n"
        "- 월간 -3% 이하: 2,000,000원\n"
        "- 월간 -7% 이하: 3,000,000원\n"
        "- 월간 -12% 이하: 5,000,000원\n\n"
        f"이번 달 매수금액: {format_won(result.investment_amount)}"
    )


def build_skip_message(today: date) -> str:
    payday = get_payday(today)
    return f"오늘({today:%Y-%m-%d})은 알림일이 아닙니다. 이번 달 알림일: {payday:%Y-%m-%d}"


def build_error_message(error: Exception) -> str:
    return (
        "투자 알림 오류\n\n"
        f"오류 내용: {error}"
    )


def telegram_url(method: str) -> str:
    bot_token = get_required_env("TELEGRAM_BOT_TOKEN")
    return f"https://api.telegram.org/bot{bot_token}/{method}"


def send_telegram(message: str, chat_id: str | None = None) -> None:
    response = requests.post(
        telegram_url("sendMessage"),
        data={
            "chat_id": chat_id or get_required_env("TELEGRAM_CHAT_ID"),
            "text": message,
        },
        timeout=15,
    )
    response.raise_for_status()


def get_telegram_updates() -> list[dict]:
    response = requests.get(
        telegram_url("getUpdates"),
        params={"timeout": 0, "allowed_updates": '["message"]'},
        timeout=15,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"텔레그램 업데이트 조회 실패: {payload}")
    return payload.get("result", [])


def acknowledge_telegram_updates(last_update_id: int) -> None:
    response = requests.get(
        telegram_url("getUpdates"),
        params={"offset": last_update_id + 1, "timeout": 0},
        timeout=15,
    )
    response.raise_for_status()


def poll_telegram_commands(today: date) -> int:
    updates = get_telegram_updates()
    if not updates:
        print("No Telegram updates.")
        return 0

    configured_chat_id = str(get_required_env("TELEGRAM_CHAT_ID"))
    last_update_id = max(update["update_id"] for update in updates)
    handled_count = 0

    for update in updates:
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        text = str(message.get("text") or "").strip()
        chat_id = str(chat.get("id") or "")

        if chat_id == configured_chat_id and text == "1":
            result = calculate_alert_result(today)
            send_telegram(build_alert_message(result, title="수시 투자 조회"), chat_id=chat_id)
            handled_count += 1

    acknowledge_telegram_updates(last_update_id)
    print(f"Handled {handled_count} Telegram command(s).")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="S&P500/TIGER 미국S&P500 투자 알림")
    parser.add_argument(
        "--force",
        action="store_true",
        help="월급 지급일 여부와 상관없이 알림을 보냅니다.",
    )
    parser.add_argument(
        "--poll-telegram",
        action="store_true",
        help="텔레그램에서 '1' 메시지가 왔는지 확인하고 답장합니다.",
    )
    parser.add_argument(
        "--date",
        help="테스트용 실행 날짜입니다. 예: 2026-05-25",
    )
    return parser.parse_args()


def resolve_today(date_text: str | None) -> date:
    if date_text:
        return datetime.strptime(date_text, "%Y-%m-%d").date()
    return datetime.now(KST).date()


def main() -> int:
    args = parse_args()
    today = resolve_today(args.date)

    try:
        if args.poll_telegram:
            return poll_telegram_commands(today)

        if not args.force and not should_send_today(today):
            print(build_skip_message(today))
            return 0

        result = calculate_alert_result(today)
        send_telegram(build_alert_message(result))
        return 0
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)

        try:
            send_telegram(build_error_message(error))
        except Exception as telegram_error:
            print(f"Failed to send Telegram error message: {telegram_error}", file=sys.stderr)

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
