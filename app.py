
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
import pandas as pd
import streamlit as st
from itertools import cycle
import requests
import os
import base64
from datetime import datetime
import traceback
import json

# Google Sheets
import gspread
from gspread_dataframe import set_with_dataframe, get_as_dataframe

# ---------- Load environment variables ----------
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=env_path)
else:
    secrets_env_vars = ["GOOGLE_SHEET_ID", "EVO_BASE_URL", "EVO_INSTANCE_NAME", "AUTHENTICATION_API_KEY"]
    for var in secrets_env_vars:
        if var in st.secrets:
            os.environ[var] = st.secrets[var]

# ---------- Setup Directories ----------
BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

receivers_path = os.path.join(DATA_DIR, "emails.csv")
senders_path = os.path.join(DATA_DIR, "senders-emails.csv")
service_account_file = os.path.join(BASE_DIR, "service_account.json")
google_sheet_id = os.getenv("GOOGLE_SHEET_ID")

# ---------- Evolution API ----------
class EvolutionAPI:
    BASE_URL = os.getenv("EVO_BASE_URL")
    INSTANCE_NAME = os.getenv("EVO_INSTANCE_NAME")

    def __init__(self):
        self.__api_key = os.getenv("AUTHENTICATION_API_KEY")
        self.__headers = {'apikey': self.__api_key, 'Content-Type': 'application/json'}

    def send_message(self, number, text):
        payload = {'number': number, 'options': {'delay': 3000}, 'linkPreview': True, 'text': text}
        response = requests.post(f'{self.BASE_URL}/message/sendText/{self.INSTANCE_NAME}', headers=self.__headers, json=payload)
        return response.json()

    def send_media(self, number, media_type="image", file_name="file.jpg", caption="", file_path=None, file_bytes=None):
        if not file_path and not file_bytes:
            raise ValueError("Either file_path or file_bytes must be provided.")
        if file_bytes:
            encoded_file = base64.b64encode(file_bytes).decode('utf-8')
        elif file_path:
            if not os.path.exists(file_path):
                raise FileNotFoundError(f"{file_path} not found.")
            with open(file_path, "rb") as f:
                encoded_file = base64.b64encode(f.read()).decode('utf-8')
        payload = {"number": number, "mediatype": media_type, "fileName": file_name, "caption": caption, "media": encoded_file}
        response = requests.post(f'{self.BASE_URL}/message/sendMedia/{self.INSTANCE_NAME}', headers=self.__headers, json=payload)
        return response.json()

# ---------- Google Sheets Logging ----------
def log_message_gsheet(sheet_id, log_data, service_account_file):
    try:
        if os.path.exists(service_account_file):
            gc = gspread.service_account(filename=service_account_file)
        elif "SERVICE_ACCOUNT_JSON" in st.secrets:
            info = json.loads(st.secrets["SERVICE_ACCOUNT_JSON"])
            gc = gspread.service_account_from_dict(info)
        else:
            raise FileNotFoundError("Service account file not found and SERVICE_ACCOUNT_JSON secret not set.")

        if not sheet_id:
            raise ValueError("GOOGLE_SHEET_ID is empty or not set in .env or secrets.")

        sh = gc.open_by_key(sheet_id)
        worksheet = sh.sheet1

        log_data["Message Content"] = str(log_data["Message Content"]).replace('\n', ' ').replace('\r', ' ')
        log_df = pd.DataFrame([log_data])

        try:
            existing_df = get_as_dataframe(worksheet, evaluate_formulas=True)
            existing_df = existing_df.dropna(how='all')
        except Exception:
            existing_df = pd.DataFrame()

        combined_df = pd.concat([existing_df, log_df], ignore_index=True)
        set_with_dataframe(worksheet, combined_df)

    except Exception as e:
        st.error(f"Google Sheets Logging Error: {e}")
        st.text(traceback.format_exc())
        raise

