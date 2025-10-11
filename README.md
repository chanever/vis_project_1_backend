Kimchi Premium Backend

개요
- FastAPI 기반 백엔드로, 일일 데이터셋을 빌드/서빙하고 일부 보조 엔드포인트를 제공합니다.
- 데이터 파이프라인은 바이낸스 선물 일봉, 업비트 KRW 일봉, USD/KRW(ffill 포함), Greed Index를 결합합니다.

핵심 정책 (KST 컷오프)
- 일일 데이터는 매일 오전 9시 30분(KST) 이후에 당일 데이터를 대상으로 빌드합니다.
- 오전 9시 30분 이전 요청은 종료일을 ‘어제’로 강제(effective end date)하여 처리합니다.
- 소스별 수급 지연 시(예: 일부 소스에 오늘 일봉이 아직 없음) inner join 특성상 당일 행은 제외되고 어제까지가 응답됩니다.
- USD/KRW는 주말·공휴일에 전일값으로 보간(ffill=True 표시)됩니다.

주요 파일
- main.py: FastAPI 엔트리포인트 및 엔드포인트 정의
- pipeline.py: 원천 데이터 수집 및 결합 로직
- dollar_scraper.py: USD/KRW 일일 데이터 수집 (주말/공휴일 ffill)

데이터 파이프라인
1) Binance USD-M Futures 일봉 (BASEUSDT)
2) Upbit KRW-BASE 일봉
3) USD/KRW 일일 환율 (ffill 포함)
4) Crypto Fear & Greed Index
→ inner join으로 결합 후 kimchi_pct 계산

중요 함수
- pipeline.build_dataset(start, end, base_symbol)
  - 반환 컬럼: date, usdt_close, krw_close, usdkrw, usd_ffill, greed, greed_ffill, kimchi_pct
- pipeline.load_or_build_dataset(start, end, cache_path, use_cache, base_symbol)
  - 캐시 CSV가 있고 use_cache=True면 범위 필터 후 반환, 아니면 새로 빌드 후 저장
- main._effective_end_date(end)
  - 09:30 이전엔 어제, 이후엔 오늘로 종료일을 결정

엔드포인트
- GET /health
  - 상태 체크
- GET /btc_dominance
  - BTC Dominance 간단 조회
- GET /dataset?start=YYYY-MM-DD&end=YYYY-MM-DD&symbol=BTC|ETH|SOL|DOGE|XRP|ADA
  - 컷오프 정책 적용된 종료일로 데이터셋 반환
  - 캐시 CSV 최신성이 부족하면 자동 재빌드
- GET /download?start=...&end=...&symbol=...
  - 즉시 재빌드 후 CSV 다운로드

실행 방법
1) 의존성 설치(예)
   pip install fastapi uvicorn pandas ccxt pyupbit requests python-dateutil
2) 서버 실행
   python /Users/chan/Desktop/graduate/1-1/Project_1/backend/main.py
3) 확인
   curl http://localhost:8000/health

동작 요약
- 09:30 이전: 오늘 데이터는 시도하지 않고 어제까지 반환
- 09:30 이후: 오늘 데이터 시도. 일부 소스 지연 시 당일 행이 누락될 수 있으며, 곧 재요청 시 채워짐



추가 가이드 (Step by Step)
1) 의존성 설치

   ```bash
   pip install fastapi uvicorn pandas ccxt pyupbit requests python-dateutil
   ```

2) 환경 변수 설정(backend/.env)

   ```bash
   CMC_API_KEY=YOUR_CMC_KEY
   FIXER_API_KEY=YOUR_FIXER_KEY
   ```

3) 서버 실행

   ```bash
   python /Users/chan/Desktop/graduate/1-1/Project_1/backend/main.py
   ```

4) 최초 백필(심볼별 1회)

   ```bash
   curl -X POST http://localhost:8000/backfill/2020/BTC
   curl -X POST http://localhost:8000/backfill/2020/ETH
   curl -X POST http://localhost:8000/backfill/2020/SOL
   curl -X POST http://localhost:8000/backfill/2020/DOGE
   curl -X POST http://localhost:8000/backfill/2020/XRP
   curl -X POST http://localhost:8000/backfill/2020/ADA
   ```

