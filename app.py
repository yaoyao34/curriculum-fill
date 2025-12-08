import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import datetime
import json
import base64
import uuid
import math

def safe_note(row):
    """
    最終穩定版 v2：
    - 自動抓所有「備註」欄位
    - 處理 Series
    - 用 replace 清掉 備註1/2
    - 移除 dtype 尾巴
    - ✅ 若 r1 == r2，自動清空 r2（避免雙重顯示）
    """

    note_cols = [c for c in row.index if "備註" in str(c)]

    notes = []

    for col in note_cols:
        val = row[col]

        if isinstance(val, pd.Series):
            if not val.empty:
                val = val.iloc[0]
            else:
                val = ""

        if val is None or str(val).lower() == "nan":
            val = ""

        val = str(val)

        # 強制移除 備註1 / 備註2
        val = val.replace("備註1", "").replace("備註2", "")

        # 強制移除 Name: 0, dtype: object
        if "dtype" in val:
            val = val.split("Name:")[0]

        val = val.replace("\n", " ").strip()

        notes.append(val)

    r1 = notes[0] if len(notes) > 0 else ""
    r2 = notes[1] if len(notes) > 1 else ""

    # ✅ ✅ ✅ 重點修正：如果 r1 == r2，視為只有一則備註
    if r1 and r2 and r1 == r2:
        r2 = ""

    return [r1, r2]


# --- NEW: Import FPDF for PDF generation
from fpdf import FPDF 

# --- 全域設定 ---
SPREADSHEET_NAME = "教科書填報" 
SHEET_HISTORY = "DB_History"
SHEET_CURRICULUM = "DB_Curriculum"
SHEET_SUBMISSION = "Submission_Records"

# --- 0. 班級資料庫 ---
ALL_SUFFIXES = {
    "普通科": ["機甲", "機乙", "電甲", "電乙", "建築", "室設", "製圖"],
    "建教班": ["機丙", "模丙"],
    "實用技能班": ["機加", "電修", "營造"]
}

DEPT_SPECIFIC_CONFIG = {
    "機械科": { "普通科": ["機甲", "機乙"], "建教班": ["機丙", "模丙"], "實用技能班": ["機加"] },
    "電機科": { "普通科": ["電甲", "電乙"], "建教班": [], "實用技能班": ["電修"] },
    "建築科": { "普通科": ["建築"], "建教班": [], "實用技能班": ["營造"] },
    "室設科": { "普通科": ["室設"], "建教班": [], "實用技能班": [] },
    "製圖科": { "普通科": ["製圖"], "建教班": [], "實用技能班": [] }
}

# --- 1. 連線設定 ---
@st.cache_resource
def get_connection():
    scope = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    if "GCP_CREDENTIALS" in st.secrets:
        try:
            creds_dict = json.loads(st.secrets["GCP_CREDENTIALS"])
            creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        except json.JSONDecodeError:
            st.error("Secrets 格式錯誤")
            return None
        except ValueError as e: # 處理可能不是 JSON 的情況
            try:
                # 假設 GCP_CREDENTIALS 是一個 Base64 編碼的 JSON
                creds_json_str = base64.b64decode(st.secrets["GCP_CREDENTIALS"]).decode('utf-8')
                creds_dict = json.loads(creds_json_str)
                creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
            except Exception as e:
                st.error(f"Secrets 格式錯誤或 Base64 解碼失敗: {e}")
                return None
    else:
        try:
            creds = Credentials.from_service_account_file('credentials.json', scopes=scope)
            pass
        except Exception:
            st.error("找不到金鑰")
            return None
    return gspread.authorize(creds)

