"""Email channel for N184 controller.

Polls an IMAP inbox on a heartbeat interval for new messages.
Sends outbound messages via SMTP.

Implements the Channel protocol (see channel.py).

Requires:
  EMAIL_IMAP_HOST — IMAP server hostname (e.g. imap.gmail.com)
  EMAIL_IMAP_USER — IMAP username / email address
  EMAIL_IMAP_PASS — IMAP password or app password
  EMAIL_SMTP_HOST — SMTP server hostname (e.g. smtp.gmail.com)
  EMAIL_SMTP_PORT — SMTP port (default: 587)
  EMAIL_SMTP_USER — SMTP username (defaults to IMAP user)
  EMAIL_SMTP_PASS — SMTP password (defaults to IMAP pass)
  EMAIL_POLL_INTERVAL — Seconds between IMAP checks (default: 60)
  EMAIL_FOLDER — IMAP folder to watch (default: INBOX)
"""

from __future__ import annotations

import asyncio
import email
import email.utils
import imaplib
import logging
import os
import smtplib
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis_bridge import RedisBridge

logger = logging.getLogger(__name__)


class EmailChannel:
    """Email channel — polls IMAP, sends via SMTP."""

    prefix = "email:"

    def __init__(
        self,
        imap_host: str,
        imap_user: str,
        imap_pass: str,
        smtp_host: str,
        smtp_port: int = 587,
        smtp_user: str | None = None,
        smtp_pass: str | None = None,
        poll_interval: int = 60,
        folder: str = "INBOX",
        redis_bridge: RedisBridge | None = None,
        assistant_name: str = "Honoré",
    ) -> None:
        self.imap_host = imap_host
        self.imap_user = imap_user
        self.imap_pass = imap_pass
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user or imap_user
        self.smtp_pass = smtp_pass or imap_pass
        self.poll_interval = poll_interval
        self.folder = folder
        self.redis_bridge = redis_bridge
        self.assistant_name = assistant_name
        self._running = False
        self._task: asyncio.Task | None = None
        # Track last seen UID to avoid reprocessing
        self._last_uid: int = 0

    async def start(self) -> None:
        self._running = True
        # Get the highest existing UID so we only process new messages
        self._last_uid = await asyncio.to_thread(self._get_latest_uid)
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "Email channel started (IMAP: %s, poll: %ds, folder: %s, last_uid: %d)",
            self.imap_host,
            self.poll_interval,
            self.folder,
            self._last_uid,
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def send_message(
        self, chat_jid: str, text: str, sender: str | None = None
    ) -> None:
        """Send an email reply via SMTP.

        chat_jid format: email:recipient@example.com
        """
        recipient = chat_jid.removeprefix("email:")
        if not recipient or "@" not in recipient:
            logger.warning("Invalid email JID: %s", chat_jid)
            return

        subject = f"[N184] {sender or self.assistant_name}"

        await asyncio.to_thread(
            self._send_smtp, recipient, subject, text
        )

    # ── IMAP Polling ──────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Heartbeat loop — checks IMAP every poll_interval seconds."""
        while self._running:
            try:
                new_messages = await asyncio.to_thread(self._fetch_new)
                for msg in new_messages:
                    from_addr = msg["from"]
                    subject = msg["subject"]
                    body = msg["body"]
                    chat_jid = f"email:{from_addr}"

                    logger.info(
                        "Email from %s: %s (%d chars)",
                        from_addr,
                        subject,
                        len(body),
                    )

                    if self.redis_bridge:
                        await self.redis_bridge.publish_to_agent(
                            "honore",
                            {
                                "type": "message",
                                "text": f"[Email from {from_addr}]\nSubject: {subject}\n\n{body}",
                                "sender": from_addr,
                                "sender_name": from_addr,
                                "chat_jid": chat_jid,
                                "timestamp": msg.get("date", ""),
                            },
                        )
            except Exception:
                logger.exception("Error polling IMAP")

            await asyncio.sleep(self.poll_interval)

    def _get_latest_uid(self) -> int:
        """Connect to IMAP and get the highest UID in the folder."""
        try:
            imap = imaplib.IMAP4_SSL(self.imap_host)
            imap.login(self.imap_user, self.imap_pass)
            imap.select(self.folder, readonly=True)
            _, data = imap.search(None, "ALL")
            uids = data[0].split()
            imap.logout()
            return int(uids[-1]) if uids else 0
        except Exception:
            logger.exception("Failed to get latest IMAP UID")
            return 0

    def _fetch_new(self) -> list[dict]:
        """Fetch messages with UID greater than last seen."""
        messages: list[dict] = []
        try:
            imap = imaplib.IMAP4_SSL(self.imap_host)
            imap.login(self.imap_user, self.imap_pass)
            imap.select(self.folder, readonly=True)

            # Search for messages newer than last UID
            search_criteria = f"UID {self._last_uid + 1}:*"
            _, data = imap.uid("search", None, search_criteria)
            uids = data[0].split()

            for uid_bytes in uids:
                uid = int(uid_bytes)
                if uid <= self._last_uid:
                    continue

                _, msg_data = imap.uid("fetch", uid_bytes, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue

                raw = msg_data[0][1]
                if isinstance(raw, bytes):
                    msg = email.message_from_bytes(raw)
                else:
                    continue

                from_addr = _extract_email(msg.get("From", ""))
                subject = msg.get("Subject", "(no subject)")
                date = msg.get("Date", "")
                body = _extract_body(msg)

                messages.append({
                    "uid": uid,
                    "from": from_addr,
                    "subject": subject,
                    "date": date,
                    "body": body,
                })

                self._last_uid = max(self._last_uid, uid)

            imap.logout()
        except Exception:
            logger.exception("IMAP fetch error")

        return messages

    # ── SMTP ──────────────────────────────────────────────────────────

    def _send_smtp(self, recipient: str, subject: str, body: str) -> None:
        """Send an email via SMTP."""
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["From"] = self.smtp_user
            msg["To"] = recipient
            msg["Subject"] = subject

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_user, self.smtp_pass)
                server.send_message(msg)

            logger.info("Email sent to %s: %s", recipient, subject)
        except Exception:
            logger.exception("SMTP send error to %s", recipient)


def _extract_email(header: str) -> str:
    """Extract bare email address from a From header."""
    _, addr = email.utils.parseaddr(header)
    return addr or header


def _extract_body(msg: email.message.Message) -> str:
    """Extract the plain text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback: try HTML
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""