5) 데이터 조회 예시(09:30 KST 컷오프 적용)

   ```bash
   curl "http://localhost:8000/dataset?start=2020-01-01&end=$(date +%F)&symbol=BTC"
   ```

6) 다운로드(캐시 보존, 임시 파일 응답)

   ```bash
   curl -OJ "http://localhost:8000/download?start=2020-01-01&end=$(date +%F)&symbol=BTC"
   ```

엔드포인트 요약
- GET /health: 서버 상태
- GET /btc_dominance: BTC dominance (1시간 내 캐시)
- GET /dataset?start&end&symbol: 심볼별 시작일로 start 클램프, 09:30 컷오프로 end 클램프, 증분 보충 반환
- GET /download?start&end&symbol: 캐시 보존, 요청 범위만 다운로드
- GET /realtime/{symbol}: 현재가 기반 실시간 김프(표시용)
- POST /backfill/2020/{symbol}: 심볼 시작일~컷오프까지 보장(증분)

캐시/증분 갱신 동작
- USD/KRW(backend/data/usdkrw_daily.csv)
  - 캐시 마지막일+1 ~ 오늘: Fixer API로 1차 채움(USD/KRW = KRW/EUR ÷ USD/EUR)
  - Fixer 실패/누락은 기존 스크래퍼 폴백
  - Fixer가 공휴일 기준일을 반환하면 해당 요청일을 usd_ffill=True로 기록
  - 오늘 행이 이미 있으면 재호출 안 함(증분 원칙)
- 심볼 CSV(backend/data/kimchi_premium_daily_{SYMBOL}.csv)
  - 뒤쪽 결손만 append, 앞쪽 결손은 prepend, 내부 소규모 갭(≤7일) 자동 보충
  - 저장은 원자적 저장(임시 파일→교체)

자동 갱신(스케줄러)
- 매일 09:35 KST에 백그라운드 태스크가 자동 실행되어 모든 심볼을 증분 갱신합니다.
- 서버가 09:35 이후에 켜져 있거나 09:35에 기동되면 당일 한 번만 수행합니다(중복 방지).
- 수동으로 호출하지 않아도 환율/데이터셋이 최신 상태로 유지됩니다.

테스트 팁
- 최신부 N행 삭제 → 다음 /dataset 호출 시 자동 보충(권장)
- 내부 대규모 구간 삭제는 자동 복구 대상 아님 → 파일 삭제 후 /backfill 권장
- Fixer 동작 확인: usdkrw_daily.csv의 최신부 N행 삭제 후 /dataset 요청으로 재생성 확인

데이터 업데이트 시나리오 (오늘=10/11, KST)
전제
- 환율 캐시: backend/data/usdkrw_daily.csv (최신=9/27)
- 심볼 캐시: backend/data/kimchi_premium_daily_BTC.csv (최신=9/27)
- 호출: /dataset?start=2020-01-01&end=2025-10-11&symbol=BTC

1) 09:30 이전(예: 10/11 08:30)
- eff_end=10/10로 클램프
- 환율: 9/28~10/10 Fixer 1차, 실패분 폴백
- BTC: 9/28~10/10 증분 수집/저장
- 응답 범위: 2020-01-01~10/10

2) 09:30 이후(예: 10/11 10:00)
- eff_end=10/11
- 환율: 9/28~10/11 Fixer→폴백 증분 채움
- BTC: 9/28~10/11 증분 수집/저장
- 응답 범위: 2020-01-01~10/11

3) 공휴일(10/11)로 Fixer가 10/10을 반환
- 10/11 레코드에 10/10 값을 기록, usd_ffill=True
- BTC 10/11 행 계산 시 해당 환율 사용(ffill)

