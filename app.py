import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import datetime
import json

# --- 設定 ---
# 請確認您的 Google Sheet 檔名與分頁名稱完全一致
SPREADSHEET_NAME = "教科書填報" 
SHEET_HISTORY = "DB_History"
SHEET_CURRICULUM = "DB_Curriculum"
SHEET_SUBMISSION = "Submission_Records"

# --- 1. 連線設定 (支援雲端 Secrets 與本地 JSON) ---
@st.cache_resource
def get_connection():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    
    # 優先嘗試讀取 Streamlit 雲端設定的 Secrets
    if "GCP_CREDENTIALS" in st.secrets:
        try:
            # 嘗試解析 JSON 字串
            creds_dict = json.loads(st.secrets["GCP_CREDENTIALS"])
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        except json.JSONDecodeError:
            # 如果解析失敗，可能是格式問題，嘗試直接讀取 (有些時候 secrets 會自動解析 TOML)
            st.error("Secrets 格式解析錯誤，請確認 JSON 格式正確。")
            return None
    else:
        # 如果在本地開發，嘗試讀取檔案
        try:
            creds = Credentials.from_service_account_file('credentials.json', scopes=scope)
        except Exception:
            st.error("找不到金鑰！請確認本地有 credentials.json 或雲端已設定 Secrets (GCP_CREDENTIALS)。")
            return None
            
    client = gspread.authorize(creds)
    return client

# --- 2. 資料讀取函式 ---
def load_data(dept, semester, grade):
    client = get_connection()
    if not client: return pd.DataFrame()

    try:
        sh = client.open(SPREADSHEET_NAME)
    except Exception as e:
        st.error(f"找不到試算表：{SPREADSHEET_NAME}。請確認機器人 Email 已加入共用。錯誤：{e}")
        return pd.DataFrame()

    try:
        ws_curr = sh.worksheet(SHEET_CURRICULUM)
        ws_hist = sh.worksheet(SHEET_HISTORY)
        
        # 讀取全部資料
        data_curr = ws_curr.get_all_records()
        data_hist = ws_hist.get_all_records()
        
        df_curr = pd.DataFrame(data_curr)
        df_hist = pd.DataFrame(data_hist)
        
        # 轉型避免錯誤 (確保年級與學期都是字串)
        for df in [df_curr, df_hist]:
            if not df.empty:
                df['年級'] = df['年級'].astype(str)
                df['學期'] = df['學期'].astype(str)
            
    except Exception as e:
        st.error(f"讀取分頁錯誤 (可能是分頁名稱不對): {e}")
        return pd.DataFrame()

    # 篩選課綱 (Curriculum)
    mask_curr = (df_curr['科別'] == dept) & (df_curr['學期'] == semester) & (df_curr['年級'] == grade)
    target_courses = df_curr[mask_curr]

    if target_courses.empty:
        return pd.DataFrame()

    display_rows = []
    
    # 針對每一門課，去 History 找找看以前有沒有填過
    for _, row in target_courses.iterrows():
        c_name = row['課程名稱']
        c_type = row['課程類別']
        # 處理預設班級可能為空的情況
        default_class = row.get('預設適用班級', '')

        # 在 History 中尋找同名的課
        hist_matches = df_hist[df_hist['課程名稱'] == c_name]

        if not hist_matches.empty:
            # 如果歷史資料有紀錄 (可能有多筆，例如測量實習有兩本書)
            for _, h_row in hist_matches.iterrows():
                new_row = {
                    "科別": dept, "年級": grade, "學期": semester,
                    "課程類別": c_type, "課程名稱": c_name,
                    "教科書(優先1)": h_row.get('教科書(優先1)', ''),
                    "冊次(1)": h_row.get('冊次(1)', ''),
                    "出版社(1)": h_row.get('出版社(1)', ''),
                    "審定字號(1)": h_row.get('審定字號(1)', ''),
                    "教科書(優先2)": h_row.get('教科書(優先2)', ''),
                    "冊次(2)": h_row.get('冊次(2)', ''),
                    "出版社(2)": h_row.get('出版社(2)', ''),
                    "審定字號(2)": h_row.get('審定字號(2)', ''),
                    "適用班級": h_row.get('適用班級', default_class), # 優先用歷史填的班級
                    "備註": h_row.get('備註', '')
                }
                display_rows.append(new_row)
        else:
            # 歷史沒資料，帶入空白的一列
            new_row = {
                "科別": dept, "年級": grade, "學期": semester,
                "課程類別": c_type, "課程名稱": c_name,
                "教科書(優先1)": "", "冊次(1)": "", "出版社(1)": "", "審定字號(1)": "",
                "教科書(優先2)": "", "冊次(2)": "", "出版社(2)": "", "審定字號(2)": "",
                "適用班級": default_class, 
                "備註": ""
            }
            display_rows.append(new_row)

    return pd.DataFrame(display_rows)