# --- 2. 資料讀取 (v7：修復 InvalidIndexError + 歷史資料切換邏輯) ---
def load_data(dept, semester, grade, use_history=False):
    client = get_connection()
    if not client: return pd.DataFrame()
    try:
        sh = client.open(SPREADSHEET_NAME)
        # 根據模式決定讀取哪些工作表，但為了比對通常都需要 Sub
        ws_sub = sh.worksheet(SHEET_SUBMISSION)
        
        # 讀取 Submission (共用)
        def get_df(ws):
            data = ws.get_all_values()
            if not data: return pd.DataFrame()
            headers = data[0]
            rows = data[1:]
            seen = {}
            new_headers = []
            for col in headers:
                c = str(col).strip()
                if c in seen:
                    seen[c] += 1
                    # 重新命名重複欄位，避免 InvalidIndexError
                    new_name = f"{c}({seen[c]})" 
                    if c == '教科書': new_name = f"教科書(優先{seen[c]})"
                    elif c == '冊次': new_name = f"冊次({seen[c]})"
                    elif c == '出版社': new_name = f"出版社({seen[c]})"
                    elif c == '字號' or c == '審定字號': new_name = f"審定字號({seen[c]})"
                    elif c.startswith('備註'): new_name = f"備註{seen[c]}"
                    new_headers.append(new_name)
                else:
                    seen[c] = 1
                    if c == '教科書': new_headers.append('教科書(優先1)')
                    elif c == '冊次': new_headers.append('冊次(1)')
                    elif c == '出版社': new_headers.append('出版社(1)')
                    elif c == '字號' or c == '審定字號': new_headers.append('審定字號(1)')
                    elif c.startswith('備註'): new_headers.append('備註1')
                    else: new_headers.append(c)
            return pd.DataFrame(rows, columns=new_headers)

        df_sub = get_df(ws_sub)
        
        # 統一轉字串避免比對錯誤
        if not df_sub.empty:
            df_sub['年級'] = df_sub['年級'].astype(str)
            df_sub['學期'] = df_sub['學期'].astype(str)
            df_sub['科別'] = df_sub['科別'].astype(str)

        display_rows = []
        displayed_uuids = set()

        # ==========================================
        # 模式 A: 載入歷史資料 (History Mode)
        # ==========================================
        if use_history:
            ws_hist = sh.worksheet(SHEET_HISTORY)
            df_hist = get_df(ws_hist)
            if not df_hist.empty:
                df_hist['年級'] = df_hist['年級'].astype(str)
                df_hist['學期'] = df_hist['學期'].astype(str)
                df_hist['科別'] = df_hist['科別'].astype(str)
                
                # 1. 篩選 History
                mask_hist = (df_hist['科別'] == dept) & (df_hist['學期'] == str(semester)) & (df_hist['年級'] == str(grade))
                target_hist = df_hist[mask_hist]

                # 2. 遍歷 History，優先使用 Submission 的資料 (對應 UUID)
                for _, h_row in target_hist.iterrows():
                    h_uuid = str(h_row.get('uuid', '')).strip()
                    if not h_uuid: h_uuid = str(uuid.uuid4()) # 防呆

                    # 嘗試在 Submission 找這個 UUID
                    sub_match = pd.DataFrame()
                    if not df_sub.empty:
                        sub_match = df_sub[df_sub['uuid'] == h_uuid]
                    
                    row_data = {}
                    
                    if not sub_match.empty:
                        # [情境] Submission 有這筆資料 (已被修改過) -> 用 Submission
                        s_row = sub_match.iloc[0]
                        row_data = s_row.to_dict() # 轉 dict 避免 index 問題
                        # 確保 uuid 一致
                        row_data['uuid'] = h_uuid
                        row_data['勾選'] = False
                    else:
                        # [情境] Submission 沒這筆 -> 用 History 原文
                        row_data = h_row.to_dict() # 轉 dict 避免 index 問題
                        row_data['uuid'] = h_uuid
                        row_data['勾選'] = False
                        
                        # 補齊可能缺失的欄位 key (因為 History 欄位名稱可能跟 Submission 略有不同)
                        if '教科書(1)' in row_data and '教科書(優先1)' not in row_data: row_data['教科書(優先1)'] = row_data['教科書(1)']
                        if '字號(1)' in row_data and '審定字號(1)' not in row_data: row_data['審定字號(1)'] = row_data['字號(1)']
                        if '字號(2)' in row_data and '審定字號(2)' not in row_data: row_data['審定字號(2)'] = row_data['字號(2)']

                    display_rows.append(row_data)
                    displayed_uuids.add(h_uuid)

        # ==========================================
        # 模式 B: 不載入歷史 (Curriculum Mode - 預設)
        # ==========================================
        else:
            ws_curr = sh.worksheet(SHEET_CURRICULUM)
            df_curr = get_df(ws_curr)
            if not df_curr.empty:
                df_curr['年級'] = df_curr['年級'].astype(str)
                df_curr['學期'] = df_curr['學期'].astype(str)
                
                mask_curr = (df_curr['科別'] == dept) & (df_curr['學期'] == str(semester)) & (df_curr['年級'] == str(grade))
                target_curr = df_curr[mask_curr]

                for _, c_row in target_curr.iterrows():
                    c_name = c_row['課程名稱']
                    c_type = c_row['課程類別']
                    default_class = c_row.get('預設適用班級') or c_row.get('適用班級', '')

                    # 找 Submission 對應 (這裡只能用課程名稱 + 班級模糊比對，因為 Curriculum 沒有 UUID)
                    # 簡化邏輯：找出同名課程的所有 Submission
                    sub_matches = pd.DataFrame()
                    if not df_sub.empty:
                        mask_sub = (df_sub['科別'] == dept) & (df_sub['學期'] == str(semester)) & (df_sub['年級'] == str(grade)) & (df_sub['課程名稱'] == c_name)
                        sub_matches = df_sub[mask_sub]
                    
                    if not sub_matches.empty:
                         # 顯示所有找到的 Submission
                        for _, s_row in sub_matches.iterrows():
                            s_data = s_row.to_dict()
                            s_data['勾選'] = False
                            s_data['課程類別'] = c_type # 補回類別
                            display_rows.append(s_data)
                            displayed_uuids.add(s_data.get('uuid'))
                    else:
                        # 沒填報過 -> 顯示預設空白列
                        new_uuid = str(uuid.uuid4())
                        display_rows.append({
                            "勾選": False,
                            "uuid": new_uuid,
                            "科別": dept, "年級": grade, "學期": semester,
                            "課程類別": c_type, "課程名稱": c_name,
                            "適用班級": default_class,
                            "教科書(優先1)": "", "冊次(1)": "", "出版社(1)": "", "審定字號(1)": "",
                            "教科書(優先2)": "", "冊次(2)": "", "出版社(2)": "", "審定字號(2)": "",
                            "備註1": "", "備註2": ""
                        })

        # ==========================================
        # 共同階段：補上「自訂課程」(Orphans)
        # ==========================================
        # 找出 Submission 中，屬於此科別年級，但尚未被加入 display_rows 的 (即自訂課程或尚未對應到的)
        if not df_sub.empty:
            mask_orphan = (df_sub['科別'] == dept) & (df_sub['學期'] == str(semester)) & (df_sub['年級'] == str(grade))
            orphan_subs = df_sub[mask_orphan]
            
            for _, s_row in orphan_subs.iterrows():
                s_uuid = s_row.get('uuid')
                if s_uuid and s_uuid not in displayed_uuids:
                    s_data = s_row.to_dict()
                    s_data['勾選'] = False
                    s_data['課程類別'] = "自訂/新增"
                    display_rows.append(s_data)
                    displayed_uuids.add(s_uuid)

        # 轉成 DataFrame 並排序
        df_final = pd.DataFrame(display_rows)
        if not df_final.empty:
            # 確保欄位存在，避免顯示錯誤
            required_cols = ["勾選", "課程類別", "課程名稱", "適用班級", "教科書(優先1)", "冊次(1)", "出版社(1)", "審定字號(1)", "備註1"]
            for col in required_cols:
                if col not in df_final.columns:
                    df_final[col] = ""
            
            # 排序
            if '課程類別' in df_final.columns and '課程名稱' in df_final.columns:
                 df_final = df_final.sort_values(by=['課程類別', '課程名稱'], ascending=[False, True]).reset_index(drop=True)

        return df_final

    except Exception as e:
        st.error(f"讀取錯誤 (Detail): {e}")
        # print error traceback to console for debugging
        import traceback
        traceback.print_exc()
        return pd.DataFrame()
        