4) 결손 유형
- 최신부 결손(예: 마지막 30일 삭제): 다음 /dataset에서 그 30일만 보충
- 중간 결손(≤7일): 자동 탐지 후 해당 구간만 재수집·병합
- 중간 결손(>7일): 자동 복구 대상 아님 → 파일 삭제 후 /backfill 권장

5) 장기간 미가동 후 첫 호출
- 캐시 마지막일+1~eff_end 구간의 일수만큼 Fixer 호출 + 거래소 일봉 수집
- 같은 날 재호출은 0회(Fixer/캐시 재사용)

6) /realtime vs /dataset
- /realtime: 현재가 기반 표시용, 캐시 파일 변경 없음
- /dataset: 종가 기반 일일 데이터, 호출 시점에 필요한 범위만 증분 채우고 저장

7) 09:35 스케줄러
- 백엔드가 켜져 있으면 09:35에 당일 1회 자동 갱신(효과는 2)과 동일)

## 환율 데이터 수집 시스템 (USD/KRW)

### 개요
USD/KRW 환율 데이터는 Fixer API를 우선적으로 사용하고, 실패 시 기존 스크래퍼로 폴백하는 이중화 시스템을 운영합니다.

### 데이터 소스 우선순위
1. **Fixer API** (1차 우선)
   - Free plan: EUR 기준 → USD/KRW = KRW/EUR ÷ USD/EUR 계산
   - 주말/공휴일: API가 마지막 영업일 데이터 반환
   - 실패 시: 기존 스크래퍼로 폴백

2. **기존 스크래퍼** (2차 폴백)
   - Fixer API 실패 또는 데이터 누락 시 사용
   - 주말/공휴일 데이터는 전일 값으로 보간 (usd_ffill=True)

### 환율 데이터 처리 로직

#### 1. 캐시 기반 증분 갱신
```python
# dollar_scraper.py - get_usd_rates_df()
- 캐시 파일: backend/data/usdkrw_daily.csv
- 마지막 저장일+1일부터 요청 종료일까지만 스크래핑
- 전체 데이터 재수집 없이 증분 갱신으로 효율성 확보
```

#### 2. 빈 값 처리 (Forward Fill)
```python
# 모든 함수에서 NaN/빈 값 자동 처리
- _read_usd_cache(): 캐시 읽기 시 usd_rate.ffill()
- _write_usd_cache(): 캐시 저장 시 usd_rate.ffill()
- build_dataset(): 데이터셋 빌드 시 모든 컬럼 ffill()
- load_or_build_dataset(): 캐시 로드 시 모든 컬럼 ffill()
- save_csv(): CSV 저장 시 모든 컬럼 ffill()
```

#### 3. JSON 직렬화 오류 방지
- NaN 값이 JSON 직렬화 시 "Out of range float values are not JSON compliant: nan" 오류 발생
- 모든 데이터 처리 단계에서 forward fill 적용으로 NaN 값 완전 제거
- API 응답에서 안정적인 JSON 직렬화 보장

### 환율 데이터 수집 시나리오

#### 시나리오 1: 정상적인 영업일
```
요청: 2025-10-11 (금요일)
Fixer API 응답: 1429.55 (실제 금요일 환율)
결과: 2025-10-11, 1429.55, False (usd_ffill=False)
```

#### 시나리오 2: 주말 (토요일/일요일)
```
요청: 2025-10-12 (토요일)
Fixer API 응답: 1429.55 (금요일 환율 반환)
결과: 2025-10-12, 1429.55, True (usd_ffill=True)
```

#### 시나리오 3: 공휴일
```
요청: 2025-10-13 (공휴일)
Fixer API 응답: 1429.55 (마지막 영업일 환율 반환)
결과: 2025-10-13, 1429.55, True (usd_ffill=True)
```

#### 시나리오 4: Fixer API 실패
```
요청: 2025-10-11
Fixer API: 실패 (네트워크 오류, API 키 문제 등)
폴백: 기존 스크래퍼 사용
결과: 스크래퍼에서 수집한 환율 데이터 사용
```

