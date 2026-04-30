import streamlit as st
import imaplib
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
import os
from dotenv import load_dotenv

def decode_str(s):
    if s is None:
        return ""
    parts = decode_header(s)
    result = ""
    for part, enc in parts:
        if isinstance(part, bytes):
            result += part.decode(enc or "utf-8", errors="replace")
        else:
            result += part
    return result

def get_imap(account):
    cfg = st.secrets["email"]
    pwd = st.secrets["email_passwords"]
    if account == "shop@propertya.co.jp":
        M = imaplib.IMAP4_SSL(cfg["onamae_imap_host"], 993)
        M.login(cfg["onamae_address"], pwd["onamae_password"])
    else:
        M = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        M.login(cfg["gmail_address"], pwd["gmail_app_password"])
    return M

def fetch_emails(account, limit=100):
    try:
        M = get_imap(account)
        M.select("INBOX")
        _, data = M.search(None, "ALL")
        ids = data[0].split()[-limit:]
        mails = []
        for mid in reversed(ids):
            _, msg_data = M.fetch(mid, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1])
            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        break
            else:
                body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
            mails.append({"uid": mid.decode(), "from": decode_str(msg["From"]), "subject": decode_str(msg["Subject"]), "date": decode_str(msg["Date"]), "body": body[:1000]})
        M.logout()
        return mails
    except Exception as e:
        st.error(f"Error: {e}")
        return []

def delete_email(account, uid):
    try:
        M = get_imap(account)
        M.select("INBOX")
        M.store(uid.encode(), "+FLAGS", "\\Deleted")
        M.expunge()
        M.logout()
        return True
    except Exception as e:
        st.error(f"Error: {e}")
        return False

def send_email(account, to, subject, body):
    cfg = st.secrets["email"]
    pwd = st.secrets["email_passwords"]
    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["To"] = to
        if account == "shop@propertya.co.jp":
            msg["From"] = cfg["onamae_address"]
            msg.attach(MIMEText(body, "plain", "utf-8"))
            with smtplib.SMTP_SSL(cfg["onamae_smtp_host"], 465) as s:
                s.login(cfg["onamae_address"], pwd["onamae_password"])
                s.send_message(msg)
        else:
            msg["From"] = cfg["gmail_address"]
            msg.attach(MIMEText(body, "plain", "utf-8"))
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                s.login(cfg["gmail_address"], pwd["gmail_app_password"])
                s.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)

def translate(text, to_lang):
    load_dotenv()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return "(APIキー未設定)"
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)
    if to_lang == "ja":
        prompt = f"以下のテキストを日本語に翻訳してください。翻訳のみ出力してください。\n\n{text}"
    else:
        prompt = f"以下のテキストを英語に翻訳してください。翻訳のみ出力してください。\n\n{text}"
    res = client.messages.create(model="claude-sonnet-4-20250514", max_tokens=1000, messages=[{"role": "user", "content": prompt}])
    return res.content[0].text

def show_mail_page():
    st.title("\U0001f4e7 メール管理")
    account = st.selectbox("アカウント選択", ["shop@propertya.co.jp", "propertya.kato@gmail.com"])
    tab1, tab2 = st.tabs(["\U0001f4e5 受信", "\u2709\ufe0f 新規作成"])

    with tab1:
        col1, col2 = st.columns([1, 3])
        with col1:
            per_page = st.selectbox("表示件数", [10, 20, 50], index=1)
        with col2:
            if st.button("\U0001f504 メール取得", type="primary"):
                with st.spinner("取得中..."):
                    st.session_state["mails"] = fetch_emails(account)
                    st.session_state["mail_page"] = 0
                    for k in [k for k in st.session_state if k.startswith("reply_") or k.startswith("body_ja_")]:
                        del st.session_state[k]

        mails = st.session_state.get("mails", [])
        if not mails:
            st.info("「メール取得」ボタンを押してください")
        else:
            total = len(mails)
            page = st.session_state.get("mail_page", 0)
            total_pages = (total - 1) // per_page + 1
            start = page * per_page
            end = min(start + per_page, total)
            st.caption(f"{total}件 | {start+1}〜{end}件表示")

            for i, m in enumerate(mails[start:end], start=start):
                with st.expander(f"\U0001f4e8 {m['subject'][:45]} — {m['from'][:30]}"):
                    st.caption(m["date"])
                    st.text_area("本文", m["body"], height=150, key=f"body_{i}", disabled=True)

                    col_read, col_arc, col_del = st.columns([8, 2.5, 1.5])
                    with col_read:
                        if st.button("\U0001f4d6 本文を日本語で読む", key=f"trans_body_{i}"):
                            with st.spinner("翻訳中..."):
                                st.session_state[f"body_ja_{i}"] = translate(m["body"], "ja")
                    with col_arc:
                        if st.button("\U0001f4e6 アーカイブ", key=f"arc_{i}"):
                            if archive_email(account, m["uid"]):
                                st.session_state["mails"].pop(i)
                                st.rerun()
                    with col_del:
                        if st.button("\U0001f5d1\ufe0f 削除", key=f"del_{i}"):
                            if delete_email(account, m["uid"]):
                                st.session_state["mails"].pop(i)
                                st.rerun()

                    if f"body_ja_{i}" in st.session_state:
                        st.info(st.session_state[f"body_ja_{i}"])

                    st.divider()
                    ja_input = st.text_area("日本語で返信を書く", key=f"ja_input_{i}", height=100, placeholder="日本語で入力→英語に翻訳して返信欄へ")
                    if st.button("\U0001f504 英語に翻訳して返信欄へ", key=f"trans_{i}") and ja_input:
                        with st.spinner("翻訳中..."):
                            st.session_state[f"reply_{i}"] = translate(ja_input, "en")

                    if f"reply_{i}" in st.session_state:
                        reply = st.text_area("返信文（編集可）", st.session_state[f"reply_{i}"], key=f"edit_{i}", height=150)
                        if st.button("\U0001f4e4 送信", key=f"send_{i}", type="primary"):
                            ok, err = send_email(account, m["from"], f"Re: {m['subject']}", reply)
                            if ok:
                                st.success("\u2705 送信完了")
                            else:
                                st.error(f"\u274c {err}")

            st.divider()
            cp, ci, cn = st.columns([1, 2, 1])
            with cp:
                if st.button("\u25c0 前へ", disabled=(page == 0)):
                    st.session_state["mail_page"] = page - 1
                    st.rerun()
            with ci:
                st.markdown(f"<center>{page+1} / {total_pages}</center>", unsafe_allow_html=True)
            with cn:
                if st.button("次へ \u25b6", disabled=(page >= total_pages - 1)):
                    st.session_state["mail_page"] = page + 1
                    st.rerun()

    with tab2:
        to = st.text_input("宛先")
        subject = st.text_input("件名")
        ja_body = st.text_area("本文（日本語で入力）", height=150)
        if st.button("\U0001f504 英語に翻訳"):
            if ja_body:
                with st.spinner("翻訳中..."):
                    st.session_state["new_en"] = translate(ja_body, "en")
        if "new_en" in st.session_state:
            en_body = st.text_area("英語本文（編集可）", st.session_state["new_en"], height=150)
            if st.button("\U0001f4e4 送信", type="primary") and to and subject:
                ok, err = send_email(account, to, subject, en_body)
                if ok:
                    st.success("\u2705 送信完了")
                    del st.session_state["new_en"]
                else:
                    st.error(f"\u274c {err}")