# --- 3. 取得課程列表 (保持不變) ---
def get_course_list():
    if 'data' in st.session_state and not st.session_state['data'].empty:
        return st.session_state['data']['課程名稱'].unique().tolist()
    return []

# --- 4. 存檔 (單筆寫入) ---
def save_single_row(row_data, original_key=None):
    client = get_connection()
    if not client: return False
    
    sh = client.open(SPREADSHEET_NAME)
    try:
        ws_sub = sh.worksheet(SHEET_SUBMISSION)
    except:
        # --- 新增備註1, 備註2 欄位 ---
        ws_sub = sh.add_worksheet(title=SHEET_SUBMISSION, rows=1000, cols=20)
        ws_sub.append_row(["uuid", "填報時間", "科別", "學期", "年級", "課程名稱", "教科書(1)", "冊次(1)", "出版社(1)", "字號(1)", "教科書(2)", "冊次(2)", "出版社(2)", "字號(2)", "適用班級", "備註1", "備註2"])

    all_values = ws_sub.get_all_values()
    if not all_values:
        # --- 確保無資料時，標題包含備註1, 備註2 ---
        headers = ["uuid", "填報時間", "科別", "學期", "年級", "課程名稱", "教科書(1)", "冊次(1)", "出版社(1)", "字號(1)", "教科書(2)", "冊次(2)", "出版社(2)", "字號(2)", "適用班級", "備註1", "備註2"]
        ws_sub.append_row(headers)
        all_values = [headers] 
    
    headers = all_values[0]
    
    if "uuid" not in headers:
        # 標頭不對時重寫
        ws_sub.clear() 
        headers = ["uuid", "填報時間", "科別", "學期", "年級", "課程名稱", "教科書(1)", "冊次(1)", "出版社(1)", "字號(1)", "教科書(2)", "冊次(2)", "出版社(2)", "字號(2)", "適用班級", "備註1", "備註2"]
        ws_sub.append_row(headers)
        all_values = [headers]

    col_map = {h: i for i, h in enumerate(headers)}
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    target_uuid = row_data.get('uuid')
    
    # --- 儲存備註1, 備註2 ---
    data_dict = {
        "uuid": target_uuid,
        "填報時間": timestamp,
        "科別": row_data['科別'], "學期": row_data['學期'], "年級": row_data['年級'], "課程名稱": row_data['課程名稱'],
        "教科書(1)": row_data['教科書(優先1)'], "冊次(1)": row_data['冊次(1)'], "出版社(1)": row_data['出版社(1)'], "字號(1)": row_data['審定字號(1)'],
        "教科書(2)": row_data['教科書(優先2)'], "冊次(2)": row_data['冊次(2)'], "出版社(2)": row_data['出版社(2)'], "字號(2)": row_data['審定字號(2)'],
        "適用班級": row_data['適用班級'], 
        "備註1": row_data.get('備註1', ''),
        "備註2": row_data.get('備註2', '')
    }
    
    row_to_write = []
    for h in headers:
        val = ""
        # 優先從 data_dict 尋找精確欄位
        if h in data_dict: val = data_dict[h]
        # 兼容舊版/不規範的欄位名稱
        elif h == "字號(1)": val = data_dict.get("字號(1)") or data_dict.get('審定字號(1)', '')
        elif h == "字號(2)": val = data_dict.get("字號(2)") or data_dict.get('審定字號(2)', '')
        elif h == "字號" or h == "審定字號": val = data_dict.get("字號(1)", "") # 應該不會用到，保留舊版邏輯
        elif h == "備註": val = data_dict.get("備註1", "") # 兼容舊版只有一個備註欄位的情況
        row_to_write.append(val)

    target_row_index = -1

    if target_uuid:
        uuid_col_idx = col_map.get("uuid")
        if uuid_col_idx is not None:
            for i in range(1, len(all_values)):
                if all_values[i][uuid_col_idx] == target_uuid:
                    target_row_index = i + 1
                    break

    if target_row_index > 0:
        start_col_char = 'A'
        # 計算結束欄位，避免寫入錯誤
        end_col_char = chr(ord('A') + len(headers) - 1) 
        if len(headers) > 26: end_col_char = 'Z' 

        range_name = f"{start_col_char}{target_row_index}:{end_col_char}{target_row_index}"
        ws_sub.update(range_name=range_name, values=[row_to_write])
    else:
        ws_sub.append_row(row_to_write)
        
    return True

# --- 4.5 刪除功能 (保持不變) ---
def delete_row_from_db(target_uuid):
    if not target_uuid: return False
    
    client = get_connection()
    if not client: return False
    sh = client.open(SPREADSHEET_NAME)
    try:
        ws_sub = sh.worksheet(SHEET_SUBMISSION)
    except:
        return False
        
    all_values = ws_sub.get_all_values()
    if not all_values: return False
    headers = all_values[0]
    
    if "uuid" not in headers: return False 
    uuid_idx = headers.index("uuid")
    
    target_row_index = -1
    for i in range(1, len(all_values)):
        if all_values[i][uuid_idx] == target_uuid:
            target_row_index = i + 1
            break
            
    if target_row_index > 0:
        ws_sub.delete_rows(target_row_index)
        return True
    return False