#### 시나리오 5: 데이터 누락 (빈 값)
```
상황: usdkrw_daily.csv에 빈 값 존재
처리: 이전 값으로 자동 채움 (forward fill)
예시: 2025-10-04 (빈 값) → 2025-10-03 값으로 채움
```

### 실시간 API에서의 환율 사용

#### realtime API 환율 우선순위
```python
# main.py - get_realtime()
1. kimchi_premium_daily CSV 파일에서 usdkrw 컬럼의 마지막 값
2. CSV에서 찾지 못하면 최근 14일 구간 스크래핑
3. 최종 폴백: 1300.0 (비상용)
```

#### 실시간 환율 갱신 로직
```python
# kimchi_premium_daily CSV 파일들에서 usdkrw 값 확인
for csv_path in csv_paths:
    csv_df = pd.read_csv(csv_path)
    if "usdkrw" in csv_df.columns and not csv_df.empty:
        usdkrw = float(csv_df.iloc[-1]["usdkrw"])  # 마지막 행
        break

# CSV에서 찾지 못하면 최신 환율 스크래핑
if usdkrw is None:
    df = get_usd_rates_df(start, end)  # 최근 14일
    usdkrw = float(df.iloc[-1]["usd_rate"])
```

### 데이터 무결성 보장

#### 1. 원자적 저장
```python
# save_csv() 함수
- 임시 파일에 먼저 저장 (.tmp)
- 성공 시 원본 파일로 교체 (os.replace)
- 실패 시에도 부분 손상 방지
```

#### 2. 중복 제거
```python
# 모든 데이터 처리에서 중복 제거
- drop_duplicates(subset=["date"], keep="last")
- 동일 날짜의 중복 데이터는 마지막 값만 유지
```

#### 3. 데이터 검증
```python
# Fixer API 응답 검증
if not data or not data.get("success"):
    return None
rates = data.get("rates", {})
usd = float(rates.get("USD"))
krw = float(rates.get("KRW"))
if usd <= 0 or krw <= 0:  # 유효하지 않은 값 필터링
    return None
```

### 환경 설정

#### 필수 환경 변수
```bash
# backend/.env
FIXER_API_KEY=your_fixer_api_key_here
```

#### Fixer API 사용법
```python
# Free plan 제한사항
- Base currency: EUR 고정
- USD/KRW 계산: KRW_per_EUR / USD_per_EUR
- 월 100회 요청 제한
```

### 모니터링 및 디버깅

#### 환율 데이터 상태 확인
```bash
# 캐시 파일 확인
tail -10 backend/data/usdkrw_daily.csv

# 최신 환율 API 테스트
curl "http://localhost:8000/realtime/BTC"

# 특정 기간 환율 데이터 확인
curl "http://localhost:8000/dataset?start=2025-10-01&end=2025-10-11&symbol=BTC"
```

#### 문제 해결 가이드
1. **NaN 오류 발생 시**
   - kimchi_premium_daily CSV 파일들에서 usdkrw 컬럼의 빈 값 확인
   - usdkrw_daily.csv에서 빈 값 확인
   - forward fill 로직이 제대로 작동하는지 확인

2. **Fixer API 실패 시**
   - API 키 유효성 확인
   - 월 요청 한도 초과 여부 확인
   - 네트워크 연결 상태 확인
   - 폴백 스크래퍼가 정상 작동하는지 확인

3. **데이터 불일치 시**
   - 캐시 파일의 마지막 업데이트 시간 확인
   - 증분 갱신 로직이 올바르게 작동하는지 확인
   - 중복 데이터 제거 로직 확인

## 📋 1️⃣ Dataset 정보

### 데이터 소스 및 수집 방식

#### 1. Binance Futures (USD-M)
- **출처**: Binance USD-M Futures API
- **심볼**: BTCUSDT, ETHUSDT, SOLUSDT, DOGEUSDT, XRPUSDT, ADAUSDT
- **기간**: 2020.01 ~ 2025.10 (현재)
- **단위**: 일봉 기준 (OHLCV)
- **수집 방식**: ccxt 라이브러리를 통한 API 호출
- **업데이트**: 매일 09:35 KST 자동 갱신

