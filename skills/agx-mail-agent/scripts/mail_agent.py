#!/usr/bin/env python3
"""
Mail Agent - multi-mailbox send/receive tool
Supports SMTP sending (with attachments) + POP3/IMAP receiving (with attachment download)
Configuration lives in mail_config.json; a mail_config argument may also be passed ad hoc
"""

import email
import email.encoders
import imaplib
import json
import os
import poplib
import smtplib
import sys
from datetime import datetime, timedelta
from email.header import decode_header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mail_config.json")


def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {"accounts": {}}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def _resolve_account(account_name, inline_config=None):
    """Resolve the account config: prefer the ad hoc config passed via inline_config, otherwise read the local config"""
    if inline_config:
        return inline_config
    config = load_config()
    acct = config.get("accounts", {}).get(account_name)
    if acct:
        return acct
    return None


# ========== Send mail ==========


def send_mail(
    account_name,
    to,
    subject,
    body,
    cc=None,
    bcc=None,
    is_html=False,
    mail_config=None,
    attachments=None,
    inline_images=None,
):
    """Send an email

        Two modes are supported:
        1. Use a preset account: send(account="robertfeng", to="...", ...)
        2. Pass a config ad hoc: send(account="temp", to="...", ..., mail_config={...})

        attachments: list of attachment file paths, e.g. ["/path/to/photo.jpg", "./doc.pdf"]
        inline_images: list of image file paths embedded in the message body, e.g. ["/path/to/photo.jpg"]
                      when used, body must reference them e.g. <img src="cid:image0">
    """
    if body is None:
        body = ""

    acct = _resolve_account(account_name, mail_config)
    if not acct:
        return {"success": False, "error": f"账号 '{account_name}' 未配置，且未提供 mail_config 参数"}
    smtp_host = acct.get("smtp_host")
    smtp_port = acct.get("smtp_port", 465)
    smtp_user = acct.get("smtp_user", acct["email"])
    smtp_pass = acct.get("smtp_pass")

    if not smtp_host:
        return {"success": False, "error": "SMTP 服务器未配置"}

    # Build the message
    msg_from = f"{acct.get('name', '')} <{acct['email']}>"

    if attachments:
        msg = MIMEMultipart("mixed")
        msg_body = MIMEMultipart("alternative")
        msg_body["From"] = msg_from
        msg_body["To"] = to
        msg_body["Subject"] = subject
        if cc:
            msg_body["Cc"] = cc
        if bcc:
            msg_body["Bcc"] = bcc
        if is_html:
            msg_body.attach(MIMEText(body, "html", "utf-8"))
        else:
            msg_body.attach(MIMEText(body, "plain", "utf-8"))
        msg.attach(msg_body)
    else:
        msg = MIMEMultipart("alternative")
        if is_html:
            msg.attach(MIMEText(body, "html", "utf-8"))
        else:
            msg.attach(MIMEText(body, "plain", "utf-8"))

    msg["From"] = msg_from
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc

    # Add attachments
    attached_files = []
    if attachments:
        for filepath in attachments:
            filepath = os.path.expanduser(filepath)
            if not os.path.isfile(filepath):
                return {"success": False, "error": f"附件文件不存在: {filepath}"}
            filename = os.path.basename(filepath)
            try:
                with open(filepath, "rb") as f:
                    raw_data = f.read()
            except Exception as e:
                return {"success": False, "error": f"读取附件 {filepath} 失败: {e}"}
            # Determine the MIME type from the file extension
            _, ext = os.path.splitext(filepath)
            ext = ext.lower().lstrip(".")
            content_type_map = {
                "jpg": ("image", "jpeg"),
                "jpeg": ("image", "jpeg"),
                "png": ("image", "png"),
                "gif": ("image", "gif"),
                "webp": ("image", "webp"),
                "bmp": ("image", "bmp"),
                "pdf": ("application", "pdf"),
                "zip": ("application", "zip"),
                "rar": ("application", "x-rar-compressed"),
                "doc": ("application", "msword"),
                "docx": ("application", "vnd.openxmlformats-officedocument.wordprocessingml.document"),
                "xls": ("application", "vnd.ms-excel"),
                "xlsx": ("application", "vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            }
            maintype, subtype = content_type_map.get(ext, ("application", "octet-stream"))
            try:
                part = MIMEBase(maintype, subtype)
                part.set_payload(raw_data)
                email.encoders.encode_base64(part)
                # RFC 2047 encode non-ASCII file names
                encoded_filename = email.header.Header(filename, "utf-8").encode()
                part.add_header("Content-Disposition", "attachment", filename=encoded_filename)
                msg.attach(part)
                attached_files.append(filename)
            except Exception as e:
                return {"success": False, "error": f"打包附件 {filepath} 失败: {e}"}

    # Embed inline images
    inline_attached = []
    if inline_images:
        body_parts = getattr(msg, "_body_parts", None)
        img_counter = 0
        for img_path in inline_images:
            img_path = os.path.expanduser(img_path)
            if not os.path.isfile(img_path):
                return {"success": False, "error": f"图片文件不存在: {img_path}"}
            _, ext = os.path.splitext(img_path)
            ext = ext.lower().lstrip(".")
            if ext in ("jpg", "jpeg"):
                maintype, subtype = "image", "jpeg"
            elif ext == "png":
                maintype, subtype = "image", "png"
            elif ext == "gif":
                maintype, subtype = "image", "gif"
            elif ext == "webp":
                maintype, subtype = "image", "webp"
            else:
                maintype, subtype = "image", ext if ext else "jpeg"
            try:
                with open(img_path, "rb") as f:
                    img_data = f.read()
                img_part = MIMEBase(maintype, subtype)
                img_part.set_payload(img_data)
                email.encoders.encode_base64(img_part)
                cid = f"image{img_counter}"
                encoded_fn = email.header.Header(os.path.basename(img_path), "utf-8").encode()
                img_part.add_header("Content-Disposition", "inline", filename=encoded_fn)
                img_part.add_header("Content-ID", f"<{cid}>")
                msg.attach(img_part)
                inline_attached.append({"path": img_path, "cid": cid})
                img_counter += 1
            except Exception as e:
                return {"success": False, "error": f"读取图片 {img_path} 失败: {e}"}

    try:
        if smtp_port in [25, 587]:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port)

        server.login(smtp_user, smtp_pass)
        recipients = [to]
        if cc:
            recipients += [addr.strip() for addr in cc.split(",")]
        if bcc:
            recipients += [addr.strip() for addr in bcc.split(",")]
        server.sendmail(acct["email"], recipients, msg.as_string())
        server.quit()
        extra = []
        if attached_files:
            extra.append(f"附件: {', '.join(attached_files)}")
        if inline_attached:
            extra.append(f"内嵌图片: {', '.join(i['cid'] for i in inline_attached)}")
        msg_text = f"邮件已发送到 {to}"
        if extra:
            msg_text += f"（{'；'.join(extra)}）"
        return {"success": True, "message": msg_text}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ========== Receive mail ==========


def decode_mime_header(header_value):
    """Decode message headers and the like"""
    if header_value is None:
        return ""
    decoded_parts = decode_header(header_value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            try:
                result.append(part.decode(charset or "utf-8", errors="replace"))
            except:
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(str(part))
    return "".join(result)


def get_email_body(msg):
    """Extract the message body"""
    body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    body = payload.decode(charset, errors="replace")
                except:
                    pass
            elif ct == "text/html":
                try:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset() or "utf-8"
                    html_body = payload.decode(charset, errors="replace")
                except:
                    pass
        return body or html_body or "（无正文）"
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
        except:
            return str(msg.get_payload())


def list_attachments(msg, include_data=False):
    """List the message attachment information"""
    attachments = []
    if msg.is_multipart():
        idx = 0
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                filename = decode_mime_header(part.get_filename())
                data = part.get_payload(decode=True) or b""
                result = {
                    "id": idx,
                    "filename": filename or "unnamed",
                    "size": len(data),
                    "content_type": part.get_content_type(),
                }
                if include_data:
                    result["data_bytes"] = len(data)
                attachments.append(result)
                idx += 1
    return attachments


def save_attachment_from_msg(msg, attachment_index, save_dir):
    """Save the attachment at the given index from a message object into a directory"""
    idx = 0
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_disposition() == "attachment":
                if idx == attachment_index:
                    filename = decode_mime_header(part.get_filename()) or f"attachment_{idx}"
                    data = part.get_payload(decode=True)
                    if data is None:
                        return {"success": False, "error": "无法读取附件数据"}
                    save_path = os.path.join(save_dir, filename)
                    with open(save_path, "wb") as f:
                        f.write(data)
                    return {"success": True, "path": save_path, "filename": filename, "size": len(data)}
                idx += 1
    return {"success": False, "error": f"未找到索引为 {attachment_index} 的附件"}


# ===== Inbox listing =====


def fetch_inbox(account_name, limit=10, folder="INBOX", search_criteria=None, since_days=None, mail_config=None):
    """Fetch the inbox message list (POP3 first, IMAP as fallback)
        Only summary fields are returned (subject, sender, date), never the body.
        To read a body or download attachments, use view_email() or save_attachments().
    """
    acct = _resolve_account(account_name, mail_config)
    if not acct:
        return {"success": False, "error": f"账号 '{account_name}' 未配置，且未提供 mail_config 参数"}

    imap_host = acct.get("imap_host")
    if imap_host:
        result = _fetch_via_imap(acct, account_name, limit, folder, search_criteria, since_days)
        if result.get("success"):
            return result

    pop_host = acct.get("pop_host")
    if pop_host:
        return _fetch_via_pop3(acct, account_name, limit, search_criteria, since_days)

    return {"success": False, "error": "未配置 IMAP 或 POP3 服务器"}


def _fetch_via_imap(acct, account_name, limit, folder, search_criteria, since_days):
    try:
        mail = imaplib.IMAP4_SSL(acct["imap_host"], acct.get("imap_port", 993))
        status, login_result = mail.login(acct.get("imap_user", acct["email"]), acct.get("imap_pass"))
        if status != "OK":
            return {"success": False, "error": f"IMAP 登录失败: {login_result}"}

        status, select_result = mail.select(folder)
        if status != "OK":
            mail.logout()
            return {"success": False, "error": f"IMAP 选择文件夹失败: {select_result}"}

        if search_criteria:
            search_str = search_criteria
        elif since_days:
            date_from = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
            search_str = f"(SINCE {date_from})"
        else:
            search_str = "ALL"

        status, messages = mail.search(None, search_str)
        if status != "OK":
            mail.logout()
            return {"success": False, "error": "搜索失败"}

        msg_ids = messages[0].split()
        msg_ids = msg_ids[-limit:] if len(msg_ids) > limit else msg_ids
        msg_ids.reverse()

        emails = []
        for mid in msg_ids:
            status, data = mail.fetch(mid, "(RFC822)")
            if status != "OK":
                continue
            raw_email = data[0][1]
            msg = email.message_from_bytes(raw_email)
            from_name, from_addr = parseaddr(msg.get("From", ""))
            to_name, to_addr = parseaddr(msg.get("To", ""))
            emails.append(
                {
                    "id": mid.decode(),
                    "from": decode_mime_header(from_name) or from_addr,
                    "from_addr": from_addr,
                    "to": decode_mime_header(to_name) or to_addr,
                    "subject": decode_mime_header(msg.get("Subject", "")),
                    "date": msg.get("Date", ""),
                    "body_preview": get_email_body(msg)[:100],
                    "attachments": list_attachments(msg),
                    "has_attachments": len(list_attachments(msg)) > 0,
                }
            )

        mail.logout()
        return {"success": True, "emails": emails, "total": len(messages[0].split())}
    except Exception as e:
        return {"success": False, "error": f"IMAP 异常: {e}"}


def _fetch_via_pop3(acct, account_name, limit, search_criteria, since_days):
    try:
        pop = poplib.POP3_SSL(acct["pop_host"], acct.get("pop_port", 995))
        pop.user(acct.get("pop_user", acct["email"]))
        pop.pass_(acct.get("pop_pass", acct.get("imap_pass", "")))

        count, size = pop.stat()

        start = max(1, count - limit + 1)
        emails = []
        for i in range(count, start - 1, -1):
            resp, lines, octets = pop.retr(i)
            raw = b"\r\n".join(lines)
            msg = email.message_from_bytes(raw)
            from_name, from_addr = parseaddr(msg.get("From", ""))
            to_name, to_addr = parseaddr(msg.get("To", ""))
            emails.append(
                {
                    "id": str(i),
                    "from": decode_mime_header(from_name) or from_addr,
                    "from_addr": from_addr,
                    "to": decode_mime_header(to_name) or to_addr,
                    "subject": decode_mime_header(msg.get("Subject", "")),
                    "date": msg.get("Date", ""),
                    "body_preview": get_email_body(msg)[:100],
                    "attachments": list_attachments(msg),
                    "has_attachments": len(list_attachments(msg)) > 0,
                }
            )

        pop.quit()
        return {"success": True, "emails": emails, "total": count}
    except Exception as e:
        return {"success": False, "error": f"POP3 异常: {e}"}


# ========== View message details ==========


def view_email(account_name, mail_id, text_only=True, mail_config=None):
    """View the full content of the given message (POP3 first)"""
    acct = _resolve_account(account_name, mail_config)
    if not acct:
        return {"success": False, "error": f"账号 '{account_name}' 未配置，且未提供 mail_config 参数"}
    if mail_id is None:
        return {"success": False, "error": "请指定邮件 id"}

    pop_host = acct.get("pop_host")
    if pop_host:
        return _view_via_pop3(acct, mail_id, text_only)
    imap_host = acct.get("imap_host")
    if imap_host:
        return _view_via_imap(acct, mail_id, text_only)
    return {"success": False, "error": "未配置 POP3 或 IMAP 服务器"}


def _view_via_pop3(acct, mail_id, text_only):
    try:
        pop = poplib.POP3_SSL(acct["pop_host"], acct.get("pop_port", 995))
        pop.user(acct.get("pop_user", acct["email"]))
        pop.pass_(acct.get("pop_pass", acct.get("imap_pass", "")))

        mail_id = int(mail_id)
        resp, lines, octets = pop.retr(mail_id)
        raw = b"\r\n".join(lines)
        msg = email.message_from_bytes(raw)

        from_name, from_addr = parseaddr(msg.get("From", ""))
        to_name, to_addr = parseaddr(msg.get("To", ""))
        cc_str = msg.get("Cc", "")
        body = get_email_body(msg)
        attachments = list_attachments(msg)

        pop.quit()
        return {
            "success": True,
            "mail": {
                "id": str(mail_id),
                "from": decode_mime_header(from_name) or from_addr,
                "from_addr": from_addr,
                "to": decode_mime_header(to_name) or to_addr,
                "to_addr": to_addr,
                "cc": cc_str,
                "subject": decode_mime_header(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
                "body": body,
                "attachments": attachments,
            },
        }
    except Exception as e:
        return {"success": False, "error": f"查看失败: {e}"}


def _view_via_imap(acct, mail_id, text_only):
    try:
        mail = imaplib.IMAP4_SSL(acct["imap_host"], acct.get("imap_port", 993))
        mail.login(acct.get("imap_user", acct["email"]), acct.get("imap_pass"))
        mail.select("INBOX")

        status, data = mail.fetch(mail_id.encode(), "(RFC822)")
        if status != "OK":
            mail.logout()
            return {"success": False, "error": "获取邮件失败"}

        msg = email.message_from_bytes(data[0][1])
        from_name, from_addr = parseaddr(msg.get("From", ""))
        to_name, to_addr = parseaddr(msg.get("To", ""))
        cc_str = msg.get("Cc", "")
        body = get_email_body(msg)
        attachments = list_attachments(msg)

        mail.logout()
        return {
            "success": True,
            "mail": {
                "id": str(mail_id),
                "from": decode_mime_header(from_name) or from_addr,
                "from_addr": from_addr,
                "to": decode_mime_header(to_name) or to_addr,
                "to_addr": to_addr,
                "cc": cc_str,
                "subject": decode_mime_header(msg.get("Subject", "")),
                "date": msg.get("Date", ""),
                "body": body,
                "attachments": attachments,
            },
        }
    except Exception as e:
        return {"success": False, "error": f"IMAP 查看失败: {e}"}


# ========== Download attachments ==========


def save_attachments(account_name, mail_id, save_dir=None, mail_config=None):
    """Download all attachments of the given message into a local directory"""
    acct = _resolve_account(account_name, mail_config)
    if not acct:
        return {"success": False, "error": f"账号 '{account_name}' 未配置，且未提供 mail_config 参数"}
    if mail_id is None:
        return {"success": False, "error": "请指定邮件 id"}

    if save_dir is None:
        save_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
    save_dir = os.path.expanduser(save_dir)
    os.makedirs(save_dir, exist_ok=True)

    pop_host = acct.get("pop_host")
    if pop_host:
        return _save_attachments_pop3(acct, mail_id, save_dir)
    imap_host = acct.get("imap_host")
    if imap_host:
        return _save_attachments_imap(acct, mail_id, save_dir)
    return {"success": False, "error": "未配置 POP3 或 IMAP 服务器"}


def _save_attachments_pop3(acct, mail_id, save_dir):
    try:
        pop = poplib.POP3_SSL(acct["pop_host"], acct.get("pop_port", 995))
        pop.user(acct.get("pop_user", acct["email"]))
        pop.pass_(acct.get("pop_pass", acct.get("imap_pass", "")))

        mail_id = int(mail_id)
        resp, lines, octets = pop.retr(mail_id)
        raw = b"\r\n".join(lines)
        msg = email.message_from_bytes(raw)
        pop.quit()

        saved = []
        idx = 0
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    filename = decode_mime_header(part.get_filename()) or f"attachment_{idx}"
                    data = part.get_payload(decode=True)
                    if data:
                        save_path = os.path.join(save_dir, filename)
                        # Avoid overwriting same-named files
                        base, ext = os.path.splitext(filename)
                        counter = 1
                        while os.path.exists(save_path):
                            save_path = os.path.join(save_dir, f"{base}_{counter}{ext}")
                            counter += 1
                        with open(save_path, "wb") as f:
                            f.write(data)
                        saved.append({"filename": filename, "path": save_path, "size": len(data)})
                    idx += 1

        return {"success": True, "saved": saved, "save_dir": save_dir}
    except Exception as e:
        return {"success": False, "error": f"下载附件失败: {e}"}


def _save_attachments_imap(acct, mail_id, save_dir):
    try:
        mail = imaplib.IMAP4_SSL(acct["imap_host"], acct.get("imap_port", 993))
        mail.login(acct.get("imap_user", acct["email"]), acct.get("imap_pass"))
        mail.select("INBOX")
        status, data = mail.fetch(str(mail_id).encode(), "(RFC822)")
        if status != "OK":
            mail.logout()
            return {"success": False, "error": "获取邮件失败"}
        msg = email.message_from_bytes(data[0][1])
        mail.logout()

        saved = []
        idx = 0
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_disposition() == "attachment":
                    filename = decode_mime_header(part.get_filename()) or f"attachment_{idx}"
                    data = part.get_payload(decode=True)
                    if data:
                        save_path = os.path.join(save_dir, filename)
                        base, ext = os.path.splitext(filename)
                        counter = 1
                        while os.path.exists(save_path):
                            save_path = os.path.join(save_dir, f"{base}_{counter}{ext}")
                            counter += 1
                        with open(save_path, "wb") as f:
                            f.write(data)
                        saved.append({"filename": filename, "path": save_path, "size": len(data)})
                    idx += 1

        return {"success": True, "saved": saved, "save_dir": save_dir}
    except Exception as e:
        return {"success": False, "error": f"IMAP 下载附件失败: {e}"}


# ========== Config management ==========


def add_account(
    account_name,
    email,
    smtp_host,
    smtp_port,
    smtp_user,
    smtp_pass,
    imap_host=None,
    imap_port=None,
    imap_user=None,
    imap_pass=None,
    name=None,
):
    config = load_config()
    if "accounts" not in config:
        config["accounts"] = {}

    acct = {
        "email": email,
        "name": name or account_name,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_user": smtp_user or email,
        "smtp_pass": smtp_pass,
    }

    if imap_host:
        acct["imap_host"] = imap_host
        acct["imap_port"] = imap_port or 993
        acct["imap_user"] = imap_user or email
        acct["imap_pass"] = imap_pass or smtp_pass

    config["accounts"][account_name] = acct
    save_config(config)
    return {"success": True, "message": f"账号 '{account_name}' 已添加"}


def remove_account(account_name):
    config = load_config()
    if account_name in config.get("accounts", {}):
        del config["accounts"][account_name]
        save_config(config)
        return {"success": True, "message": f"账号 '{account_name}' 已删除"}
    return {"success": False, "error": f"账号 '{account_name}' 不存在"}


def list_accounts():
    config = load_config()
    accounts = config.get("accounts", {})
    result = []
    for name, acct in accounts.items():
        result.append(
            {
                "name": name,
                "email": acct["email"],
                "smtp_host": acct.get("smtp_host", ""),
                "smtp_port": acct.get("smtp_port", 465),
                "pop_host": acct.get("pop_host", ""),
                "pop_port": acct.get("pop_port", 995),
                "imap_host": acct.get("imap_host", ""),
                "imap_port": acct.get("imap_port", 993),
            }
        )
    return {"success": True, "accounts": result}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "Usage: mail_agent.py <action> [params]"}, ensure_ascii=False))
        sys.exit(1)

    action = sys.argv[1]

    try:
        params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
        mail_config = params.get("mail_config")

        if action == "send":
            result = send_mail(
                params.get("account"),
                params.get("to"),
                params.get("subject"),
                params.get("body"),
                params.get("cc"),
                params.get("bcc"),
                params.get("is_html", False),
                mail_config,
                params.get("attachments"),  # List of file paths
                params.get("inline_images"),  # List of inline image paths
            )
        elif action == "inbox":
            result = fetch_inbox(
                params.get("account", ""),
                params.get("limit", 10),
                params.get("folder", "INBOX"),
                params.get("search"),
                params.get("since_days"),
                mail_config,
            )
        elif action == "add_account":
            result = add_account(
                params["name"],
                params["email"],
                params.get("smtp_host"),
                params.get("smtp_port", 465),
                params.get("smtp_user", params["email"]),
                params.get("smtp_pass"),
                params.get("imap_host"),
                params.get("imap_port", 993),
                params.get("imap_user", params["email"]),
                params.get("imap_pass", params.get("smtp_pass")),
                params.get("display_name"),
            )
        elif action == "remove_account":
            result = remove_account(params.get("name"))
        elif action == "view":
            result = view_email(params.get("account", ""), params.get("id"), params.get("text_only", True), mail_config)
        elif action == "save_attachments":
            result = save_attachments(params.get("account", ""), params.get("id"), params.get("save_dir"), mail_config)
        elif action == "list_accounts":
            result = list_accounts()
        else:
            result = {"success": False, "error": f"未知操作: {action}"}
    except Exception as e:
        result = {"success": False, "error": str(e)}

    print(json.dumps(result, ensure_ascii=False))