# --- 4.6 同步歷史資料到 Submission (新功能) ---
def sync_history_to_db(dept, semester, grade):
    """
    當勾選「載入歷史資料」且按下轉 PDF 時觸發。
    功能：找出 DB_History 有，但 Submission_Records 沒有的資料 (比對 UUID)，
    將這些資料直接寫入 Submission_Records。
    """
    client = get_connection()
    if not client: return False

    try:
        sh = client.open(SPREADSHEET_NAME)
        ws_hist = sh.worksheet(SHEET_HISTORY)
        ws_sub = sh.worksheet(SHEET_SUBMISSION)

        # 讀取 History
        data_hist = ws_hist.get_all_records() # 使用 records 比較方便取得 dict
        df_hist = pd.DataFrame(data_hist)
        
        # 讀取 Submission
        data_sub = ws_sub.get_all_records()
        df_sub = pd.DataFrame(data_sub)

        # 篩選當前科別/年級/學期
        if not df_hist.empty:
            df_hist['年級'] = df_hist['年級'].astype(str)
            df_hist['學期'] = df_hist['學期'].astype(str)
            target_hist = df_hist[
                (df_hist['科別'] == dept) & 
                (df_hist['學期'] == str(semester)) & 
                (df_hist['年級'] == str(grade))
            ]
        else:
            target_hist = pd.DataFrame()

        if target_hist.empty:
            return True # 沒歷史資料，不需要同步

        # 取得已存在的 UUID 集合
        existing_uuids = set()
        if not df_sub.empty:
            existing_uuids = set(df_sub['uuid'].astype(str).tolist())

        # 準備要寫入的 rows
        rows_to_append = []
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for _, row in target_hist.iterrows():
            h_uuid = str(row.get('uuid', '')).strip()
            
            # --- 穩健取值 (兼容舊欄位名) ---
            def get_val(keys):
                for k in keys:
                    if k in row and str(row[k]).strip():
                        return str(row[k]).strip()
                return ""

            if h_uuid and h_uuid not in existing_uuids:
                # 這是 History 有，但 Submission 沒有的 -> 準備寫入
                new_row = [
                    h_uuid,
                    timestamp,
                    row.get('科別', ''),
                    str(row.get('學期', '')),
                    str(row.get('年級', '')),
                    row.get('課程名稱', ''),
                    get_val(['教科書(優先1)', '教科書(1)', '教科書']),
                    get_val(['冊次(1)', '冊次']),
                    get_val(['出版社(1)', '出版社']),
                    get_val(['審定字號(1)', '字號(1)', '審定字號', '字號']),
                    get_val(['教科書(優先2)', '教科書(2)']),
                    get_val(['冊次(2)']),
                    get_val(['出版社(2)']),
                    get_val(['審定字號(2)', '字號(2)']),
                    row.get('適用班級', ''),
                    get_val(['備註1', '備註']),
                    get_val(['備註2'])
                ]
                rows_to_append.append(new_row)

        if rows_to_append:
            ws_sub.append_rows(rows_to_append)
            return True # 有更新
        
        return False # 無需更新

    except Exception as e:
        st.error(f"同步歷史資料失敗: {e}")
        return False

