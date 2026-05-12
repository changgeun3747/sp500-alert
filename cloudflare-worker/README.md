# Cloudflare Workers 버전

이 버전은 GitHub Actions 대신 Cloudflare Workers에서 실행됩니다.

- 텔레그램에 `1`을 보내면 웹훅으로 거의 즉시 응답합니다.
- 월급 지급일 오전 9:00, 9:05 KST에 정기 알림을 보냅니다.
- 25일이 주말 또는 한국 공휴일이면 그 전 금요일로 앞당깁니다.
- S&P500 지수와 TIGER 미국S&P500 값을 모두 표시합니다.

## 1. Cloudflare 가입

https://dash.cloudflare.com 에 가입합니다.

## 2. 로컬에서 Wrangler 설치 및 로그인

```powershell
cd C:\Users\HOME\Documents\Codex\sp500-alert\cloudflare-worker
npm install
npx wrangler login
```

## 3. 텔레그램 값을 Cloudflare Secret으로 저장

```powershell
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_CHAT_ID
```

각 명령을 실행하면 값을 입력하라고 나옵니다.

## 4. Cloudflare에 배포

```powershell
npm run deploy
```

배포가 끝나면 아래처럼 Worker 주소가 나옵니다.

```txt
https://sp500-alert.<계정명>.workers.dev
```

## 5. 텔레그램 웹훅 연결

PowerShell에서 아래처럼 실행합니다.

```powershell
$env:TELEGRAM_BOT_TOKEN="봇토큰"
$env:WORKER_URL="https://sp500-alert.<계정명>.workers.dev"
npm run set-webhook
```

`ok: true`가 나오면 연결된 것입니다.

## 6. 테스트

### 수동 알림 테스트

브라우저에서 아래 주소를 엽니다.

```txt
https://sp500-alert.<계정명>.workers.dev/send-test
```

텔레그램으로 `수동 테스트 투자 알림`이 오면 성공입니다.

### 텔레그램 명령 테스트

텔레그램 봇에게 아래처럼 보냅니다.

```txt
1
```

`수시 투자 조회` 답장이 오면 성공입니다.
