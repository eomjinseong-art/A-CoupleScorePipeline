import time
import requests
import pandas as pd
from io import StringIO
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

# Google API 사용 시 필요한 라이브러리 (필요시 pip install gspread google-auth)
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GSPREAD_AVAILABLE = True
except ImportError:
    GSPREAD_AVAILABLE = False


# ==============================================================================
# 1. [전략 1] HTTP CSV URL 요청 (재시도 + User-Agent 헤더 적용)
# ==============================================================================
def fetch_via_csv_url(sheet_csv_url: str, max_retries: int = 3) -> pd.DataFrame:
    """
    HTTP GET 방식으로 CSV 데이터를 가져옵니다.
    네트워크 흔들림, 구글 봇 차단(RemoteDisconnected) 현상을 방지하기 위해 
    User-Agent 헤더 및 Automatic Retry 로직이 적용되어 있습니다.
    """
    session = requests.Session()
    
    # 500, 502, 503, 504 등 서버 에러 및 연결 끊김 시 지수 백오프 재시도
    retries = Retry(
        total=max_retries,
        backoff_factor=2,  # 2초, 4초, 8초 대기 후 재시도
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    }

    print(" -> [시도] HTTP CSV URL 방식으로 구글 시트 요청 중...")
    resp = session.get(sheet_csv_url, headers=headers, timeout=15)
    resp.raise_for_status()

    # CSV 응답 텍스트를 Pandas DataFrame으로 변환
    csv_data = StringIO(resp.text)
    df = pd.read_csv(csv_data)
    return df


# ==============================================================================
# 2. [전략 2] Google Sheets 공식 API 사용 (재시도 로직 포함)
# ==============================================================================
def fetch_via_google_api(service_account_path: str, spreadsheet_id: str, max_retries: int = 3) -> pd.DataFrame:
    """
    Google Service Account 인증 및 gspread 라이브러리를 사용하여 시트 데이터를 읽습니다.
    """
    if not GSPREAD_AVAILABLE:
        raise ImportError("gspread 또는 google-auth 패키지가 설치되어 있지 않습니다.")

    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(service_account_path, scopes=scopes)
    client = gspread.authorize(creds)

    for attempt in range(1, max_retries + 1):
        try:
            print(f" -> [시도 {attempt}/{max_retries}] Google API 방식으로 구글 시트 요청 중...")
            doc = client.open_by_key(spreadsheet_id)
            sheet = doc.sheet1  # 첫 번째 시트 선택
            records = sheet.get_all_records()
            return pd.DataFrame(records)
        except Exception as e:
            print(f"    └ Google API 호출 실패 ({e})")
            if attempt == max_retries:
                raise e
            time.sleep(2 ** attempt)


# ==============================================================================
# 3. [전략 1 + 전략 2 결합] 통합 fetch_next_episode 함수
# ==============================================================================
def fetch_next_episode(
    sheet_csv_url: str = None, 
    service_account_path: str = None, 
    spreadsheet_id: str = None
):
    """
    1차로 Google API 방식을 시도하고, 실패하거나 설정이 없을 경우
    2차로 재시도 로직이 적용된 CSV URL 방식을 호출하는 이중 안전 구조입니다.
    """
    print("\n[1/7] Google Sheets에서 대본 읽기...")

    # 1안: Google API 설정 및 라이브러리가 준비되어 있으면 우선 시도
    if service_account_path and spreadsheet_id and GSPREAD_AVAILABLE:
        try:
            df = fetch_via_google_api(service_account_path, spreadsheet_id)
            print(" -> Google API를 통해 성공적으로 대본을 읽어왔습니다.")
            return df
        except Exception as e:
            print(f" -> Google API 방식 실패. CSV URL 방식으로 Fallback(2차 시도)을 진행합니다. 원인: {e}")

    # 2안: CSV URL 방식 시도 (API가 실패했거나, API 설정이 없을 때 실행)
    if sheet_csv_url:
        try:
            df = fetch_via_csv_url(sheet_csv_url)
            print(" -> HTTP CSV URL을 통해 성공적으로 대본을 읽어왔습니다.")
            return df
        except Exception as e:
            print(f" -> [최종 오류] CSV URL 방식도 실패했습니다: {e}")
            raise e
    else:
        raise ValueError("SHEET_CSV_URL 또는 Google API 설정(service_account_path, spreadsheet_id)이 필요합니다.")


# ==============================================================================
# 실행 테스트 예시
# ==============================================================================
if __name__ == "__main__":
    # 설정 예시 (실제 사용하시는 URL / 키 경로를 대입하세요)
    SHEET_CSV_URL = "https://docs.google.com/spreadsheets/d/e/2PACX-1v.../pub?output=csv"
    
    # Google API를 사용하는 경우 설정 (선택 사항)
    SERVICE_ACCOUNT_JSON = "service_account.json"
    SPREADSHEET_ID = "your_spreadsheet_id_here"

    try:
        # 기존 SHEET_CSV_URL만 넘겨주어도 재시도 및 헤더 보완 로직이 작동하여 오류를 막아줍니다.
        df_episode = fetch_next_episode(
            sheet_csv_url=SHEET_CSV_URL,
            # service_account_path=SERVICE_ACCOUNT_JSON,  # Google API 사용 시 주석 해제
            # spreadsheet_id=SPREADSHEET_ID               # Google API 사용 시 주석 해제
        )
        print("수집 완료된 데이터 샘플:")
        print(df_episode.head())

    except Exception as err:
        print(f"파이프라인 실행 중 오류 발생: {err}")