# --- 5. 產生 PDF 報表 (v4：橫向 + 字體10 + 校長核定框) ---
def create_pdf_report(dept):
    """
    從 Google Sheet 抓取該科別所有資料 (Submission_Records)，並使用 FPDF 生成 PDF 報表。
    返回 PDF 內容的 bytes。
    """
    
    # 定義字體名稱
    CHINESE_FONT = 'NotoSans' 
    
    # 內部類別用於自訂 PDF 頁首/頁尾
    class PDF(FPDF):
        def header(self):
            # 標題字體加大
            self.set_font(CHINESE_FONT, 'B', 18) 
            self.cell(0, 10, f'{dept} 114學年度 教科書選用總表', 0, 1, 'C')
            self.set_font(CHINESE_FONT, '', 10)
            self.cell(0, 5, f"列印時間：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}", 0, 1, 'R')
            self.ln(5)

        def footer(self):
            self.set_y(-15)
            self.set_font(CHINESE_FONT, 'I', 8)
            self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', 0, 0, 'C')
            
    # --- 1. 資料讀取與處理 ---
    client = get_connection()
    if not client: return None
    
    try:
        sh = client.open(SPREADSHEET_NAME)
        ws_sub = sh.worksheet(SHEET_SUBMISSION)
        data = ws_sub.get_all_values()
        if not data: return None
        
        headers = data[0]
        rows = data[1:]
        
        # 處理重複的欄位名稱 (需處理備註)
        seen = {}
        new_headers = []
        for col in headers:
            c = str(col).strip()
            if c in seen:
                seen[c] += 1
                new_name = f"{c}({seen[c]})"
                if c == '冊次': new_name = f"冊次({seen[c]})"
                elif c == '出版社': new_name = f"出版社({seen[c]})"
                elif c == '字號' or c == '審定字號': new_name = f"審定字號({seen[c]})"
                elif c == '教科書': new_name = f"教科書(優先{seen[c]})"
                elif c.startswith('備註'): new_name = c
                new_headers.append(new_name)
            else:
                seen[c] = 1
                if c == '教科書(1)': new_headers.append('教科書(優先1)')
                elif c == '教科書': new_headers.append('教科書(優先1)')
                elif c == '冊次': new_headers.append('冊次(1)')
                elif c == '出版社': new_headers.append('出版社(1)')
                elif c == '字號' or c == '審定字號': new_headers.append('審定字號(1)')
                elif c.startswith('備註'): new_headers.append(c)
                else: new_headers.append(c)
        
        df_full = pd.DataFrame(rows, columns=new_headers)

        if df_full.empty: return None

        df = df_full[df_full['科別'] == dept].copy()
        
        if df.empty: return None

        # 資料清洗與排序
        if '年級' in df.columns: df['年級'] = df['年級'].astype(str)
        if '學期' in df.columns: df['學期'] = df['學期'].astype(str)
        df = df.sort_values(by='填報時間')
        df = df.drop_duplicates(subset=['科別', '年級', '學期', '課程名稱', '適用班級'], keep='last')
        
    except Exception:
        return None
        
    # --- 2. PDF 生成 ---
    # 🌟 設定為橫向 (L)
    pdf = PDF(orientation='L', unit='mm', format='A4') 
    pdf.set_auto_page_break(auto=True, margin=15)
    
    try:
        pdf.add_font(CHINESE_FONT, '', 'NotoSansCJKtc-Regular.ttf', uni=True) 
        pdf.add_font(CHINESE_FONT, 'B', 'NotoSansCJKtc-Regular.ttf', uni=True) 
        pdf.add_font(CHINESE_FONT, 'I', 'NotoSansCJKtc-Regular.ttf', uni=True) 
    except Exception as e:
        st.warning(f"🚨 警告: 無法載入中文字體 ({e})。")
        CHINESE_FONT = 'Helvetica'
        
    pdf.add_page()
    
    # --- 🌟 欄位寬度調整 (橫向 A4 總寬約 297mm，扣邊距可用約 277mm) ---
    # 調整欄寬以容納 10pt 字體，並加入最後一欄「核定」
    # 總和: 30+65+45+12+22+28+55+18 = 275mm
    col_widths = [28, 73, 53, 11, 29, 38, 33, 11 ]
    
    col_names = [
        "課程名稱", "適用班級", 
        "教科書", "冊次", "出版社", "審定字號",
        "備註", "核定" # 新增欄位
    ]
    
    TOTAL_TABLE_WIDTH = sum(col_widths)
    
    def render_table_header(pdf):
        """繪製表格標頭"""
        # 標題字體加大到 11
        pdf.set_font(CHINESE_FONT, 'B', 12) 
        pdf.set_fill_color(220, 220, 220)
        start_x = pdf.get_x()
        start_y = pdf.get_y()
        for w, name in zip(col_widths, col_names):
            pdf.set_xy(start_x, start_y)
            pdf.multi_cell(w, 8, name, 1, 'C', 1) # 高度微調為 8
            start_x += w
        pdf.set_xy(pdf.l_margin, start_y + 8) 
        pdf.set_font(CHINESE_FONT, '', 12) # 🌟 內文改為 10pt
        
    # 依學期和年級分組繪製表格
    pdf.set_font(CHINESE_FONT, '', 12) # 🌟 內文改為 10pt
    
    # 因字體變大，行高需增加
    LINE_HEIGHT = 5.5 
    
    for sem in sorted(df['學期'].unique()):
        sem_df = df[df['學期'] == sem].copy()
        
        # 學期標頭
        pdf.set_font(CHINESE_FONT, 'B', 8)
        pdf.set_fill_color(200, 220, 255)
        pdf.cell(TOTAL_TABLE_WIDTH, 10, f"第 {sem} 學期", 1, 1, 'L', 1)
        
        # 依 年級 -> 課程名稱 排序
        if not sem_df.empty:
            sem_df = sem_df.sort_values(by=['年級', '課程名稱']) 
            
            render_table_header(pdf)

            for _, row in sem_df.iterrows():
                
                b1 = str(row.get('教科書(優先1)') or row.get('教科書(1)', '')).strip()
                v1 = str(row.get('冊次(1)', '')).strip()
                p1 = str(row.get('出版社(1)', '')).strip()
                c1 = str(row.get('審定字號(1)') or row.get('字號(1)', '')).strip()
                r1, r2 = safe_note(row)
                
                b2 = str(row.get('教科書(優先2)') or row.get('教科書(2)', '')).strip()
                v2 = str(row.get('冊次(2)', '')).strip()
                p2 = str(row.get('出版社(2)', '')).strip()
                c2 = str(row.get('審定字號(2)') or row.get('字號(2)', '')).strip()
                
                # 檢查是否有第二優先 (用於決定是否畫第二個勾選框)
                has_priority_2 = (b2 != "" or v2 != "")
                
                def format_combined_cell(val1, val2):
                    val1 = val1 if val1 else ""
                    val2 = val2 if val2 else ""
                    if not val1 and not val2: return ""
                    elif not val2: return val1
                    elif not val1: return val2
                    else: return f"{val1}\n{val2}"
                
                # 前7欄的資料
                data_row_to_write = [
                    str(row['課程名稱']),
                    str(row['適用班級']),
                    format_combined_cell(b1, b2), 
                    format_combined_cell(v1, v2), 
                    format_combined_cell(p1, p2), 
                    format_combined_cell(c1, c2), 
                    format_combined_cell(r1, r2)
                ]
                
                # --- 動態計算高度 ---
                pdf.set_font(CHINESE_FONT, '', 12) # 確保計算時用的是 10pt
                
                cell_line_counts = [] 
                
                for i, text in enumerate(data_row_to_write):
                    w = col_widths[i] # 對應寬度
                    segments = str(text).split('\n')
                    total_lines_for_cell = 0
                    
                    for seg in segments:
                        safe_width = w - 2
                        if safe_width < 1: safe_width = 1
                        txt_width = pdf.get_string_width(seg)
                        
                        if txt_width > 0:
                            lines_needed = math.ceil(txt_width / safe_width)
                        else:
                            lines_needed = 1 
                            if not seg and len(segments) == 1 and text == "": lines_needed = 0
                            
                        total_lines_for_cell += lines_needed
                    
                    if total_lines_for_cell < 1: total_lines_for_cell = 1
                    cell_line_counts.append(total_lines_for_cell)
                
                max_lines_in_row = max(cell_line_counts)
                
                # 如果有第2優先，高度至少要能容納2行，不然勾選框會擠在一起
                min_lines = 2 if has_priority_2 else 1
                if max_lines_in_row < min_lines: max_lines_in_row = min_lines

                calculated_height = max_lines_in_row * LINE_HEIGHT + 4 # 增加 padding
                row_height = max(calculated_height, 10.0) # 最小高度 10mm
                
                # --- 換頁檢查 ---
                if pdf.get_y() + row_height > pdf.page_break_trigger:
                    pdf.add_page()
                    pdf.set_font(CHINESE_FONT, 'B', 14)
                    pdf.set_fill_color(200, 220, 255)
                    pdf.cell(TOTAL_TABLE_WIDTH, 10, f"第 {sem} 學期 (續)", 1, 1, 'L', 1)
                    render_table_header(pdf)
                    
                # --- 繪製儲存格 ---
                start_x = pdf.get_x()
                start_y = pdf.get_y()
                
                # 1. 繪製前7欄 (文字資料)
                for i, text in enumerate(data_row_to_write):
                    w = col_widths[i]
                    
                    pdf.set_xy(start_x, start_y)
                    pdf.cell(w, row_height, "", 1, 0, 'L') # 畫框
                    
                    this_cell_content_height = cell_line_counts[i] * LINE_HEIGHT
                    y_pos = start_y + (row_height - this_cell_content_height) / 2
                    
                    pdf.set_xy(start_x, y_pos)
                    pdf.set_font(CHINESE_FONT, '', 12)
                    
                    align = 'C' if i == 3 else 'L' 
                    pdf.multi_cell(w, LINE_HEIGHT, str(text), 0, align, 0)
                        
                    start_x += w 
                
                # 2. 🌟 繪製最後一欄：核定 (勾選框)
                w_check = col_widths[7]
                pdf.set_xy(start_x, start_y)
                pdf.cell(w_check, row_height, "", 1, 0, 'L') # 畫框
                
                # 畫勾選方框 (大小 4mm)
                box_size = 4
                box_x = start_x + (w_check - box_size) / 2 - 2 # 稍微置中偏左
                
                # 第一優先的框 (位置在 row 上方 1/4 處)
                y_p1 = start_y + (row_height * 0.25) - (box_size / 2)
                pdf.rect(box_x, y_p1, box_size, box_size)
                # 標示 "1"
                pdf.set_xy(box_x + box_size + 1, y_p1)
                pdf.set_font(CHINESE_FONT, '', 8)
                pdf.cell(5, box_size, "1", 0, 0, 'L')
                
                # 如果有第二優先，畫第二個框 (位置在 row 下方 3/4 處)
                if has_priority_2:
                    y_p2 = start_y + (row_height * 0.75) - (box_size / 2)
                    pdf.rect(box_x, y_p2, box_size, box_size)
                    # 標示 "2"
                    pdf.set_xy(box_x + box_size + 1, y_p2)
                    pdf.cell(5, box_size, "2", 0, 0, 'L')

                # 移動到下一列
                pdf.set_y(start_y + row_height)
                    
            pdf.ln(5) 
    
    
    # 頁尾簽名區
    pdf.set_font(CHINESE_FONT, '', 12) # 頁尾字體也稍微加大
    pdf.ln(10)
    
    is_vocational = dept in DEPT_SPECIFIC_CONFIG
    footer_text = ["填表人：", "召集人：", "教務主任："]
    if is_vocational:
        footer_text.append("實習主任：")
    footer_text.append("校長：")
    
    cell_width = TOTAL_TABLE_WIDTH / len(footer_text)
    
    for text in footer_text:
        pdf.cell(cell_width, 12, text, 'B', 0, 'L')
    pdf.ln()

    return pdf.output(dest='S')