# ---------- Streamlit App ----------
st.set_page_config(page_title="Messaging App", layout="centered")
st.title("Badir-wa-Sahm Messaging App")
# ---------- Custom Styling ----------
# Load CSV files
try:
    receivers_df = pd.read_csv(receivers_path)
    senders_df = pd.read_csv(senders_path)
except Exception as e:
    st.error(f"Error loading CSV files: {e}")
    st.stop()

# ---------- Method Selection ----------
method = st.selectbox("Choose Sending Method", ["Email", "WhatsApp"])

if method == "Email":
    st.subheader("Email Settings")
    subject = st.text_input("Email Subject", "Test Email")
    body_template = st.text_area("Email Body", "Hello {name},\nThis is a test email.")
    attachment_path = st.file_uploader("Attach file (optional)", type=["pdf","jpg","png","mp3"])
else:
    st.subheader("WhatsApp Settings")
    body_template = st.text_area("WhatsApp Message", "Hi {name}, this is a test message!")
    media_file = st.file_uploader("Upload media file (optional)", type=["jpg","png","pdf","mp3"])
    media_type = st.radio(
    "Choose Media Type",
    options=["image", "document", "audio"],
    index=0,
    horizontal=True 
)  
# ---------- Department filter ----------
if "dept" in receivers_df.columns:
    departments = sorted(receivers_df["dept"].dropna().unique())
    selected_depts = st.multiselect(
        "Choose Department(s)",
        options=departments,
        default=[],  # فارغ عند البداية
        help="Type to search or click the arrow to see all departments"
    )
    filtered_df = receivers_df if not selected_depts else receivers_df[receivers_df["dept"].isin(selected_depts)]
else:
    filtered_df = receivers_df

# ---------- Send messages ----------
st.markdown("---")


if st.button(f"Send {method} Messages"):
    total = len(filtered_df)
    sent_count = 0
    senders_cycle = cycle(senders_df.to_dict(orient="records"))
    api = EvolutionAPI()

    # عداد الرسائل الفوري
    message_counter = st.empty()
    message_counter.markdown(f"Sending {sent_count}/{total}")

    for _, row in filtered_df.iterrows():
        name = row["name"]
        dept = row.get("dept", "N/A")
        message = body_template.format(name=name)
        start_time = datetime.now()
        status = "Success"

        try:
            if method == "Email":
                sender_data = next(senders_cycle)
                sender_email = sender_data.get("email")
                app_password = sender_data.get("app_password")
                receiver = row["email"]

                msg = MIMEMultipart()
                msg['From'] = sender_email
                msg['To'] = receiver
                msg['Subject'] = subject
                msg.attach(MIMEText(message, 'plain'))

                if attachment_path:
                    part = MIMEBase('application', 'octet-stream')
                    part.set_payload(attachment_path.read())
                    encoders.encode_base64(part)
                    part.add_header('Content-Disposition', f'attachment; filename={attachment_path.name}')
                    msg.attach(part)

                server = smtplib.SMTP("smtp.gmail.com", 587)
                server.starttls()
                server.login(sender_email, app_password)
                server.send_message(msg)
                server.quit()

            else:
                number = str(row["number"])
                if media_file:
                    api.send_media(
                        number=number,
                        media_type=media_type,
                        file_name=media_file.name,
                        caption=message,
                        file_bytes=media_file.read()
                    )
                else:
                    api.send_message(number=number, text=message)

            sent_count += 1

        except Exception as e:
            status = f"Failed: {e}"

        duration = (datetime.now() - start_time).total_seconds()

        # Log message
        log_data = {
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Method": method,
            "Sender": sender_email if method == "Email" else os.getenv("EVO_INSTANCE_NAME"),
            "Receiver": receiver if method == "Email" else row.get("number", "N/A"),
            "Department": dept,
            "Duration (s)": duration,
            "Message Content": message,
            "Status": status
        }

        try:
            log_message_gsheet(google_sheet_id, log_data, service_account_file)
        except Exception as e:
            st.warning(f"Failed to log message: {e}")

        
        message_counter.markdown(f"Sending {sent_count}/{total}")

    st.success(f"All done! {sent_count}/{total} messages processed.")