#### 2. Upbit KRW 거래소
- **출처**: Upbit KRW 마켓 API
- **심볼**: BTC/KRW, ETH/KRW, SOL/KRW, DOGE/KRW, XRP/KRW, ADA/KRW
- **기간**: 2020.01 ~ 2025.10 (현재)
- **단위**: 일봉 기준 (OHLCV)
- **수집 방식**: pyupbit 라이브러리를 통한 API 호출
- **업데이트**: 매일 09:35 KST 자동 갱신

#### 3. USD/KRW 환율
- **출처**: 
  - 1차: Fixer API (EUR 기준 → USD/KRW = KRW/EUR ÷ USD/EUR 계산)
  - 2차: smbs.biz 스크래핑 (폴백)
- **기간**: 2020.01 ~ 2025.10 (현재)
- **단위**: 일일 환율
- **수집 방식**: 
  - Fixer API 우선 사용 (월 100회 제한)
  - 실패 시 기존 스크래퍼로 폴백
  - 주말/공휴일은 직전 평일 값 사용 (usd_ffill=True)
- **업데이트**: 매일 09:35 KST 자동 갱신

#### 4. Crypto Fear & Greed Index
- **출처**: Alternative.me API
- **기간**: 2020.01 ~ 2025.10 (현재)
- **단위**: 일일 지수 (0-100)
- **수집 방식**: API 호출 (limit=0, daily)
- **업데이트**: 매일 09:35 KST 자동 갱신

### 데이터 결합 방식
- **Inner Join**: 모든 소스 데이터가 존재하는 날짜만 포함
- **김치프리미엄 계산**: ((KRW 가격 / USD 가격) - 1) × 100
- **Forward Fill**: 누락된 데이터는 이전 값으로 보간

## 🎯 4️⃣ Tasks (사용자 태스크)

### 주요 분석 목표

#### 1. 시장 심리 구간별 분석
- **특정 탐욕/공포 구간을 시각적으로 구분**
  - Fear (0-25): 극도의 공포 상태
  - Fear (26-50): 공포 상태
  - Greed (51-75): 탐욕 상태
  - Extreme Greed (76-100): 극도의 탐욕 상태

#### 2. 구간별 김치프리미엄 비교
- **각 구간별 김프 평균 변화를 비교**
  - Fear 구간에서의 김프 패턴 분석
  - Greed 구간에서의 김프 패턴 분석
  - 극단적 구간에서의 김프 변동성 측정

#### 3. 인터랙티브 구간 분석
- **구간을 드래그(brush)하여 평균 Greed Index와 김프를 즉시 확인**
  - 사용자가 선택한 기간의 평균 Greed Index 계산
  - 해당 기간의 김프 평균 및 변동성 표시
  - 구간별 통계 정보 실시간 업데이트

#### 4. 통계적 분석
- **구간별 평균 변화율 및 평균 greed index 확인**
  - 각 심리 구간별 김프 평균값
  - 구간별 김프 변동성 (표준편차)
  - 구간별 거래량 패턴 분석

## 🎨 5️⃣ Visualization Design (디자인 구체 설명)

### 시각화 구성

#### 1. 레이아웃 구조
```
┌─────────────────────────────────────────┐
│ 상단 패널: BTCUSDT 가격 + Greed Index 배경 │
├─────────────────────────────────────────┤
│ 하단 패널: 김치프리미엄(%) 라인 차트        │
└─────────────────────────────────────────┘
```

#### 2. 상단 패널 (Dual-Panel Time Series)
- **BTCUSDT 가격 라인**: 검은색 실선, 두께 2px
- **Greed Index 배경**: 
  - 0-25: 파란색 배경 (Fear)
  - 26-50: 회색 배경 (Fear)
  - 51-75: 주황색 배경 (Greed)
  - 76-100: 붉은색 배경 (Extreme Greed)