# --- 6. 班級計算邏輯 (核心修正區) ---
def get_all_possible_classes(grade):
    """取得該年級全校所有可能的班級"""
    prefix = {"1": "一", "2": "二", "3": "三"}.get(str(grade), "")
    if not prefix: return []
    classes = []
    for sys_name, suffixes in ALL_SUFFIXES.items():
        if str(grade) == "3" and sys_name == "建教班": continue
        for s in suffixes: classes.append(f"{prefix}{s}")
    return sorted(list(set(classes)))

def get_target_classes_for_dept(dept, grade, sys_name):
    """
    根據科別與學制，回傳「預設勾選」的班級。
    - 專業科系 (機械科)：只回傳該科系的班級 (機甲, 機乙)。
    - 共同科目 (資訊)：回傳該學制的全校班級 (機甲, 電甲, 建築...)。
    """
    prefix = {"1": "一", "2": "二", "3": "三"}.get(str(grade), "")
    if not prefix: return []
    
    suffixes = []
    if dept in DEPT_SPECIFIC_CONFIG:
        # 專業科系：只抓該科設定
        suffixes = DEPT_SPECIFIC_CONFIG[dept].get(sys_name, [])
    else:
        # 共同科目：抓全校該學制設定
        suffixes = ALL_SUFFIXES.get(sys_name, [])
        
    if str(grade) == "3" and sys_name == "建教班": return []
    return [f"{prefix}{s}" for s in suffixes]

