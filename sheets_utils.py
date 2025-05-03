import gspread
from google.oauth2.service_account import Credentials

# Google Sheets setup
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
SHEET_NAME = 'tushlik'
WORKSHEET_NAME = 'Sheet1'
CREDENTIALS_FILE = 'credentials.json'

def get_worksheet():
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open(SHEET_NAME)
    worksheet = sh.worksheet(WORKSHEET_NAME)
    return worksheet

def fetch_all_rows():
    worksheet = get_worksheet()
    return worksheet.get_all_records() 