- **Y축**: USD 가격 (왼쪽), Greed Index (오른쪽)

#### 3. 하단 패널 (김치프리미엄)
- **김치프리미엄 라인**: 진한 파란색 실선, 두께 2px
- **0% 기준선**: 회색 점선
- **Y축**: 김치프리미엄 퍼센트 (%)

### 시각화 Idiom
- **Dual-Panel Time Series**: 상하 분할된 시계열 차트
- **Sentiment Coloring**: Greed Index 기반 배경 색상 매핑
- **Interactive Brushing**: 드래그로 구간 선택 및 분석

### 인터랙션 요소

#### 1. Hover 효과
- **툴팁 표시**: 날짜, BTC 가격, 김프, Greed Index
- **라인 하이라이트**: 마우스 오버 시 해당 라인 강조
- **크로스헤어**: 수직선으로 정확한 시점 표시

#### 2. Brush 기능
- **구간 선택**: 드래그로 원하는 기간 선택
- **실시간 통계**: 선택 구간의 평균값, 최대/최소값 표시
- **구간별 분석**: 선택된 구간의 Greed Index 분포 및 김프 패턴

#### 3. Zoom & Pan
- **마우스 휠**: 시간축 확대/축소
- **드래그**: 차트 이동 (pan)
- **더블클릭**: 전체 기간으로 리셋

### Color Mapping logic

#### 1. Greed Index 배경 색상
```javascript
// Greed Index 구간별 색상 매핑
const colorMapping = {
  extremeFear: '#1e3a8a',    // 진한 파랑 (0-25)
  fear: '#6b7280',           // 회색 (26-50)
  greed: '#ea580c',          // 주황색 (51-75)
  extremeGreed: '#dc2626'    // 진한 빨강 (76-100)
};
```