# --- 7. Callbacks ---
def update_class_list_from_checkboxes():
    dept = st.session_state.get('dept_val')
    grade = st.session_state.get('grade_val')
    
    # 1. 取得目前已經選的 (避免覆蓋使用者手動加的)
    current_list = list(st.session_state.get('class_multiselect', []))
    current_set = set(current_list)

    # 2. 針對三個學制 Checkbox 進行增刪
    for sys_key, sys_name in [('cb_reg', '普通科'), ('cb_prac', '實用技能班'), ('cb_coop', '建教班')]:
        is_checked = st.session_state[sys_key]
        
        # 這裡會根據科別，回傳「該科班級」或「全校班級」
        target_classes = get_target_classes_for_dept(dept, grade, sys_name)
        
        if is_checked:
            # 勾選 -> 加入
            current_set.update(target_classes)
        else:
            # 取消 -> 移除
            # 注意：這裡只移除「該科別該學制」的班級，避免誤刪手動加的其他班級
            current_set.difference_update(target_classes)
    
    # 3. 更新結果到 active_classes 和 widget
    final_list = sorted(list(current_set))
    st.session_state['active_classes'] = final_list
    st.session_state['class_multiselect'] = final_list 

    # 連動全選
    if st.session_state['cb_reg'] and st.session_state['cb_prac'] and st.session_state['cb_coop']:
        st.session_state['cb_all'] = True
    else:
        st.session_state['cb_all'] = False

def toggle_all_checkboxes():
    # 修正 14: 在使用 st.session_state 之前，先檢查鍵是否存在 (在 main() 裡已初始化，這裡會安全)
    new_state = st.session_state['cb_all']
    st.session_state['cb_reg'] = new_state
    st.session_state['cb_prac'] = new_state
    st.session_state['cb_coop'] = new_state
    update_class_list_from_checkboxes()

def on_multiselect_change():
    st.session_state['active_classes'] = st.session_state['class_multiselect']

def on_editor_change():
    key = f"main_editor_{st.session_state['editor_key_counter']}"
    if key not in st.session_state: return

    edits = st.session_state[key]["edited_rows"]
    
    target_idx = None
    for idx, changes in edits.items():
        if "勾選" in changes and changes["勾選"] is True:
            target_idx = int(idx)
            break
            
    if target_idx is not None:
        st.session_state['data']["勾選"] = False
        st.session_state['data'].at[target_idx, "勾選"] = True
        st.session_state['edit_index'] = target_idx
        
        row_data = st.session_state['data'].iloc[target_idx]
        
        st.session_state['original_key'] = {
            '科別': row_data['科別'],
            '年級': str(row_data['年級']),
            '學期': str(row_data['學期']),
            '課程名稱': row_data['課程名稱'],
            '適用班級': str(row_data.get('適用班級', ''))
        }
        st.session_state['current_uuid'] = row_data.get('uuid')
        
        # --- 修正 6: 更新 form_data 結構，包含備註1/2 ---
        st.session_state['form_data'] = {
            'course': row_data["課程名稱"],
            'book1': row_data.get("教科書(優先1)", ""), 'vol1': row_data.get("冊次(1)", ""), 'pub1': row_data.get("出版社(1)", ""), 'code1': row_data.get("審定字號(1)", ""),
            'book2': row_data.get("教科書(優先2)", ""), 'vol2': row_data.get("冊次(2)", ""), 'pub2': row_data.get("出版社(2)", ""), 'code2': row_data.get("審定字號(2)", ""),
            # 確保從 dataframe 正確讀取 '備註1' 和 '備註2'
            'note1': row_data.get("備註1", ""), 
            'note2': row_data.get("備註2", "")
        }
        
        # 載入班級
        class_str = str(row_data.get("適用班級", ""))
        class_list = [c.strip() for c in class_str.replace("，", ",").split(",") if c.strip()]
        
        grade = st.session_state.get('grade_val')
        dept = st.session_state.get('dept_val')
        valid_classes = get_all_possible_classes(grade) if grade else []
        final_list = [c for c in class_list if c in valid_classes]
        
        st.session_state['active_classes'] = final_list
        st.session_state['class_multiselect'] = final_list

        # 反推 Checkbox
        st.session_state['cb_reg'] = False
        st.session_state['cb_prac'] = False
        st.session_state['cb_coop'] = False
        
        reg_targets = get_target_classes_for_dept(dept, grade, "普通科")
        prac_targets = get_target_classes_for_dept(dept, grade, "實用技能班")
        coop_targets = get_target_classes_for_dept(dept, grade, "建教班")
        
        if reg_targets and any(c in final_list for c in reg_targets): st.session_state['cb_reg'] = True
        if prac_targets and any(c in final_list for c in prac_targets): st.session_state['cb_prac'] = True
        if coop_targets and any(c in final_list for c in coop_targets): st.session_state['cb_coop'] = True
        
        st.session_state['cb_all'] = (st.session_state['cb_reg'] and st.session_state['cb_prac'] and st.session_state['cb_coop'])
    
    else:
        current_idx = st.session_state.get('edit_index')
        if current_idx is not None and str(current_idx) in edits:
            if edits[str(current_idx)].get("勾選") is False:
                st.session_state['data'].at[current_idx, "勾選"] = False
                st.session_state['edit_index'] = None
                st.session_state['original_key'] = None
                st.session_state['current_uuid'] = None

def auto_load_data():
    dept = st.session_state.get('dept_val')
    sem = st.session_state.get('sem_val')
    grade = st.session_state.get('grade_val')
    # 讀取 Checkbox 狀態
    use_history = st.session_state.get('use_history', False)
    
    if dept and sem and grade:
        # 傳入 use_history 參數
        df = load_data(dept, sem, grade, use_history)
        st.session_state['data'] = df
        st.session_state['loaded'] = True
        st.session_state['edit_index'] = None
        st.session_state['original_key'] = None
        st.session_state['current_uuid'] = None
        st.session_state['active_classes'] = []
        
        st.session_state['form_data'] = {
            'course': '', 'book1': '', 'vol1': '全', 'pub1': '', 'code1': '',
            'book2': '', 'vol2': '全', 'pub2': '', 'code2': '', 'note1': '', 'note2': ''
        }
        
        # 預設勾選 (保持原邏輯)
        if dept not in DEPT_SPECIFIC_CONFIG:
            st.session_state['cb_reg'] = True
            st.session_state['cb_prac'] = True
            st.session_state['cb_coop'] = True
            st.session_state['cb_all'] = True
        else:
            st.session_state['cb_reg'] = True
            st.session_state['cb_prac'] = False
            st.session_state['cb_coop'] = False
            st.session_state['cb_all'] = False
            
        update_class_list_from_checkboxes()
        st.session_state['editor_key_counter'] += 1

