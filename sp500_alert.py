import argparse
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import holidays
import requests
import yfinance as yf


TICKER = "360750.KS"
ASSET_NAME = "TIGER 미국S&P500"
BASE_AMOUNT = 1_000_000
PAYDAY_DAY = 25
KST = ZoneInfo("Asia/Seoul")

# 큰 하락률부터 판단해야 -12% 이하일 때 500만원이 정확히 선택된다.
INVESTMENT_RULES = [
    (-12.0, 5_000_000),
    (-7.0, 3_000_000),
    (-3.0, 2_000_000),
]


@dataclass
class PricePoint:
    close_date: date
    close_price: float


@dataclass
class MonthlyResult:
    previous: PricePoint
    latest: PricePoint
    monthly_return: float
    investment_amount: int
    payday: date


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


def get_close_series(data):
    if data.empty or "Close" not in data:
        raise RuntimeError(f"{ASSET_NAME}({TICKER}) 종가 데이터를 가져오지 못했습니다.")

    close_data = data["Close"]
    if hasattr(close_data, "ndim") and close_data.ndim == 2:
        close_data = close_data.iloc[:, 0]

    closes = close_data.dropna()
    if len(closes) < 2:
        raise RuntimeError("수익률 계산에 필요한 종가 데이터가 부족합니다.")

    return closes


def fetch_price_points() -> tuple[PricePoint, PricePoint]:
    data = yf.download(
        TICKER,
        period="6mo",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    closes = get_close_series(data)

    latest_timestamp = closes.index[-1]
    latest_date = latest_timestamp.date()
    latest = PricePoint(latest_date, float(closes.iloc[-1]))

    first_day_of_latest_month = latest_date.replace(day=1)
    previous_month_closes = closes[closes.index.date < first_day_of_latest_month]
    if previous_month_closes.empty:
        raise RuntimeError("전월 마지막 거래일 종가를 찾지 못했습니다.")

    previous_timestamp = previous_month_closes.index[-1]
    previous = PricePoint(previous_timestamp.date(), float(previous_month_closes.iloc[-1]))

    return previous, latest


def decide_investment_amount(monthly_return: float) -> int:
    for trigger_rate, amount in INVESTMENT_RULES:
        if monthly_return <= trigger_rate:
            return amount
    return BASE_AMOUNT


def calculate_monthly_result(today: date) -> MonthlyResult:
    previous, latest = fetch_price_points()
    monthly_return = (latest.close_price / previous.close_price - 1) * 100
    investment_amount = decide_investment_amount(monthly_return)

    return MonthlyResult(
        previous=previous,
        latest=latest,
        monthly_return=monthly_return,
        investment_amount=investment_amount,
        payday=get_payday(today),
    )


def format_won(amount: int) -> str:
    return f"{amount:,}원"


def format_price(price: float) -> str:
    return f"{price:,.0f}원"


def format_close_time(close_date: date) -> str:
    return f"{close_date:%Y-%m-%d} 15:30 KST 기준"


def build_alert_message(result: MonthlyResult) -> str:
    return (
        f"{ASSET_NAME} 월급날 투자 알림\n\n"
        f"월급 지급일: {result.payday:%Y-%m-%d} 09:00 KST\n\n"
        f"이전 월 종가: {format_price(result.previous.close_price)}\n"
        f"기준 시각: {format_close_time(result.previous.close_date)}\n\n"
        f"최근 종가: {format_price(result.latest.close_price)}\n"
        f"기준 시각: {format_close_time(result.latest.close_date)}\n\n"
        f"월간 변화율: {result.monthly_return:.2f}%\n\n"
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
        f"{ASSET_NAME} 월급날 투자 알림 오류\n\n"
        f"오류 내용: {error}"
    )


def send_telegram(message: str) -> None:
    bot_token = get_required_env("TELEGRAM_BOT_TOKEN")
    chat_id = get_required_env("TELEGRAM_CHAT_ID")

    response = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data={
            "chat_id": chat_id,
            "text": message,
        },
        timeout=15,
    )
    response.raise_for_status()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"{ASSET_NAME} 월급날 투자 알림")
    parser.add_argument(
        "--force",
        action="store_true",
        help="월급 지급일 여부와 상관없이 알림을 보냅니다.",
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

    if not args.force and not should_send_today(today):
        print(build_skip_message(today))
        return 0

    try:
        result = calculate_monthly_result(today)
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