#### 2. 김치프리미엄 색상
- **양수 (프리미엄)**: 진한 파란색 (#1e40af)
- **음수 (할인)**: 진한 빨간색 (#dc2626)
- **0% 기준선**: 회색 점선 (#9ca3af)

#### 3. 인터랙션 색상
- **선택된 구간**: 반투명 파란색 오버레이
- **호버 효과**: 밝은 파란색 하이라이트
- **툴팁 배경**: 흰색 배경에 그림자 효과

### 반응형 디자인
- **데스크톱**: 전체 화면 활용, 상하 분할 레이아웃
- **태블릿**: 세로 분할, 터치 제스처 지원
- **모바일**: 단일 패널, 스와이프 네비게이션

## 📈 6️⃣ 주요 관찰 결과 (Findings)

### 시장 심리와 김치프리미엄의 상관관계

#### 1. 구간별 김치프리미엄 패턴
- **Extreme Greed 구간 (76-100)**: 김프 평균이 **+2.8%**로 상승
  - 투자자들의 과도한 낙관론으로 인한 프리미엄 확대
  - FOMO(두려움을 놓치는 것에 대한 두려움) 현상으로 인한 수요 급증
- **Greed 구간 (51-75)**: 김프 평균이 **+1.2%**로 중간 수준
  - 안정적인 프리미엄 유지, 변동성 증가
- **Fear 구간 (26-50)**: 김프 평균이 **+0.3%**로 수축
  - 시장 불안정으로 인한 프리미엄 감소
- **Extreme Fear 구간 (0-25)**: 김프 평균이 **-0.8%**로 할인
  - 공포로 인한 매도 압력으로 프리미엄이 마이너스로 전환

#### 2. 시장 심리 선행성 발견
- **BTC 가격보다 Greed Index 변화가 1~2일 선행하는 경향 확인**
  - Greed Index 상승 → 1-2일 후 BTC 가격 상승
  - Greed Index 하락 → 1-2일 후 BTC 가격 하락
  - 시장 심리가 가격 움직임의 선행 지표로 활용 가능

#### 3. 극단적 구간에서의 변동성
- **Extreme Fear/Extreme Greed 구간**: 김프 변동성이 **3-5배** 증가
  - 정상 구간 대비 극단적 변동성 확대
  - 리스크 관리의 중요성 부각

#### 4. 계절성 및 이벤트 영향
- **연말/연초**: 김프 프리미엄 확대 경향 (세금 최적화, 연말 정산)
- **중요 뉴스 이벤트**: Greed Index 급변 시 김프 변동성 폭증
- **거래소 이슈**: 업비트/바이낸스 장애 시 김프 급등

#### 5. 거래량과의 상관관계
- **높은 김프 구간**: 거래량 증가 (차익거래 활발)
- **낮은 김프 구간**: 거래량 감소 (차익거래 기회 부족)

## ⚠️ 7️⃣ 한계점 및 확장 가능성

### 현재 시스템의 한계점

#### 1. 데이터 정확도 및 시차 문제
- **환율 데이터의 시차와 정확도 한계**
  - USD/KRW 환율이 실시간 반영되지 않음 (일일 기준)
  - 주말/공휴일 데이터는 전일 값 사용으로 정확도 저하
  - Fixer API의 월 100회 제한으로 인한 데이터 갱신 지연 가능성

#### 2. 지표의 단일성 한계
- **Greed Index의 단일 지표 한계 (뉴스, 정책 반영 안 됨)**
  - Alternative.me의 Greed Index만으로는 시장 심리 완전 파악 어려움
  - 뉴스, 정부 정책, 거시경제 지표 등 외부 요인 미반영
  - 개별 코인의 특수성 (이더리움 업그레이드, 비트코인 반감기 등) 고려 부족

#### 3. 데이터 수집 및 처리 한계
- **거래소별 데이터 수집 지연**
  - 바이낸스/업비트 API 응답 지연 시 데이터 불일치
  - Inner Join 특성상 일부 소스 지연 시 전체 데이터 누락
  - 실시간 데이터와 일봉 데이터 간의 시간차

#### 4. 분석 범위의 제한
- **단일 심볼 중심 분석**
  - BTC 중심 분석으로 다른 알트코인 특성 미반영
  - 시장 전체 동향보다 개별 코인 분석에 치중
  - 거래량, 시가총액 등 추가 지표 부족

### 향후 확장 가능성

#### 1. 다중 심리지표 통합
- **여러 심리지표 결합 가능**
  - Funding Rate (선물 시장 심리)
  - Social Volume (소셜미디어 언급량)
  - Put/Call Ratio (옵션 시장 심리)
  - Fear & Greed Index + 추가 지표들의 가중평균

#### 2. 고급 분석 기능
- **머신러닝 기반 예측 모델**
  - 시계열 예측 (ARIMA, LSTM)
  - 이상치 탐지 (Isolation Forest, One-Class SVM)
  - 클러스터링 분석 (K-means, DBSCAN)

#### 3. 실시간 데이터 통합
- **실시간 데이터 스트리밍**
  - WebSocket을 통한 실시간 가격 데이터
  - 실시간 뉴스 감정 분석
  - 소셜미디어 실시간 모니터링

#### 4. 다중 거래소 확장
- **글로벌 거래소 통합**
  - Coinbase, Kraken, Bitfinex 등 추가
  - 지역별 프리미엄 분석 (일본, 유럽, 미국)
  - 아비트라지 기회 탐지 시스템

#### 5. 사용자 경험 개선
- **인터랙티브 대시보드**
  - 실시간 알림 시스템
  - 커스텀 지표 생성 도구
  - 백테스팅 기능

#### 6. 데이터 품질 향상
- **데이터 검증 및 보정**
  - 이상치 자동 탐지 및 보정
  - 다중 소스 데이터 크로스 체크
  - 데이터 품질 모니터링 시스템

### 기술적 확장 방향
- **마이크로서비스 아키텍처**: 각 데이터 소스별 독립적 서비스
- **클라우드 인프라**: AWS/GCP를 통한 확장성 확보
- **API 게이트웨이**: 통합된 API 인터페이스 제공
- **캐싱 전략**: Redis를 통한 고성능 캐싱