# --- 8. 主程式 ---
def main():
    st.set_page_config(page_title="教科書填報系統", layout="wide")
    
    # ... (CSS 保持不變) ...
    st.markdown("""
        <style>
        html, body, [class*="css"] { font-family: 'Segoe UI', sans-serif; }
        div[data-testid="stDataEditor"] { background-color: #ffffff !important; }
        div[data-testid="column"] button { margin-top: 1.5rem; }
        </style>
    """, unsafe_allow_html=True)

    # ... (Session State 初始化 保持不變) ...
    if 'edit_index' not in st.session_state: st.session_state['edit_index'] = None
    if 'current_uuid' not in st.session_state: st.session_state['current_uuid'] = None
    if 'active_classes' not in st.session_state: st.session_state['active_classes'] = []
    if 'form_data' not in st.session_state:
        st.session_state['form_data'] = {
            'course': '', 'book1': '', 'vol1': '全', 'pub1': '', 'code1': '',
            'book2': '', 'vol2': '全', 'pub2': '', 'code2': '', 'note1': '', 'note2': ''
        }
    if 'cb_all' not in st.session_state: st.session_state['cb_all'] = False
    if 'cb_reg' not in st.session_state: st.session_state['cb_reg'] = False
    if 'cb_prac' not in st.session_state: st.session_state['cb_prac'] = False
    if 'cb_coop' not in st.session_state: st.session_state['cb_coop'] = False
    if 'last_selected_row' not in st.session_state: st.session_state['last_selected_row'] = None
    if 'editor_key_counter' not in st.session_state: st.session_state['editor_key_counter'] = 0
    # 新增: 預設不使用歷史資料
    if 'use_history' not in st.session_state: st.session_state['use_history'] = False

    # ==========================================
    # 1. Sidebar 設定 (已修改)
    # ==========================================
    with st.sidebar:
        st.header("1. 填報設定")
        dept_options = [
            "建築科", "機械科", "電機科", "製圖科", "室設科", 
            "國文科", "英文科", "數學科", "自然科", "社會科", 
            "資訊科技", "體育科", "國防科", "藝術科", "健護科", "輔導科", "閩南語"
        ]
        
        dept = st.selectbox("科別", dept_options, key='dept_val', on_change=auto_load_data)
        col1, col2 = st.columns(2)
        with col1: sem = st.selectbox("學期", ["1", "2", "寒", "暑"], key='sem_val', on_change=auto_load_data)
        with col2: grade = st.selectbox("年級", ["1", "2", "3"], key='grade_val', on_change=auto_load_data)
        
        # --- 修改處: 改為 Checkbox ---
        st.checkbox("載入歷史資料 (113學年)", key='use_history', on_change=auto_load_data)
        st.caption("勾選後將載入去年資料。若未勾選，則載入預設課程表。")

    # ==========================================
    # 2. 頂部區域：標題 + PDF 按鈕 (已修改)
    # ==========================================
    top_col1, top_col2 = st.columns([4, 1])
    
    with top_col1:
        st.title("📚 教科書填報系統")
        
    with top_col2:
        if st.button("📄 轉 PDF 報表 (下載)", type="primary", use_container_width=True):
            if dept:
                with st.spinner(f"正在處理 {dept} PDF..."):
                    # --- 修改處: PDF 同步邏輯 ---
                    if st.session_state.get('use_history'):
                        st.info("正在同步歷史資料到填報紀錄...")
                        # 呼叫同步函式
                        sync_success = sync_history_to_db(dept, sem, grade)
                        if sync_success:
                            st.success("✅ 歷史資料已同步寫入！")
                    
                    # 接著產生 PDF (這會去讀取 Submission Records，剛同步完的資料也會被讀到)
                    pdf_report_bytes = create_pdf_report(dept)
                    
                    if pdf_report_bytes is not None:
                        b64_bytes = base64.b64encode(pdf_report_bytes)
                        b64 = b64_bytes.decode('latin-1') 
                        href = f'<a href="data:application/pdf;base64,{b64}" download="{dept}_教科書總表.pdf" style="text-decoration:none; color:white; background-color:#b31412; padding:8px 12px; border-radius:5px; font-weight:bold; font-size:14px; display:block; text-align:center;">⬇️ 點此下載 PDF</a>'
                        st.markdown(href, unsafe_allow_html=True)
                    else:
                        st.error("生成失敗，請檢查資料。")
            else:
                st.warning("請先選擇科別")

    # ... (後面 3. 資料載入與 Data Editor 部分保持不變，因為 auto_load_data 已經更新了 session_state['data']) ...
    if 'loaded' not in st.session_state and dept and sem and grade:
        auto_load_data()

    if st.session_state.get('loaded'):
        # ... (Sidebar 編輯區塊保持不變) ...
        # ... (Data Editor 區塊保持不變) ...
        # (這裡複製您原有的 main 下半部程式碼即可，從 `with st.sidebar:` 的編輯功能開始到結束)
        # 為了節省篇幅，請保留您原本在 main 下方的程式碼
        pass
if __name__ == "__main__":
    main()




