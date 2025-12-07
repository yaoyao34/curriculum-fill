import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import datetime
import json
import base64
import uuid

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

# --- 2. 資料讀取 ---
# --- 2. 資料讀取 (修正版：解決重複顯示與班級對應問題) ---
def load_data(dept, semester, grade):
    client = get_connection()
    if not client: return pd.DataFrame()
    try:
        sh = client.open(SPREADSHEET_NAME)
        ws_curr = sh.worksheet(SHEET_CURRICULUM)
        ws_hist = sh.worksheet(SHEET_HISTORY)
        ws_sub = sh.worksheet(SHEET_SUBMISSION)
        
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

        df_curr = get_df(ws_curr)
        df_hist = get_df(ws_hist)
        df_sub = get_df(ws_sub)
        
        for df in [df_curr, df_hist, df_sub]:
            if not df.empty:
                if '年級' in df.columns: df['年級'] = df['年級'].astype(str)
                if '學期' in df.columns: df['學期'] = df['學期'].astype(str)
                
    except Exception as e:
        st.error(f"讀取錯誤: {e}")
        return pd.DataFrame()

    mask_curr = (df_curr['科別'] == dept) & (df_curr['學期'] == str(semester)) & (df_curr['年級'] == str(grade))
    target_courses = df_curr[mask_curr]

    if target_courses.empty:
        return pd.DataFrame()

    display_rows = []
    
    # 🌟 新增：用來記錄已經顯示過的 Submission UUID，防止同一筆填報紀錄出現兩次
    displayed_uuids = set()
    
    # --- 輔助函式 ---
    def safe_get_value(row, key, default=''):
        val = row.get(key, default)
        if isinstance(val, pd.Series):
            try:
                val = val.iloc[0]
            except IndexError:
                val = default
        return str(val).strip()

    for _, row in target_courses.iterrows():
        c_name = row['課程名稱']
        c_type = row['課程類別']
        # 取得這一列原本預設給哪個班級 (例如: 一建築)
        default_class = row.get('預設適用班級', '').strip() 
        
        # 1. 先找 Submission (填報紀錄)
        sub_matches = pd.DataFrame()
        if not df_sub.empty:
            mask_sub = (df_sub['科別'] == dept) & (df_sub['學期'] == str(semester)) & (df_sub['年級'] == str(grade)) & (df_sub['課程名稱'] == c_name)
            sub_matches = df_sub[mask_sub]

        # 標記：這一列 Curriculum 是否已經被某個 Submission 覆蓋了解決？
        is_covered_by_submission = False

        if not sub_matches.empty:
            for _, s_row in sub_matches.iterrows():
                s_uuid = s_row.get('uuid', str(uuid.uuid4()))
                s_classes = safe_get_value(s_row, '適用班級')
                
                # 🌟 關鍵邏輯修正：
                # 只有當「填報紀錄的適用班級」包含了「這列 Curriculum 的預設班級」時，才視為匹配。
                # 例如：Loop跑到「一建築」時，填報資料「一建築」會匹配 -> 顯示。
                #      Loop跑到「一營造」時，填報資料「一建築」不匹配 -> 不顯示，程式會往下走去顯示「一營造」的預設值。
                
                # 使用簡單的字串包含檢查 (若班級名稱有重疊風險如 '機甲', '機甲乙'，建議改用 split 後檢查)
                if default_class in s_classes:
                    is_covered_by_submission = True
                    
                    # 🌟 避免重複顯示：如果這個 UUID 已經顯示過了，就不再 add 到 display_rows
                    if s_uuid not in displayed_uuids:
                        備註1_val = safe_get_value(s_row, '備註1')
                        備註2_val = safe_get_value(s_row, '備註2')

                        display_rows.append({
                            "勾選": False,
                            "uuid": s_uuid, 
                            "科別": dept, "年級": grade, "學期": semester,
                            "課程類別": c_type, "課程名稱": c_name,
                            "適用班級": s_classes, # 顯示填報的班級
                            "教科書(優先1)": safe_get_value(s_row, '教科書(優先1)') or safe_get_value(s_row, '教科書(1)'), 
                            "冊次(1)": safe_get_value(s_row, '冊次(1)'), 
                            "出版社(1)": safe_get_value(s_row, '出版社(1)'), 
                            "審定字號(1)": safe_get_value(s_row, '審定字號(1)') or safe_get_value(s_row, '字號(1)'),
                            "教科書(優先2)": safe_get_value(s_row, '教科書(優先2)') or safe_get_value(s_row, '教科書(2)'), 
                            "冊次(2)": safe_get_value(s_row, '冊次(2)'), 
                            "出版社(2)": safe_get_value(s_row, '出版社(2)'), 
                            "審定字號(2)": safe_get_value(s_row, '審定字號(2)') or safe_get_value(s_row, '字號(2)'),
                            "備註1": 備註1_val, 
                            "備註2": 備註2_val
                        })
                        displayed_uuids.add(s_uuid)

        # 2. 如果沒有被 Submission 覆蓋，才去找 History 或顯示 Default
        if not is_covered_by_submission:
            hist_matches = df_hist[df_hist['課程名稱'] == c_name]
            target_rows = pd.DataFrame()

            if not hist_matches.empty:
                # 這裡原本邏輯就是找 exact match，所以通常不會有重複問題
                exact_match = hist_matches[hist_matches['適用班級'] == default_class]
                target_rows = exact_match if not exact_match.empty else hist_matches

            if not target_rows.empty:
                for _, h_row in target_rows.iterrows():
                    # 這裡也要稍微防呆，確認一下這筆歷史資料是不是真的跟當前預設班級有關
                    # 但因為 DB_History 結構通常較單純，這裡維持原樣即可
                    
                    備註1_val = safe_get_value(h_row, '備註1')
                    備註2_val = safe_get_value(h_row, '備註2')

                    display_rows.append({
                        "勾選": False,
                        "uuid": str(uuid.uuid4()), 
                        "科別": dept, "年級": grade, "學期": semester,
                        "課程類別": c_type, "課程名稱": c_name,
                        "適用班級": h_row.get('適用班級', default_class),
                        "教科書(優先1)": h_row.get('教科書(優先1)', ''), "冊次(1)": h_row.get('冊次(1)', ''), "出版社(1)": h_row.get('出版社(1)', ''), "審定字號(1)": h_row.get('審定字號(1)', ''),
                        "教科書(優先2)": h_row.get('教科書(優先2)', ''), "冊次(2)": h_row.get('冊次(2)', ''), "出版社(2)": h_row.get('出版社(2)', ''), "審定字號(2)": h_row.get('審定字號(2)', ''),
                        "備註1": 備註1_val,
                        "備註2": 備註2_val
                    })
            else:
                # 3. 完全沒有資料，顯示預設空白列
                display_rows.append({
                    "勾選": False,
                    "uuid": str(uuid.uuid4()), 
                    "科別": dept, "年級": grade, "學期": semester,
                    "課程類別": c_type, "課程名稱": c_name,
                    "適用班級": default_class,
                    "教科書(優先1)": "", "冊次(1)": "", "出版社(1)": "", "審定字號(1)": "",
                    "教科書(優先2)": "", "冊次(2)": "", "出版社(2)": "", "審定字號(2)": "",
                    "備註1": "", "備註2": ""
                })

    return pd.DataFrame(display_rows)

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

