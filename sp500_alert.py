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
INVESTMENT_DAY = 25
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
    base: PricePoint
    latest: PricePoint
    return_pct: float


@dataclass(frozen=True)
class AlertResult:
    sp500: AssetResult
    tiger: AssetResult
    investment_amount: int
    payday: date
    investment_day: int
    target_base_date: date
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


def add_months(day: date, months: int) -> date:
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(day.day, 28))


def get_previous_investment_date(current_date: date, investment_day: int) -> date:
    """실행일 기준 직전 투자 기준일을 반환한다.

    투자 기준일 당일에는 아직 이번 달 종가가 확정되지 않았다고 보고,
    전월 투자 기준일을 비교 기준으로 사용한다.
    """
    current_month_investment_date = date(current_date.year, current_date.month, investment_day)
    if current_date > current_month_investment_date:
        return current_month_investment_date

    previous_month = add_months(current_month_investment_date, -1)
    return date(previous_month.year, previous_month.month, investment_day)


def calculate_return(base_price: float, current_price: float) -> float:
    if base_price <= 0:
        raise RuntimeError("기준 종가가 0 이하라 수익률을 계산할 수 없습니다.")
    return (current_price / base_price - 1) * 100


def get_close_series(data, asset: Asset):
    if data.empty or "Close" not in data:
        raise RuntimeError(f"{asset.name}({asset.ticker}) 데이터 조회 실패: 종가 데이터가 없습니다.")

    close_data = data["Close"]
    if hasattr(close_data, "ndim") and close_data.ndim == 2:
        close_data = close_data.iloc[:, 0]

    closes = close_data.dropna()
    if len(closes) < 2:
        raise RuntimeError(f"{asset.name} 데이터 조회 실패: 종가 데이터가 부족합니다.")

    return closes


def get_nearest_previous_trading_close(closes, target_date: date, label: str) -> PricePoint:
    """target_date 당일 또는 그 이전의 가장 가까운 거래일 종가를 찾는다."""
    filtered = closes[closes.index.date <= target_date]
    if filtered.empty:
        raise RuntimeError(f"{label} 가격 데이터를 찾지 못했습니다. 기준일: {target_date:%Y-%m-%d}")

    timestamp = filtered.index[-1]
    return PricePoint(timestamp.date(), float(filtered.iloc[-1]))


def fetch_asset_result(asset: Asset, current_date: date, investment_day: int) -> AssetResult:
    data = yf.download(
        asset.ticker,
        period="1y",
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    closes = get_close_series(data, asset)

    target_base_date = get_previous_investment_date(current_date, investment_day)
    base = get_nearest_previous_trading_close(
        closes,
        target_base_date,
        f"{asset.name} 이전 투자 기준 종가",
    )
    latest = get_nearest_previous_trading_close(
        closes,
        current_date,
        f"{asset.name} 최근 종가",
    )
    return_pct = calculate_return(base.close_price, latest.close_price)

    return AssetResult(
        asset=asset,
        base=base,
        latest=latest,
        return_pct=return_pct,
    )


def decide_investment_amount(return_pct: float) -> int:
    for trigger_rate, amount in INVESTMENT_RULES:
        if return_pct <= trigger_rate:
            return amount
    return BASE_AMOUNT


def calculate_alert_result(today: date) -> AlertResult:
    sp500 = fetch_asset_result(ASSETS[0], today, INVESTMENT_DAY)
    tiger = fetch_asset_result(ASSETS[1], today, INVESTMENT_DAY)

    return AlertResult(
        sp500=sp500,
        tiger=tiger,
        investment_amount=decide_investment_amount(tiger.return_pct),
        payday=get_payday(today),
        investment_day=INVESTMENT_DAY,
        target_base_date=get_previous_investment_date(today, INVESTMENT_DAY),
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
        f"이전 투자 기준 종가: {format_price(result, result.base.close_price)}\n"
        f"이전 투자 기준: {format_close_time(result, result.base.close_date)}\n"
        f"최근 종가: {format_price(result, result.latest.close_price)}\n"
        f"최근 기준: {format_close_time(result, result.latest.close_date)}\n"
        f"투자 기준일 대비 변화율: {result.return_pct:.2f}%"
    )


def build_alert_message(result: AlertResult, title: str = "월급날 투자 알림") -> str:
    return (
        f"{title}\n\n"
        f"생성 시각: {result.generated_at:%Y-%m-%d %H:%M KST}\n"
        f"투자 기준일: 매월 {result.investment_day}일\n"
        f"월급 지급일: {result.payday:%Y-%m-%d} 09:00 / 09:05 KST\n"
        f"실제 비교 기준일: {format_close_time(result.tiger, result.tiger.base.close_date)}\n"
        f"최근 기준일: {format_close_time(result.tiger, result.tiger.latest.close_date)}\n\n"
        f"{build_asset_section(result.sp500)}\n\n"
        f"{build_asset_section(result.tiger)}\n\n"
        "투자금 판단 기준: TIGER 미국S&P500 투자 기준일 대비 변화율\n\n"
        "투자 기준:\n"
        "- 평상시: 1,000,000원\n"
        "- -3% 이하: 2,000,000원\n"
        "- -7% 이하: 3,000,000원\n"
        "- -12% 이하: 5,000,000원\n\n"
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
