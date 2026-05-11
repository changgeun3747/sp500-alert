import os
import sys
from dataclasses import dataclass

import requests
import yfinance as yf


TICKER = "^GSPC"
BASE_AMOUNT = 1_000_000

# 큰 하락률부터 판단해야 -12% 이하일 때 500만원이 정확히 선택된다.
INVESTMENT_RULES = [
    (-12.0, 5_000_000),
    (-7.0, 3_000_000),
    (-3.0, 2_000_000),
]


@dataclass
class MonthlyResult:
    previous_close: float
    latest_close: float
    monthly_return: float
    investment_amount: int


def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"환경변수 {name}가 설정되어 있지 않습니다.")
    return value


def fetch_monthly_closes() -> tuple[float, float]:
    """S&P500 월봉 데이터를 가져와 최근 2개 월 종가를 반환한다."""
    data = yf.download(
        TICKER,
        period="6mo",
        interval="1mo",
        auto_adjust=True,
        progress=False,
    )

    if data.empty or "Close" not in data:
        raise RuntimeError("S&P500 월별 종가 데이터를 가져오지 못했습니다.")

    close_data = data["Close"]
    if hasattr(close_data, "ndim") and close_data.ndim == 2:
        close_data = close_data.iloc[:, 0]

    closes = close_data.dropna()
    if len(closes) < 2:
        raise RuntimeError("월간 수익률 계산에 필요한 종가 데이터가 부족합니다.")

    return float(closes.iloc[-2]), float(closes.iloc[-1])


def decide_investment_amount(monthly_return: float) -> int:
    """월간 하락률에 따라 이번 달 투자금을 결정한다."""
    for trigger_rate, amount in INVESTMENT_RULES:
        if monthly_return <= trigger_rate:
            return amount
    return BASE_AMOUNT


def calculate_monthly_result() -> MonthlyResult:
    previous_close, latest_close = fetch_monthly_closes()
    monthly_return = (latest_close / previous_close - 1) * 100
    investment_amount = decide_investment_amount(monthly_return)

    return MonthlyResult(
        previous_close=previous_close,
        latest_close=latest_close,
        monthly_return=monthly_return,
        investment_amount=investment_amount,
    )


def format_won(amount: int) -> str:
    return f"{amount:,}원"


def build_alert_message(result: MonthlyResult) -> str:
    return (
        "S&P500 월간 투자 알림\n\n"
        f"이전 월 종가: {result.previous_close:,.2f}\n"
        f"최근 월 종가: {result.latest_close:,.2f}\n\n"
        f"월간 변화율: {result.monthly_return:.2f}%\n\n"
        "투자 기준:\n"
        "- 평상시: 1,000,000원\n"
        "- 월간 -3% 이하: 2,000,000원\n"
        "- 월간 -7% 이하: 3,000,000원\n"
        "- 월간 -12% 이하: 5,000,000원\n\n"
        f"이번 달 매수금액: {format_won(result.investment_amount)}"
    )


def build_error_message(error: Exception) -> str:
    return (
        "S&P500 월간 투자 알림 오류\n\n"
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


def main() -> int:
    try:
        result = calculate_monthly_result()
        send_telegram(build_alert_message(result))
        return 0
    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)

        # 텔레그램 설정 자체가 문제인 경우에는 콘솔 오류만 남긴다.
        try:
            send_telegram(build_error_message(error))
        except Exception as telegram_error:
            print(f"Failed to send Telegram error message: {telegram_error}", file=sys.stderr)

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