# --- 5. 產生 PDF 報表 ---
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
            # 使用已註冊的字體
            self.set_font(CHINESE_FONT, 'B', 16) 
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
                # --- 處理備註欄位名稱 (與 load_data 邏輯一致) ---
                #elif c == '備註' or c.startswith('備註'): new_name = f"備註{seen[c]}"
                elif c.startswith('備註'): new_name = c
                new_headers.append(new_name)
            else:
                seen[c] = 1
                if c == '教科書(1)': new_headers.append('教科書(優先1)')
                elif c == '教科書': new_headers.append('教科書(優先1)')
                elif c == '冊次': new_headers.append('冊次(1)')
                elif c == '出版社': new_headers.append('出版社(1)')
                elif c == '字號' or c == '審定字號': new_headers.append('審定字號(1)')
                # --- 處理備註欄位名稱 ---
                #elif c == '備註' or c.startswith('備註'): new_headers.append('備註1')
                elif c.startswith('備註'): new_headers.append(c)
                else: new_headers.append(c)
        
        df_full = pd.DataFrame(rows, columns=new_headers)
        #st.write("✅ PDF 欄位實際名稱：", df_full.columns.tolist())

        
        if df_full.empty: return None

        df = df_full[df_full['科別'] == dept].copy()
        
        if df.empty: return None

        # 資料清洗與排序 (僅保留最新的填報紀錄)
        if '年級' in df.columns: df['年級'] = df['年級'].astype(str)
        if '學期' in df.columns: df['學期'] = df['學期'].astype(str)
        df = df.sort_values(by='填報時間')
        df = df.drop_duplicates(subset=['科別', '年級', '學期', '課程名稱', '適用班級'], keep='last')
        
    except Exception:
        return None
        
    # --- 2. PDF 生成 ---
    pdf = PDF(orientation='L', unit='mm', format='A4') # 橫向 A4
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # 註冊中文字體 - 這是解決中文顯示的關鍵步驟
    try:
        # 假設您的中文字體檔名為 NotoSansCJKtc-Regular.ttf (請確保此文件已上傳至專案根目錄)
        pdf.add_font(CHINESE_FONT, '', 'NotoSansCJKtc-Regular.ttf', uni=True) 
        pdf.add_font(CHINESE_FONT, 'B', 'NotoSansCJKtc-Regular.ttf', uni=True) 
        pdf.add_font(CHINESE_FONT, 'I', 'NotoSansCJKtc-Regular.ttf', uni=True) 
    except Exception as e:
        # 如果找不到字體，退回到 Helvetica，但中文會無法顯示
        st.warning(f"🚨 警告: 無法載入中文字體 NotoSansCJKtc-Regular.ttf ({e})。中文將無法顯示。請確保檔案已存在。")
        CHINESE_FONT = 'Helvetica'
        
    pdf.add_page()
    
    # --- 欄位與寬度重新定義 (總寬度 259mm) ---
    col_widths = [30, 79, 40, 15, 25, 35, 35] 
    col_names = [
        "課程名稱", "適用班級", 
        "教科書", "冊次", "出版社", "審定字號",
        "備註 (作者/單價)" 
    ]
    
    TOTAL_TABLE_WIDTH = sum(col_widths)
    
    def render_table_header(pdf):
        """繪製表格標頭，支援 MultiCell 換行"""
        pdf.set_font(CHINESE_FONT, 'B', 9) 
        pdf.set_fill_color(220, 220, 220)
        start_x = pdf.get_x()
        start_y = pdf.get_y()
        # 使用 MultiCell 繪製標頭
        for w, name in zip(col_widths, col_names):
            pdf.set_xy(start_x, start_y)
            pdf.multi_cell(w, 7, name, 1, 'C', 1) 
            start_x += w
        pdf.set_xy(pdf.l_margin, start_y + 7) # 移至下一行
        pdf.set_font(CHINESE_FONT, '', 8) # 切回內文文字
        
    # 依學期和年級分組繪製表格
    pdf.set_font(CHINESE_FONT, '', 8)
    
    for sem in sorted(df['學期'].unique()):
        sem_df = df[df['學期'] == sem].copy()
        
        # 學期標頭
        pdf.set_font(CHINESE_FONT, 'B', 12)
        pdf.set_fill_color(200, 220, 255)
        # FIX: 限制標題寬度為表格總寬度 (259mm)
        pdf.cell(TOTAL_TABLE_WIDTH, 8, f"第 {sem} 學期", 1, 1, 'L', 1)
        
        # 依 年級 -> 課程名稱 排序
        if not sem_df.empty:
            sem_df = sem_df.sort_values(by=['年級', '課程名稱']) 
            
            render_table_header(pdf)

            for _, row in sem_df.iterrows():
                
                # --- 修正 9: 確保所有取出的數據都轉換為 str()，並去除空白，避免 Pandas Series 輸出 ---
                b1 = str(row.get('教科書(優先1)') or row.get('教科書(1)', '')).strip()
                v1 = str(row.get('冊次(1)', '')).strip()
                p1 = str(row.get('出版社(1)', '')).strip()
                c1 = str(row.get('審定字號(1)') or row.get('字號(1)', '')).strip()
                # 備註欄位：確保只從 DF 中取出值
                r1, r2 = safe_note(row)
                
                b2 = str(row.get('教科書(優先2)') or row.get('教科書(2)', '')).strip()
                v2 = str(row.get('冊次(2)', '')).strip()
                p2 = str(row.get('出版社(2)', '')).strip()
                c2 = str(row.get('審定字號(2)') or row.get('字號(2)', '')).strip()
                #r2 = safe_note(row[note_cols[1]])
                
                # 輔助函式：只在兩行內容皆不為空時使用 \n，並避免空行
                def format_combined_cell(val1, val2):
                    # 確保所有輸入都是非空字串
                    val1 = val1 if val1 else ""
                    val2 = val2 if val2 else ""
                    
                    if not val1 and not val2:
                        return ""
                    elif not val2:
                        return val1
                    elif not val1:
                        return val2
                    else:
                        return f"{val1}\n{val2}"
                
                data_row_to_write = [
                    str(row['課程名稱']),
                    str(row['適用班級']),
                    format_combined_cell(b1, b2), # 教科書 [2]
                    format_combined_cell(v1, v2), # 冊次 [3]
                    format_combined_cell(p1, p2), # 出版社 [4]
                    format_combined_cell(c1, c2), # 審定字號 [5]
                    format_combined_cell(r1, r2) # 備註 (作者/單價) [6]
                ]
                
                # 1. 計算最大行高 (用於 MultiCell 換行)
                pdf.set_font(CHINESE_FONT, '', 8)
                
                # 基準行高為兩行的高度 (適用於合併欄位: 4.0mm * 2 + 1mm 邊距 = 9mm)
                base_height = 9.0 
                
                # 計算適用班級行高 (適用班級是第 2 欄，索引 1)
                class_width = col_widths[1]
                class_text = str(data_row_to_write[1])
                class_height = 4.5
                if class_text:
                    # 估算行數 (每行文字寬度 * 0.9 留白)
                    num_lines_class = pdf.get_string_width(class_text) // (class_width * 0.9) + 1
                    class_height = num_lines_class * 4.5
                
                # 行高取 合併欄位基準高度、適用班級行高、以及最小高度 7.0 的最大值
                row_height = max(base_height, class_height, 7.0) 
                
                # 2. 檢查是否需要換頁
                if pdf.get_y() + row_height > pdf.page_break_trigger:
                    pdf.add_page()
                    pdf.set_font(CHINESE_FONT, 'B', 12)
                    pdf.set_fill_color(200, 220, 255)
                    pdf.cell(TOTAL_TABLE_WIDTH, 8, f"第 {sem} 學期 (續)", 1, 1, 'L', 1)
                    render_table_header(pdf)
                    
                # 3. 繪製儲存格
                start_x = pdf.get_x()
                start_y = pdf.get_y()
                
                for i, (w, text) in enumerate(zip(col_widths, data_row_to_write)):
                    
                    # 繪製單元格邊框/背景
                    pdf.set_xy(start_x, start_y)
                    pdf.cell(w, row_height, "", 1, 0, 'L')
                    
                    # 寫入內容
                    pdf.set_font(CHINESE_FONT, '', 8)
                    
                    if i in [2, 3, 4, 5, 6]: # 教科書, 冊次, 出版社, 審定字號, 備註 (兩行合併欄位)
                        # 讓兩行內容垂直置中 (y_pos 調整)
                        y_offset = (row_height - base_height) / 2 + 0.5
                        pdf.set_xy(start_x, start_y + y_offset)
                        
                        align = 'C' if i == 3 else 'L' # 冊次居中，其他靠左
                        
                        # 使用 MultiCell，每行 4.0mm 高度
                        pdf.multi_cell(w, 4.0, str(text), 0, align, 0)
                    else: # 課程名稱[0], 適用班級[1] (單行/多行，垂直置中)
                        
                        # 計算垂直置中位置
                        num_lines_in_cell = (pdf.get_string_width(str(text)) // (w * 0.9) + 1)
                        y_pos = start_y + (row_height - num_lines_in_cell * 4.5) / 2
                        pdf.set_xy(start_x, y_pos) 
                        
                        align = 'L'
                        pdf.multi_cell(w, 4.5, str(text), 0, align, 0)
                        
                    # 手動移動 X 座標
                    start_x += w 
                
                # 移動 Y 座標到下一行
                    pdf.set_y(start_y + row_height)
                    
            pdf.ln(5) 
    
    
    # 頁尾簽名區
    pdf.set_font(CHINESE_FONT, '', 10)
    pdf.ln(10)
    
    is_vocational = dept in DEPT_SPECIFIC_CONFIG
    footer_text = ["填表人：", "召集人：", "教務主任："]
    if is_vocational:
        footer_text.append("實習主任：")
    footer_text.append("校長：")
    
    # 使用表格總寬度來計算簽名欄位寬度
    cell_width = TOTAL_TABLE_WIDTH / len(footer_text)
    
    for text in footer_text:
        pdf.cell(cell_width, 10, text, 'B', 0, 'L')
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
    
    if dept and sem and grade:
        df = load_data(dept, sem, grade)
        st.session_state['data'] = df
        st.session_state['loaded'] = True
        st.session_state['edit_index'] = None
        st.session_state['original_key'] = None
        st.session_state['current_uuid'] = None
        st.session_state['active_classes'] = []
        
        # --- 修正 7: 完整初始化 form_data ---
        st.session_state['form_data'] = {
            'course': '', 'book1': '', 'vol1': '全', 'pub1': '', 'code1': '',
            'book2': '', 'vol2': '全', 'pub2': '', 'code2': '', 'note1': '', 'note2': ''
        }
        
        # 預設勾選
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
    st.title("📚 教科書填報系統")

    st.markdown("""
        <style>
        html, body, [class*="css"] { font-family: 'Segoe UI', sans-serif; }
        div[data-testid="stDataEditor"] { background-color: #ffffff !important; }
        div[data-testid="stDataEditor"] table td {
            font-size: 18px !important;
            color: #000000 !important;
            background-color: #ffffff !important;
            white-space: pre-wrap !important;
            word-wrap: break-word !important;
            vertical-align: top !important;
            height: auto !important;
            min-height: 60px !important;
            line-height: 1.6 !important;
            border-bottom: 1px solid #e0e0e0 !important;
            opacity: 1 !important;
        }
        div[data-testid="stDataEditor"] table td[aria-disabled="true"],
        div[data-testid="stDataEditor"] table td[data-disabled="true"] {
            color: #000000 !important; 
            -webkit-text-fill-color: #000000 !important;
            background-color: #ffffff !important;
            opacity: 1 !important;
        }
        div[data-testid="stDataEditor"] table th {
            font-size: 18px !important;
            font-weight: bold !important;
            background-color: #333333 !important;
            color: #ffffff !important;
            border-bottom: 2px solid #000000 !important;
        }
        thead tr th:first-child { display: none }
        tbody th { display: none }
        </style>
    """, unsafe_allow_html=True)

    # 🚨 修正 1: 在應用程式啟動時，預先初始化所有 Session State 鍵
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
        
        if st.button("🔄 手動重載", type="secondary", use_container_width=True):
            auto_load_data()

    if 'loaded' not in st.session_state and dept and sem and grade:
        auto_load_data()

    if st.session_state.get('loaded'):
        
        with st.sidebar:
            st.divider()
            is_edit_mode = st.session_state['edit_index'] is not None
            header_text = f"2. 修改第 {st.session_state['edit_index'] + 1} 列" if is_edit_mode else "2. 新增/插入課程"
            st.subheader(header_text)
            
            # 刪除按鈕
            if is_edit_mode:
                c_cancel, c_del = st.columns([1, 1])
                with c_cancel:
                    if st.button("❌ 取消", type="secondary"):
                        st.session_state['edit_index'] = None
                        st.session_state['current_uuid'] = None
                        st.session_state['data']["勾選"] = False
                        st.session_state['editor_key_counter'] += 1
                        st.rerun()
                with c_del:
                    if st.button("🗑️ 刪除此列", type="primary"):
                        idx = st.session_state['edit_index']
                        uuid_to_del = st.session_state.get('current_uuid')
                        
                        with st.spinner("同步資料庫..."):
                            if uuid_to_del:
                                delete_row_from_db(uuid_to_del)
                        
                        st.session_state['data'] = st.session_state['data'].drop(idx).reset_index(drop=True)
                        st.session_state['edit_index'] = None
                        st.session_state['current_uuid'] = None
                        st.session_state['active_classes'] = []
                        # 清空 form_data
                        st.session_state['form_data'] = {k: '' for k in st.session_state['form_data']}
                        st.session_state['form_data']['vol1'] = '全'
                        st.session_state['form_data']['vol2'] = '全'
                        st.session_state['editor_key_counter'] += 1
                        
                        st.success("已刪除！")
                        st.rerun()

            current_form = st.session_state['form_data']

            course_list = get_course_list()
            course_index = 0
            if is_edit_mode and current_form['course'] in course_list:
                course_index = course_list.index(current_form['course'])
            
            if course_list:
                input_course = st.selectbox("選擇課程", course_list, index=course_index)
            else:
                input_course = st.text_input("課程名稱", value=current_form['course'])
            
            st.markdown("**第一優先**")
            # --- Streamlit 側邊欄調整：書名、冊次/出版社 分兩行 ---
            input_book1 = st.text_input("書名", value=current_form['book1'])
            bc1, bc2 = st.columns([1, 2])
            vol_opts = ["全", "上", "下", "I", "II", "III", "IV", "V", "VI"]
            vol1_idx = vol_opts.index(current_form['vol1']) if current_form['vol1'] in vol_opts else 0
            with bc1: input_vol1 = st.selectbox("冊次", vol_opts, index=vol1_idx)
            with bc2: input_pub1 = st.text_input("出版社", value=current_form['pub1'])
            
            # 審定字號 和 備註 (優先1) 在同一列
            c_code1, c_note1 = st.columns(2)
            with c_code1: input_code1 = st.text_input("審定字號", value=current_form['code1']) 
            with c_note1: input_note1 = st.text_input("備註1(作者/單價)", value=current_form['note1']) 


            st.markdown("**第二優先**")
            input_book2 = st.text_input("備選書名", value=current_form['book2'])
            bc3, bc4 = st.columns([1, 2])
            vol2_idx = vol_opts.index(current_form['vol2']) if current_form['vol2'] in vol_opts else 0
            with bc3: input_vol2 = st.selectbox("冊次(2)", vol_opts, index=vol2_idx)
            with bc4: input_pub2 = st.text_input("出版社(2)", value=current_form['pub2'])

            # 審定字號(2) 和 備註(優先2) 在同一列
            c_code2, c_note2 = st.columns(2)
            with c_code2: input_code2 = st.text_input("審定字號(2)", value=current_form['code2']) 
            with c_note2: input_note2 = st.text_input("備註2(作者/單價)", value=current_form['note2'])

            
            st.markdown("##### 適用班級")
            st.caption("👇 勾選學制 (勾'全部'選全校)")
            
            c_all, c1, c2, c3 = st.columns([1, 1, 1, 1])
            with c_all: st.checkbox("全部", key="cb_all", on_change=toggle_all_checkboxes)
            with c1: st.checkbox("普通", key="cb_reg", on_change=update_class_list_from_checkboxes)
            with c2: st.checkbox("實技", key="cb_prac", on_change=update_class_list_from_checkboxes)
            with c3: st.checkbox("建教", key="cb_coop", on_change=update_class_list_from_checkboxes)
            
            st.caption("👇 點選加入其他班級")
            all_possible = get_all_possible_classes(grade)
            
            # 關鍵修正：Multiselect 選項必須包含當前選中的班級，否則會報錯
            final_options = sorted(list(set(all_possible + st.session_state['active_classes'])))
            
            selected_classes = st.multiselect(
                "最終班級列表:",
                options=final_options,
                default=st.session_state['active_classes'],
                key="class_multiselect",
                on_change=on_multiselect_change
            )
            
            input_class_str = ",".join(selected_classes)
            # 移除舊版 input_note

            if is_edit_mode:
                if st.button("🔄 更新表格 (存檔)", type="primary", use_container_width=True):
                    # 班級必填檢查
                    if not input_class_str or not input_book1 or not input_pub1 or not input_vol1:
                        st.error("⚠️ 適用班級、第一優先書名、冊次、出版社為必填！")
                    else:
                        idx = st.session_state['edit_index']
                        current_uuid = st.session_state.get('current_uuid')
                        
                        if not current_uuid:
                            current_uuid = str(uuid.uuid4())
                            
                        new_row = {
                            "uuid": current_uuid,
                            "科別": dept, "年級": grade, "學期": sem,
                            "課程類別": "部定必修", 
                            "課程名稱": input_course,
                            "教科書(優先1)": input_book1, "冊次(1)": input_vol1, "出版社(1)": input_pub1, "審定字號(1)": input_code1,
                            "教科書(優先2)": input_book2, "冊次(2)": input_vol2, "出版社(2)": input_pub2, "審定字號(2)": input_code2,
                            "適用班級": input_class_str,
                            "備註1": input_note1, # 存入備註1
                            "備註2": input_note2  # 存入備註2
                        }

                        with st.spinner("正在寫入資料庫..."):
                            save_single_row(new_row, st.session_state.get('original_key'))

                        for k, v in new_row.items():
                            if k in st.session_state['data'].columns:
                                st.session_state['data'].at[idx, k] = v
                        st.session_state['data'].at[idx, "勾選"] = False

                        # 清空 form_data
                        st.session_state['form_data'] = {k: '' for k in st.session_state['form_data']}
                        st.session_state['form_data']['vol1'] = '全'
                        st.session_state['form_data']['vol2'] = '全'
                        st.session_state['active_classes'] = []
                        
                        st.session_state['edit_index'] = None
                        st.session_state['original_key'] = None
                        st.session_state['current_uuid'] = None
                        st.session_state['editor_key_counter'] += 1 
                        
                        st.success("✅ 更新並存檔成功！")
                        st.rerun()
            else:
                if st.button("➕ 加入表格 (存檔)", type="primary", use_container_width=True):
                    # 班級必填檢查
                    if not input_class_str or not input_book1 or not input_pub1 or not input_vol1:
                        st.error("⚠️ 適用班級、第一優先書名、冊次、出版社為必填！")
                    else:
                        new_uuid = str(uuid.uuid4())
                        new_row = {
                            "勾選": False,
                            "uuid": new_uuid,
                            "科別": dept, "年級": grade, "學期": sem,
                            "課程類別": "部定必修", 
                            "課程名稱": input_course,
                            "教科書(優先1)": input_book1, "冊次(1)": input_vol1, "出版社(1)": input_pub1, "審定字號(1)": input_code1,
                            "教科書(優先2)": input_book2, "冊次(2)": input_vol2, "出版社(2)": input_pub2, "審定字號(2)": input_code2,
                            "適用班級": input_class_str,
                            "備註1": input_note1, # 存入備註1
                            "備註2": input_note2  # 存入備註2
                        }
                        
                        with st.spinner("正在寫入資料庫..."):
                            save_single_row(new_row, None) # 新增無 key
                            
                        st.session_state['data'] = pd.concat([st.session_state['data'], pd.DataFrame([new_row])], ignore_index=True)
                        st.session_state['editor_key_counter'] += 1
                        
                        # 清空 form_data
                        st.session_state['form_data'] = {k: '' for k in st.session_state['form_data']}
                        st.session_state['form_data']['vol1'] = '全'
                        st.session_state['form_data']['vol2'] = '全'
                        st.session_state['active_classes'] = []
                        
                        st.success(f"✅ 已存檔：{input_course}")
                        st.rerun()

        st.success(f"目前編輯：**{dept}** / **{grade}年級** / **第{sem}學期**")
        
        # --- 修正 10: 調整 Streamlit data_editor 的欄寬配置 ---
        edited_df = st.data_editor(
            st.session_state['data'],
            num_rows="dynamic",
            use_container_width=True,
            height=600,
            key=f"main_editor_{st.session_state['editor_key_counter']}",
            on_change=on_editor_change,
            column_config={
                "勾選": st.column_config.CheckboxColumn("勾選", width="small", disabled=False),
                "uuid": None,
                "科別": None, 
                "年級": None, 
                "學期": None,
                "課程類別": st.column_config.TextColumn("類別", width="small", disabled=True),
                "課程名稱": st.column_config.TextColumn("課程名稱", width="medium", disabled=True),
                "適用班級": st.column_config.TextColumn("適用班級", width="medium", disabled=True), 
                
                "教科書(優先1)": st.column_config.TextColumn("教科書(1)", width="medium", disabled=True), 
                "冊次(1)": st.column_config.TextColumn("冊次(1)", width="small", disabled=True), 
                "出版社(1)": st.column_config.TextColumn("出版社(1)", width="small", disabled=True),
                "審定字號(1)": st.column_config.TextColumn("字號(1)", width="small", disabled=True),
                "備註1": st.column_config.TextColumn("備註(1)", width="small", disabled=True), 
                
                "教科書(優先2)": st.column_config.TextColumn("教科書(2)", width="medium", disabled=True),
                "冊次(2)": st.column_config.TextColumn("冊次(2)", width="small", disabled=True), 
                "出版社(2)": st.column_config.TextColumn("出版社(2)", width="small", disabled=True),
                "審定字號(2)": st.column_config.TextColumn("字號(2)", width="small", disabled=True),
                "備註2": st.column_config.TextColumn("備註(2)", width="small", disabled=True), 
            },
            # 調整欄位順序以符合要求：審定字號和備註與對應的冊次/出版社放在一起
            column_order=[
                "勾選", "課程類別", "課程名稱", "適用班級",
                "教科書(優先1)", "冊次(1)", "審定字號(1)", "出版社(1)", "備註1", 
                "教科書(優先2)", "冊次(2)", "審定字號(2)", "出版社(2)", "備註2" 
            ]
        )

        col_submit, _ = st.columns([1, 4])
        with col_submit:
            # --- 核心修改區域：呼叫 PDF 生成函式，並提供下載連結 ---
            if st.button("📄 轉 PDF 報表 (下載)", type="primary", use_container_width=True):
                with st.spinner(f"正在抓取 {dept} 所有資料並產生 PDF 報表..."):
                    pdf_report_bytes = create_pdf_report(dept)
                    
                    if pdf_report_bytes is not None:
                        # base64.b64encode 接受 bytes，回傳 bytes
                        b64_bytes = base64.b64encode(pdf_report_bytes)
                        # 將 base64 bytes 解碼為字串，用於 HTML a 標籤
                        b64 = b64_bytes.decode('latin-1') 
                        
                        # 提供 PDF 下載連結
                        href = f'<a href="data:application/pdf;base64,{b64}" download="{dept}_教科書總表.pdf" style="text-decoration:none; color:white; background-color:#b31412; padding:10px 20px; border-radius:5px; font-weight:bold;">⬇️ 點此下載完整 PDF 報表 (含上下學期/各年級)</a>'
                        st.markdown(href, unsafe_allow_html=True)
                        st.success("✅ PDF 報表已生成！")
                    else:
                        st.error("❌ PDF 報表生成失敗，請檢查資料或連線設定。**（若中文亂碼，請依 NOTE 註冊中文字體）**")
            # --- 核心修改結束 ---

    else:
        st.info("👈 請先在左側選擇科別")

if __name__ == "__main__":
    main()