# --- 3. 存檔函式 ---
def save_submission(df_to_save):
    client = get_connection()
    sh = client.open(SPREADSHEET_NAME)
    
    # 嘗試開啟或建立 Submission 分頁
    try:
        ws_sub = sh.worksheet(SHEET_SUBMISSION)
    except:
        ws_sub = sh.add_worksheet(title=SHEET_SUBMISSION, rows=1000, cols=20)
        ws_sub.append_row(["填報時間", "科別", "年級", "學期", "課程名稱", "教科書(1)", "冊次", "出版社", "字號", "教科書(2)", "冊次", "出版社", "字號", "適用班級", "備註"])

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data_list = []
    
    # 轉換 DataFrame 為 List 準備寫入
    for _, row in df_to_save.iterrows():
        data_list.append([
            timestamp,
            row['科別'], row['年級'], row['學期'], row['課程名稱'],
            row['教科書(優先1)'], row['冊次(1)'], row['出版社(1)'], row['審定字號(1)'],
            row['教科書(優先2)'], row['冊次(2)'], row['出版社(2)'], row['審定字號(2)'],
            row['適用班級'], row['備註']
        ])
    
    # 寫入 (Append)
    ws_sub.append_rows(data_list)
    return True

# --- 4. Streamlit 介面主程式 ---
def main():
    st.set_page_config(page_title="教科書填報系統", layout="wide")
    st.title("📚 教科書填報系統")

    # --- 側邊欄：設定區 ---
    with st.sidebar:
        st.header("1. 設定填報範圍")
        # 您可以在這裡增減科別
        dept_options = ["建築科", "機械科", "電機科", "製圖科", "室設科", "國文科", "英文科", "數學科", "自然科", "社會科"]
        dept = st.selectbox("科別", dept_options)
        
        col1, col2 = st.columns(2)
        with col1:
            sem = st.selectbox("學期", ["1", "2"])
        with col2:
            grade = st.selectbox("年級", ["1", "2", "3"])
        
        st.divider()
        
        if st.button("📥 載入課程資料", type="primary", use_container_width=True):
            with st.spinner("正在讀取 Google Sheets..."):
                df = load_data(dept, sem, grade)
                if not df.empty:
                    st.session_state['data'] = df
                    st.session_state['loaded'] = True
                    # 清除之前的成功訊息
                    if 'success_msg' in st.session_state:
                        del st.session_state['success_msg']
                else:
                    st.warning(f"查無資料 ({dept} / {grade}年級)，請確認「課綱表」是否有設定。")

    # --- 主畫面：資料編輯區 ---
    if st.session_state.get('loaded'):
        st.success(f"目前編輯：**{dept}** / **{grade}年級** / **第{sem}學期**")
        
        # 顯示可編輯表格 (Data Editor)
        # num_rows="dynamic" 讓使用者可以按底下的 + 新增列
        edited_df = st.data_editor(
            st.session_state['data'],
            num_rows="dynamic",
            use_container_width=True,
            height=600,
            column_config={
                "課程類別": st.column_config.SelectboxColumn(
                    "類別",
                    options=["部定必修", "校訂必修", "校訂選修", "實習科目", "一般科目"],
                    required=True,
                    width="small"
                ),
                "課程名稱": st.column_config.TextColumn("課程名稱", required=True),
                "教科書(優先1)": st.column_config.TextColumn("教科書(1)", width="medium"),
                "冊次(1)": st.column_config.SelectboxColumn(
                    "冊次",
                    options=["全", "上", "下", "I", "II", "III", "IV", "V", "VI"],
                    width="small"
                ),
                 "冊次(2)": st.column_config.SelectboxColumn(
                    "冊次(2)",
                    options=["全", "上", "下", "I", "II", "III", "IV", "V", "VI"],
                    width="small"
                ),
                "適用班級": st.column_config.TextColumn("適用班級", width="medium"),
            },
            hide_index=True
        )

        st.caption("💡 操作提示：1. 直接點擊儲存格修改。 2. 若同一門課要買兩本書，請點表格下方的 `+` 新增一列，填入相同課名。")

        # --- 提交按鈕區 ---
        col_submit, col_dummy = st.columns([1, 4])
        with col_submit:
            if st.button("💾 確認提交 (寫入資料庫)", type="primary", use_container_width=True):
                if edited_df.empty:
                    st.error("表格是空的，無法提交。")
                else:
                    with st.spinner("正在寫入 Google Sheets..."):
                        try:
                            if save_submission(edited_df):
                                st.success("✅ 資料已成功提交！")
                                st.balloons()
                                # 選擇性：提交後清空或保留畫面，這裡選擇保留以便查看
                        except Exception as e:
                            st.error(f"寫入失敗：{e}")

    else:
        st.info("👈 請先在左側選擇科別與年級，並點擊「載入」按鈕。")

if __name__ == "__main__":
    main()
